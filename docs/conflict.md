# 文档与实现冲突清单

## 1. 文档定位

本文记录 2026-07-26 文档审计中发现的潜在实现问题。审计基线为当前工作树（基于提交 `0916275e85a7e3a95074fb8b7955d58886102247`）。这些条目与 [`ARCHITECTURE.md`](./ARCHITECTURE.md)、根目录 [`README.md`](../README.md) 或 [`AGENTS.md`](../AGENTS.md) 中的明确契约不一致，且从安全性、稳定契约或数据生命周期判断，更可能需要修改代码或补充产品决策，因此本次没有把权威文档改成当前实现行为。

条目编号沿用原始审计编号；已解决的条目按本文件规则直接删除，不再占用编号。各条目状态在标题中标注：`未解决` 表示冲突仍然存在；`部分解决` 表示条目中部分子项已修复或权威契约已收窄，剩余子项仍待处理。修复后应同步补充相应测试，并从本文件删除已经解决的条目（或把部分解决条目收敛为剩余子项）。

状态记录：

- 2026-07-26 初版：条目 2–11 全部为待处理。
- 后续复核（基于提交 `567692c`）：条目 6（工具边界错误丢失稳定原因标识）已解决并从本文件删除——三种工具失败（`max_tool_calls_exceeded`、`tool_invocation_timed_out`、`identical_tool_result_loop`）现为 `PermanentAgentOutputError` 子类，`openai_runtime.py` 的 `_wrapped_agent_failure` 会从 SDK 的 `UserError` 包装中恢复原始领域异常，`test_openai_runtime.py` 已覆盖包装后永久工具失败不重试且保留 `reason_code`；条目 5、7、8 为部分解决；其余条目未解决。

## 2. 非回环地址允许无鉴权监听（未解决）

**预期契约**

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) 规定本机无鉴权模式只能绑定 `127.0.0.1`，非回环地址必须具有明确的信任和访问边界。
- [`README.md`](../README.md) 明确说明当前无鉴权模式不支持直接开放到局域网或互联网。

**当前实现**

