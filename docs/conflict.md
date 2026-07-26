# 文档与实现冲突清单

## 1. 文档定位

本文记录 2026-07-26 文档审计中发现的潜在实现问题。审计基线为当前工作树（基于提交 `0916275e85a7e3a95074fb8b7955d58886102247`）。这些条目与 [`ARCHITECTURE.md`](./ARCHITECTURE.md)、根目录 [`README.md`](../README.md) 或 [`AGENTS.md`](../AGENTS.md) 中的明确契约不一致，且从安全性、稳定契约或数据生命周期判断，更可能需要修改代码或补充产品决策，因此本次没有把权威文档改成当前实现行为。

每项状态均为“待处理”。修复后应同步补充相应测试，并从本文件删除已经解决的条目。

## 2. 非回环地址允许无鉴权监听

**预期契约**

- [`ARCHITECTURE.md`](./ARCHITECTURE.md) 规定本机无鉴权模式只能绑定 `127.0.0.1`，非回环地址必须具有明确的信任和访问边界。
- [`README.md`](../README.md) 明确说明当前无鉴权模式不支持直接开放到局域网或互联网。

**当前实现**

- [`settings.py`](../backend/src/codelens/bootstrap/settings.py#L45-L52) 把 `0.0.0.0` 列入允许值；只要配置了至少一个 `repository_roots` 就会通过校验。
- [`test_settings.py`](../backend/tests/unit/bootstrap/test_settings.py) 把这一行为作为有效配置覆盖。

**影响**

`repository_roots` 只能限制仓库访问范围，不能替代 HTTP 身份认证或网络访问控制。服务可能在所有接口上暴露无鉴权的仓库浏览、Review 创建和 Settings 写入能力。

**后续处理方向**

确认首版是否应完全拒绝非回环绑定；若确实需要远程访问，先定义并实现明确的认证、授权和传输边界，再调整架构与 README。

## 3. 默认数据目录位于源码仓库内

**预期契约**

- [`README.md`](../README.md) 声明 `CODELENS_DATA_DIR` 默认值为 `~/.local/share/codelens-review`，并说明网关配置默认保存在仓库之外。
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) 规定 Secret Store 默认位于源码仓库之外。

**当前实现**

- [`settings.py`](../backend/src/codelens/bootstrap/settings.py#L26-L36) 将默认 `data_dir` 解析为 `<project-root>/data`。
- [`cli.py`](../backend/src/codelens/bootstrap/cli.py#L24-L45) 使用 `Settings()` 产生 `--data-dir` 默认值，并把该值重新传入运行时设置，因此普通 CLI 启动同样落到仓库内的 `data/`。

**影响**

SQLite、Artifact、worktree 和 `secrets/model-gateways.json` 默认与源码仓库相邻。虽然当前 `.gitignore` 可以降低误提交概率，但这不满足 Secret Store 的默认隔离契约，也与 README 给出的运维路径不一致。

**后续处理方向**

确定跨平台应用数据目录策略，并让 Settings、CLI、启动脚本、测试和 README 共用同一默认值；同时保留显式 `--data-dir`/环境变量覆盖能力。

## 4. `model.log` 未要求操作者显式启用

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 只允许在本地操作者明确启用后，把完整且已脱敏的模型交换写入 `logs/model.log`。

**当前实现**

- [`logging.py`](../backend/src/codelens/bootstrap/logging.py#L160-L180) 每次配置进程日志时都会创建并启用 `model.log` Handler。
- [`unified.py`](../backend/src/codelens/bootstrap/unified.py#L138-L147) 始终向 `WorkerTranscriptStore` 注入 `ModelTranscriptLogWriter`。
- [`model_log.py`](../backend/src/codelens/review/infrastructure/model_log.py#L24-L52) 会在终态转录写入时记录 Prompt、原始模型输出和工具交互，没有检查 opt-in 设置。

**影响**

文件权限、轮转和脱敏边界已经存在，但包含源码和 Prompt 的完整交换仍会在默认情况下落盘，不符合显式同意要求，也可能超出操作者对“SDK 模型数据日志默认关闭”的理解。

**后续处理方向**

增加默认关闭的持久化设置或启动配置，并让 Handler 安装和 `ModelTranscriptLogWriter` 注入共同遵循该设置；补充启用、关闭和运行期切换测试。

## 5. 运行日志契约未完全落实

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 要求后端日志位于项目根目录 `logs/`，日志级别使用小写稳定值，Settings 契约明确返回默认级别和当前级别，并且 API、Worker 与 Supervisor 无需重启即可采用新级别。各日志还应结构化、独立限量轮转。

**当前实现**

- [`logging.py`](../backend/src/codelens/bootstrap/logging.py#L128-L146) 未显式传入目录时使用 `Path.cwd()/logs`，从其他目录手动启动时不保证是项目根目录。
- [`logging.py`](../backend/src/codelens/bootstrap/logging.py#L38-L45) 直接输出 `record.levelname`，实际值为 `INFO`、`WARNING` 等大写文本。
- [`dto.py`](../backend/src/codelens/interface/http/dto.py#L50-L55) 和 [`settings.py`](../backend/src/codelens/interface/http/routers/settings.py#L73-L92) 的 HTTP 契约只返回 `level`，没有分别表达默认级别和当前级别。
- Unix [`code-lens`](../code-lens#L159-L168) 与 Windows [`code-lens.ps1`](../code-lens.ps1#L61-L88) 通过 Shell 重定向写入 `supervisor.log`/`frontend.log`，不是结构化限量 Handler，也无法采用运行期日志级别设置。

**影响**

日志位置会依赖启动目录；同一架构字段存在大小写差异；客户端无法区分默认与当前设置；Supervisor 日志可能无界增长且与后端动态级别行为不一致。

**后续处理方向**

分别确认后端、前端启动输出和 Supervisor 的所有权。统一解析项目日志目录、规范化级别字段、扩展 Settings DTO，并为 Supervisor 引入可轮转且能响应运行期设置的实现或重新收窄架构契约。

## 6. 工具边界错误丢失稳定原因标识

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 要求工具调用次数耗尽、单次超时和相同结果循环立即以明确、不可重试的错误终止，诊断应保留稳定原因。

**当前实现**

- [`tool_contract.py`](../backend/src/codelens/review/infrastructure/tool_contract.py#L72-L112) 分别产生 `max_tool_calls_exceeded`、`tool_invocation_timed_out` 和 `identical_tool_result_loop`。
- Agents SDK 会把这些非 SDK 异常包装为 `UserError`；[`openai_runtime.py`](../backend/src/codelens/review/infrastructure/openai_runtime.py#L317-L324) 再把所有 `UserError` 统一转换为 `invalid_model_output`。
- 当前行为也记录在 [`agent-loop.md`](./agent-loop.md) 的异常转换说明中。

**影响**

Agent Run 虽然会停止，但 Transcript、持久化失败原因和前端诊断无法区分预算耗尽、工具超时、重复循环与普通模型输出错误，削弱了稳定错误契约和可运维性。

**后续处理方向**

在 SDK 包装边界前后保留原始领域异常身份，或建立受控的异常解包映射；为三种错误分别增加 Runtime 契约测试，验证最终 `reason_code` 和 `retryable=false`。

## 7. 同一轮状态工具并发导致完成结果依赖调度顺序

**预期契约**

`review_file_done` 应先记录已调查文件，`task_done` 再依据完整、确定的覆盖状态决定接受、打回或强制部分完成。相同模型输出不应因协程调度顺序产生不同 Review 终态。

**当前实现**

- 锁定的 Agents SDK 允许同一模型响应中的多个函数工具并发执行，详见 [`agent-loop.md`](./agent-loop.md) 的“同一轮多个工具如何执行”。
- [`comment_collector.py`](../backend/src/codelens/review/infrastructure/comment_collector.py#L260-L322) 中 `complete()` 与 `complete_files()` 读写 `_completion`、`_reviewed_files` 和重试计数，没有共享锁或同批依赖排序。
- [`tool_contract.py`](../backend/src/codelens/review/infrastructure/tool_contract.py#L61-L65) 的锁只保护调用预算和重复结果计数，不保护 Collector 状态转换。

**影响**

模型若在同一 turn 同时调用 `review_file_done` 和 `task_done`，先执行 `task_done` 时可能先被判定为未完整；当 `max_incomplete_review_retries=0` 时还可能直接强制部分完成，随后 `review_file_done` 因任务已完成而失败。相反顺序则可能完整成功。

**后续处理方向**

增加真实并发回归测试，明确同一批状态工具的顺序语义。可选方案包括让状态转换共享锁并按依赖排序、拒绝同批完成调用，或在工具执行计划层串行化有状态工具。

## 8. Finding 源码预览未完全使用冻结 Snapshot

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 要求 Finding 源码预览返回固定 base 与冻结 target/current 内容，不读取可变原始工作区；重命名和 overlay 也必须回到同一份可信证据。软删除 Review 不得通过单条读取重新暴露。

**当前实现**

- [`source_preview.py`](../backend/src/codelens/review/application/source_preview.py#L59-L92) 从原始仓库按 `base_oid`/`head_oid` 读取 `location.path`，没有读取 Snapshot current 内容。
- workspace overlay 不属于 `head_oid`，因此 target 预览可能落后于产生 Finding 的冻结 current 内容。
- 重命名文件的 base 侧应使用 `old_path`，但服务对两侧都使用 Finding 的新 `location.path`。
- 服务通过 `get_execution()` 取任务；[`repositories.py`](../backend/src/codelens/review/infrastructure/repositories.py#L621-L637) 不过滤 tombstone。源码路由 [`reviews.py`](../backend/src/codelens/interface/http/routers/reviews.py#L208-L222) 也没有先调用可见 Review 查询。

**影响**

overlay Finding 可能显示错误目标正文，重命名 Finding 可能缺少 base 内容，已删除 Review 的 Finding 源码仍可能通过已知 ID 访问。这会破坏 UI 证据与最终 Finding 的一致性及 tombstone 可见性。

**后续处理方向**

为源码预览保留可恢复的 Snapshot 内容身份或专用 Artifact，显式使用 rename `old_path`，并在应用用例边界先验证 Review 未删除。补充 overlay、rename、tombstone、新增和删除文件测试。

## 9. Artifact 缺少任务级配额和保留策略

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 指定任务级存储配额和删除策略负责 Artifact 保留边界。

**当前实现**

- [`run_artifacts.py`](../backend/src/codelens/review/infrastructure/run_artifacts.py#L41-L94) 只实现输出 Artifact 写入和读取，没有容量检查、淘汰、删除或孤儿清理。
- [`input_artifacts.py`](../backend/src/codelens/workspace/infrastructure/input_artifacts.py#L44-L79) 仅清理未被 Review 引用的输入 Artifact，不构成任务级总配额或终态保留策略。
- Review 使用 tombstone，当前没有与 tombstone 或保留期联动的 Artifact 清理流程。

**影响**

长期运行或大量 Review 可能使输出、转录和输入 Artifact 持续增长，架构声明的本地存储边界没有可执行保证。

**后续处理方向**

定义可配置的单任务与全局容量、终态保留期、tombstone 行为和删除审计语义，再为写入拒绝、淘汰顺序、进程重启及部分失败增加集成测试。

## 10. SSE 事件名称没有显式版本

**预期契约**

[`ARCHITECTURE.md`](./ARCHITECTURE.md) 把事件名称列为稳定契约，并要求使用已经发生的领域事实且显式版本化。

**当前实现**

- [`repositories.py`](../backend/src/codelens/review/infrastructure/repositories.py#L328-L393) 等位置产生 `review.created`、`review.completed`、`review.failed`、`agent.succeeded` 等未带版本的事件名。
- [`reviews.py`](../backend/src/codelens/interface/http/routers/reviews.py#L34-L40) 的终态事件集合使用相同未版本化名称，并直接作为 SSE `event` 字段发送。

**影响**

未来载荷或语义演进时缺少版本协商点，旧前端、断线续传客户端和持久化 outbox 事件可能无法安全区分契约版本。

**后续处理方向**

确定事件版本命名方案和兼容迁移策略，处理数据库中既有 outbox 事件，并用 HTTP/SSE 契约测试覆盖旧事件重放与新客户端解析。

## 11. TypeScript 未启用严格模式

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