- [`settings.py`](../backend/src/codelens/bootstrap/settings.py#L56-L69) 校验 `auth=none` 时把 `0.0.0.0` 列入允许值（L60），且不要求 `repository_roots` 非空。
- [`test_settings.py`](../backend/tests/unit/bootstrap/test_settings.py#L27-L34) 把这一行为作为有效配置覆盖。

**影响**

`repository_roots` 只能限制仓库访问范围，不能替代 HTTP 身份认证或网络访问控制。服务可能在所有接口上暴露无鉴权的仓库浏览、Review 创建和 Settings 写入能力。

**后续处理方向**

确认首版是否应完全拒绝非回环绑定；若确实需要远程访问，先定义并实现明确的认证、授权和传输边界，再调整架构与 README。

## 3. 默认数据目录位于源码仓库内（未解决）

**预期契约**

- [`README.md`](../README.md) 声明 `CODELENS_DATA_DIR` 默认值为 `~/.local/share/codelens-review`，并说明网关配置默认保存在仓库之外。
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) 规定 Secret Store 默认位于源码仓库之外。

**当前实现**

- [`settings.py`](../backend/src/codelens/bootstrap/settings.py#L28-L47) 将默认 `data_dir` 解析为 `<project-root>/data`（L34-L35）。
- [`cli.py`](../backend/src/codelens/bootstrap/cli.py#L35) 使用 `Settings()` 产生 `--data-dir` 默认值，并把该值重新传入运行时设置（L72-L77、L108-L113），因此普通 CLI 启动同样落到仓库内的 `data/`。

**影响**

SQLite、Artifact、worktree 和 `secrets/model-gateways.json` 默认与源码仓库相邻。虽然当前 `.gitignore` 可以降低误提交概率，但这不满足 Secret Store 的默认隔离契约，也与 README 给出的运维路径不一致。

**后续处理方向**

确定跨平台应用数据目录策略，并让 Settings、CLI、启动脚本、测试和 README 共用同一默认值；同时保留显式 `--data-dir`/环境变量覆盖能力。

## 4. `model.log` 未要求操作者显式启用（未解决）

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 只允许在本地操作者明确启用后，把完整且已脱敏的模型交换写入 `logs/model.log`。

**当前实现**

- [`logging.py`](../backend/src/codelens/bootstrap/logging.py#L172-L192) 每次配置进程日志时都会创建并启用 `model.log` Handler。
- [`unified.py`](../backend/src/codelens/bootstrap/unified.py#L136-L140) 配置日志时未检查 opt-in；[`unified.py`](../backend/src/codelens/bootstrap/unified.py#L205) 始终向 `WorkerTranscriptStore` 注入 `ModelTranscriptLogWriter`。
- [`model_log.py`](../backend/src/codelens/review/infrastructure/model_log.py#L34-L39) 会在终态转录写入时记录 Prompt、原始模型输出和工具交互，没有检查 opt-in 设置。

**影响**

文件权限、轮转和脱敏边界已经存在，但包含源码和 Prompt 的完整交换仍会在默认情况下落盘，不符合显式同意要求，也可能超出操作者对“SDK 模型数据日志默认关闭”的理解。

**后续处理方向**

增加默认关闭的持久化设置或启动配置，并让 Handler 安装和 `ModelTranscriptLogWriter` 注入共同遵循该设置；补充启用、关闭和运行期切换测试。

## 5. 运行日志契约未完全落实（部分解决）

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 要求后端日志位于项目根目录 `logs/`，日志级别使用小写稳定值，Settings 契约明确返回默认级别和当前级别，并且 API、Worker 与 Supervisor 无需重启即可采用新级别。各日志还应结构化、独立限量轮转。

**当前实现**

已解决：

- 运行期日志级别热切换已落地：[`logging.py`](../backend/src/codelens/bootstrap/logging.py#L75-L112) 提供 `get_runtime_log_level`/`set_runtime_log_level`（持久化到 `data/logging.json`）与 `_RuntimeLevelFilter`（逐条刷新阈值），[`settings.py`](../backend/src/codelens/interface/http/routers/settings.py#L143-L166) 的 `GET/PUT /api/settings/logging` 与 `reset-all` 已接入；API、Worker 与 Supervisor 无需重启即可采用新级别。

仍未解决：

- [`logging.py`](../backend/src/codelens/bootstrap/logging.py#L38-L51) 仍直接输出 `record.levelname`，实际值为 `INFO`、`WARNING` 等大写文本，与契约的小写稳定值不符。
- [`dto.py`](../backend/src/codelens/interface/http/dto.py#L137-L138) 的 `RuntimeLogLevelResponse` 仍只有 `level`，没有分别表达默认级别和当前级别；默认级别只存在于服务端 `conf/web-settings-defaults.toml`，客户端无法区分。
- [`supervisor.py`](../backend/src/codelens/bootstrap/supervisor.py#L397-L398) 和 [`supervisor.py`](../backend/src/codelens/bootstrap/supervisor.py#L445-L446) 仍通过子进程 stdout 重定向写入 `supervisor.log`/`frontend.log`，不是结构化限量 Handler，也无法采用运行期日志级别设置。
- [`logging.py`](../backend/src/codelens/bootstrap/logging.py#L150) 未显式传入目录时仍使用 `Path.cwd()/logs`；Supervisor 启动时因 cwd 为项目根而实际落在项目 `logs/`，但手动从其他目录启动仍不保证是项目根目录。

**影响**

日志记录字段仍存在大小写差异；客户端无法区分默认与当前设置；Supervisor 日志仍可能无界增长且与后端动态级别行为不一致。

**后续处理方向**

- 规范化日志记录中的级别字段为小写稳定值，或重新收窄架构契约。
- 扩展 Settings DTO，分别返回默认级别与当前级别。
- 为 Supervisor 引入可轮转且能响应运行期设置的实现，或重新收窄架构契约。

## 7. 同轮完成工具与只读证据并发的终态确定性（部分解决）

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 要求 `task_done` 依据完整、确定的覆盖状态决定接受、打回或强制部分完成；一次模型响应中的多个只读工具允许有界并行，但同轮全部结果必须按原调用顺序一次性返回，状态或完成工具不得与只读证据调用组成并行批次。相同模型输出不应因协程调度顺序产生不同 Review 终态。

**当前实现**

已解决：

- 原 `review_file_done` 工具已在工具契约 v2 硬切中删除，已调查文件不再由模型显式声明，而是由 `read_file` 证据自动累计（[`snapshot_tools.py`](../backend/src/codelens/review/infrastructure/snapshot_tools.py#L184) 与 L683 的 `_reviewed_paths`），`task_done` 依据 `reviewed_paths` 判定覆盖（[`comment_collector.py`](../backend/src/codelens/review/infrastructure/comment_collector.py#L522-L588)）。条目原述“同一轮 `review_file_done` 与 `task_done` 并发”的具体竞争已不存在。

仍未解决：

- 锁定的 Agents SDK（0.18.3）同一模型响应中的多个函数工具仍并发执行（task-slot 调度）。
- [`snapshot_tools.py`](../backend/src/codelens/review/infrastructure/snapshot_tools.py#L1054-L1057) 的 `reviewed_paths` 快照（`frozenset(self._reviewed_paths)`）未与 `read_file` 的写入共享锁；`complete()` 也未持有 [`comment_collector.py`](../backend/src/codelens/review/infrastructure/comment_collector.py#L122) 的 `_state_lock`。同一 turn 最后一个 `read_file` 与 `task_done` 并发时，终态仍可能依赖调度顺序。
- 架构契约已明确“状态或完成工具不得与只读证据调用组成并行批次”，但工具执行层未找到强制实现，也缺少真实并发回归测试。

**影响**

模型若在同一 turn 同时产生证据读取与 `task_done`，Review 判定为完整或强制部分完成仍可能因协程调度顺序不同而不同。

**后续处理方向**

- 在工具执行层串行化 `task_done`（及有状态工具）与只读证据调用，或让 `complete()` 与证据写入共享锁并按依赖排序。
- 增加真实并发回归测试，验证同一批状态与证据工具在相同模型输出下产生确定性终态。

## 8. Finding 源码预览的 tombstone 可见性与重命名/overlay 处理（部分解决）

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 要求 Finding 源码预览返回 Review 固定 base/head revision 的可用完整正文，不读取可变原始工作区；重命名和 overlay 也必须回到同一份可信证据；软删除 Review 不得通过单条读取重新暴露。

**当前实现**

已解决：

- 源码预览契约已收窄为“Review 固定 base/head revision”，[`source_preview.py`](../backend/src/codelens/review/application/source_preview.py#L59-L92) 按 `base_oid`/`head_oid` 从原始仓库读取固定版本正文，常规 base/head 读取与现行契约一致，不再要求冻结 Snapshot current 内容。

仍未解决：

- 服务仍通过 `get_execution()` 取任务（[`repositories.py`](../backend/src/codelens/review/infrastructure/repositories.py#L1944-L1998)），不过滤 tombstone；源码路由（[`reviews.py`](../backend/src/codelens/interface/http/routers/reviews.py#L265-L279)）也没有先调用可见 Review 查询。已删除 Review 的 Finding 源码仍可能通过已知 ID 访问。
- 重命名文件的 base 侧应使用 `old_path`，服务对两侧仍使用 Finding 的新 `location.path`。
- workspace overlay 内容不属于 `head_oid`，overlay 任务的 target 预览可能缺失 overlay 正文。

**影响**

已删除 Review 的 Finding 源码仍可被单条读取重新暴露；重命名 Finding 可能缺少 base 内容，overlay Finding 的 target 正文可能不完整，破坏 UI 证据与最终 Finding 的一致性。

**后续处理方向**

- 在应用用例边界先验证 Review 未删除（tombstone 过滤或可见 Review 查询）。
- 显式使用重命名 `old_path` 读取 base 侧。
- 为 overlay 任务保留可恢复的 Snapshot/overlay 内容身份或专用 Artifact。
- 补充 overlay、rename、tombstone、新增和删除文件测试。

## 9. Artifact 缺少任务级配额和保留策略（未解决）

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 指定任务级存储配额和删除策略负责 Artifact 保留边界。

**当前实现**

- [`run_artifacts.py`](../backend/src/codelens/review/infrastructure/run_artifacts.py#L48-L94) 只实现输出 Artifact 写入和读取，没有容量检查、淘汰、删除或孤儿清理。
- [`input_artifacts.py`](../backend/src/codelens/workspace/infrastructure/input_artifacts.py#L61-L79) 仅清理未被 Review 引用的输入 Artifact，不构成任务级总配额或终态保留策略。
- Review 使用 tombstone，当前没有与 tombstone 或保留期联动的 Artifact 清理流程。

**影响**

长期运行或大量 Review 可能使输出、转录和输入 Artifact 持续增长，架构声明的本地存储边界没有可执行保证。

**后续处理方向**

定义可配置的单任务与全局容量、终态保留期、tombstone 行为和删除审计语义，再为写入拒绝、淘汰顺序、进程重启及部分失败增加集成测试。

## 10. SSE 事件名称没有显式版本（未解决）

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 把事件名称列为稳定契约，并要求使用已经发生的领域事实且显式版本化。

**当前实现**

- [`repositories.py`](../backend/src/codelens/review/infrastructure/repositories.py#L638) 与 L1438、L1628、L2188、L2489 等位置产生 `review.plan_created`、`review.created`、`review.failed`、`agent.succeeded`、`agent_run.started` 等未带版本的事件名。
- [`reviews.py`](../backend/src/codelens/interface/http/routers/reviews.py#L37-L43) 的终态事件集合使用相同未版本化名称，并直接作为 SSE `event` 字段发送（L377、L396）。

**影响**

未来载荷或语义演进时缺少版本协商点，旧前端、断线续传客户端和持久化 outbox 事件可能无法安全区分契约版本。

**后续处理方向**

确定事件版本命名方案和兼容迁移策略，处理数据库中既有 outbox 事件，并用 HTTP/SSE 契约测试覆盖旧事件重放与新客户端解析。

## 11. TypeScript 未启用严格模式（未解决）

**预期契约**

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) 指定 React 使用 TypeScript 严格模式。
- [`AGENTS.md`](../AGENTS.md) 要求 TypeScript 启用严格类型检查，禁止无理由的 `any`、非空断言和未校验类型转换。

**当前实现**

- [`tsconfig.app.json`](../frontend/tsconfig.app.json) 和 [`tsconfig.node.json`](../frontend/tsconfig.node.json) 均未设置 `"strict": true`，也没有完整启用对应严格选项。
- 审计时执行 `pnpm --dir frontend exec tsc --showConfig`，展开配置中同样没有严格模式标志。

**影响**

当前构建不能证明源码满足严格空值、函数类型、属性初始化等检查，文档质量门禁与实际编译门禁不一致。

**后续处理方向**

先启用 `strict` 获取完整错误清单，按业务边界修复类型问题并补充必要的边界校验；不要用全局放宽、无理由断言或 `any` 消除错误。最终让前端测试和生产构建在严格配置下通过。
