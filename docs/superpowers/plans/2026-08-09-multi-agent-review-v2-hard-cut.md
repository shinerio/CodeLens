# Multi-Agent Review v2 硬切执行计划

> 本计划取代 2026-07-31 Multi-Agent Review 系列计划中的兼容、迁移和 Legacy 路径。执行期间以本计划和 `docs/ARCHITECTURE.md` 为准；旧计划只作为历史记录，不得继续作为实现依据。

**目标：** 将 CodeLens Multi-Agent Review 原地升级为纯 v2：统一文件排除和 Review Scope、删除 `review_file_done`、支持目录批量 `get_diff`、确立 Verdict/Merge 最终裁决语义，并把数据库迁移链压缩为唯一的 v2 初始建库版本。

**技术栈：** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、SQLite WAL、OpenAI Agents SDK、React 19、TypeScript strict、Vitest、Playwright。

## 1. 已确认决策

- 不支持任何历史任务、历史数据库、v1 Tool Contract、v1 Prompt、v1 Agent/Profile、v1 Plugin 配置或 Comment v1 输出。
- 删除全部旧 Alembic revision，只保留一个从空数据库创建最终 v2 Schema 的初始化 revision。
- 所有内建 Agent、Capability Profile、Tool Contract、Prompt Output Contract 统一为 v2；不存在 fallback、alias 或双版本注册。
- 删除 `review_file_done` 及其状态和资源限制；Reviewer 只在调用 `task_done` 时接受完整覆盖校验。
- `get_diff:v2` 同时接受文件路径和目录路径，目录读取稳定排序、受预算约束并支持游标续读。
- 文件排除由统一领域模型处理，覆盖 Review target 和可见 context；来源包括 `.gitignore`、Repository Instruction、用户后缀、用户正则和二进制判定。
- `exclude_binary` 默认开启。修改或重命名文件任一相关版本为二进制时，整个变更从 Review Scope 排除。
- `verdict:v2` 只接受 `cluster_ids` 和 `action=accept|deny`；批量 accept/deny 表示分别处理每个 Cluster，不构成隐式 merge。
- `merge:v2` 只引用已有 Cluster，但可以覆盖全部模型可编辑 Comment 字段；允许单 Cluster merge。
- Merge 的严重级别不受参与 Candidate 最高严重级别限制。最终 Finding 以 Verdict/Merge 为准，Candidate 仅保留来源关系。
- “Merge 不产生新意见”是模型协议，不增加内容相似度、二次模型判断或 Candidate 字段继承限制。
- 系统仍负责引用、路径、冻结内容、位置解析、枚举、输出大小和幂等性校验；模型不能提供 Finding ID、指纹、行号、excerpt hash、任务 ID 或来源身份。

## 2. 非目标

- 不保留旧数据库升级、回滚到旧版本、数据回填或旧 revision 检测后的自动修复。
- 不保留 v1 Plugin 配置迁移器或 v1 Review API Adapter。
- 不引入真实远程 MCP、网络 Review 工具或源仓库写能力。
- 不用 MIME/文件后缀替代二进制内容判定。
- 不增加移动端或窄屏适配；前端仍以 `1280x800` 为最低桌面视口。
- 不把历史实现计划中的 Legacy 约束复制进新的运行时。

## 3. 目标架构

```text
ScopePlan.candidate_paths
          |
          +--> 捕获 immutable overlay 和 Repository Instruction 控制文件
          |
          +--> 冻结 Web FileExclusionPolicy
          |
          v
ReviewFileScopeResolver
  inputs:
    - candidate review paths
    - candidate context paths
    - GitIgnore facts
    - Repository Instruction exclusion facts
    - user suffix/regex facts
    - old/new BinaryFile facts
  output:
    - review_paths
    - context_paths
    - exclusions(path, reasons[])
    - policy_hash / scope_hash
          |
          v
SnapshotManifest.review_scope
          |
          +--> ChangeIndex / Planner / ContextBuilder
          +--> Reviewer evidence tools / task_done
          +--> Candidate validation / Cluster / Verifier
          +--> Finding publication / API projection
```

Candidate 捕获和控制文件解析是有效范围计算的输入，不属于模型可见 Review Scope。除此之外，任何使用“变更文件范围”的流程都必须消费同一个 `ReviewFileScope`，不得重新执行自己的 `.gitignore`、后缀、正则或二进制过滤。

## 4. 数据库 v2 基线

最终 `backend/migrations/versions/` 只允许存在：

```text
0001_codelens_v2.py
```

其 Alembic 元数据必须满足：

```python
revision = "0001_codelens_v2"
down_revision = None
```

### 4.1 删除的迁移链

删除以下 revision，不保留 wrapper、merge revision 或 stub：

- `0001_review_mvp.py`
- `0002_recent_repository_lru.py`
- `0003_configurable_recent_repository_limit.py`
- `0004_agent_review_completion_status.py`
- `0005_review_profiles.py`
- `0006_review_selection_requests.py`
- `0007_multi_agent_review_dag.py`
- `0008_remove_budget_columns.py`
- `0009_merge_verdict_decisions.py`
- `0e0e42b05c24_add_external_context_json_to_review_.py`

### 4.2 v2 初始化 Schema 原则

初始化 revision 直接创建与最终 SQLAlchemy metadata 一致的表、索引、外键、唯一约束和默认行：

- `review_tasks.target_paths_json` 改为非空的 `candidate_paths_json`。
- `review_tasks` 从创建起就包含 `external_context_json`、非空 v2 selection/planning context，以及冻结的 `file_exclusion_policy_json`、`file_exclusion_policy_hash`。
- 新增 `review_file_scopes`，按 `task_id` 一对一保存 canonical `scope_json`、`scope_hash` 和创建时间。
- 不创建已经废弃的预算列或 `resolution_decisions`。
- `findings` 不创建 Comment v1 专用的数值 `confidence` 列。
- 将 `verification_decisions` 规范化为 `verdict_decisions`；新增 `verdict_decision_clusters` 关系表，用外键保证只引用已有 Cluster，并用唯一约束保证一个 Cluster 最终只属于一个 Verdict Decision。
- `findings` 保存产生它的 `verdict_decision_id`；Accept 和 Merge 的来源关系可以被审计。
- 默认 Review Profile 只引用 v2 Reviewer。
- `recent_repository_settings` 和默认 Review Profile 直接由初始化 revision 插入，不执行 `SELECT ... FROM` 型历史回填。
- `downgrade()` 只负责按外键依赖逆序删除 v2 表；它不是旧版本恢复脚本。

数据库目录为空是执行本计划的前置条件。已有数据库携带旧 revision 后启动失败是预期结果，不为其增加识别或提示分支。

## 5. 执行顺序

### Task 1：重写权威架构合同

**文件：**

- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/runtime-dag.md`
- 修改：`docs/runtime-mechanism.md`
- 修改：`docs/agent-loop.md`
- 修改：`docs/build-in-tool.md`
- 删除：`docs/plugin-upgradev2.md`
- 修改：`docs/superpowers/specs/2026-07-31-multi-agent-review-design.md`
- 修改：`docs/superpowers/plans/2026-07-31-multi-agent-review-*.md`

- [ ] 先在 `ARCHITECTURE.md` 写入本计划第 1–4 节的稳定合同，再修改实现。
- [ ] 删除“保留 correctness:v1”“Comment v1 兼容”“Verifier 严重级别不得升高”“必须调用 review_file_done”等规则。
- [ ] 定义 Candidate Scope、ReviewFileScope、文件排除设置冻结、空有效范围、目录 diff、Verdict/Merge 权威和数据库硬切语义。
- [ ] 旧设计和计划文件顶部增加 `SUPERSEDED` 标记及本计划链接，移除任何“REQUIRED 执行旧计划”的措辞。
- [ ] 删除专门描述兼容迁移的 `plugin-upgradev2.md`。
- [ ] 使用 `rg` 确认所有仍属于现行说明的文档不再教授 v1 流程。

**验证：**

```bash
rg -n "correctness:v1|comment:v1|review_file_done|reviewed_files_batch|不得.*严重级别" docs/ARCHITECTURE.md docs/runtime-*.md docs/agent-loop.md docs/build-in-tool.md
```

预期：无匹配。

---

### Task 2：建立唯一的 v2 数据库初始化版本

**文件：**

- 删除：`backend/migrations/versions/*.py` 中现有十个 revision
- 创建：`backend/migrations/versions/0001_codelens_v2.py`
- 修改：`backend/src/codelens/review/infrastructure/tables.py`
- 修改：`backend/src/codelens/review/infrastructure/repositories.py`
- 修改：`backend/src/codelens/review/domain/models.py`
- 修改：`backend/src/codelens/findings/domain/verdict.py`
- 修改：`backend/tests/integration/review/test_sqlite_store.py`
- 创建：`backend/tests/integration/review/test_v2_database_baseline.py`

- [ ] 先写失败测试，断言 Alembic 只有一个 head、revision 为 `0001_codelens_v2`、`down_revision is None`。
- [ ] 写从全新临时 SQLite 文件执行 `upgrade head` 的测试，并比较 `metadata.tables` 与实际表、列、索引、外键和唯一约束。
- [ ] 写负向断言：不存在 `target_paths_json`、`confidence`、`resolution_decisions` 和预算列。
- [ ] 写 `review_file_scopes`、`verdict_decisions`、`verdict_decision_clusters` 的外键、唯一性和 cascade 测试。
- [ ] 删除旧 migration backfill、previous-head upgrade 和 downgrade/upgrade round-trip 测试；保留 fresh `base -> head -> base` 结构测试。
- [ ] 创建纯 v2 revision，表结构必须显式声明，禁止在 migration 中调用应用 Repository 或运行历史数据转换。
- [ ] 更新 SQLAlchemy table definitions 和 Repository DTO 映射，使其与初始化 revision 完全一致。
- [ ] 保持 `Database.migrate()` 只执行 `alembic upgrade head`，不增加旧 revision 识别分支。

**聚焦验证：**

```bash
uv run --project backend pytest backend/tests/integration/review/test_v2_database_baseline.py -v
uv run --project backend pytest backend/tests/integration/review/test_sqlite_store.py -v
uv run --project backend alembic -c backend/alembic.ini heads
uv run --project backend ruff check backend/migrations backend/src/codelens/review/infrastructure/tables.py
uv run --project backend mypy backend/src
```

验收：`alembic heads` 只输出 `0001_codelens_v2 (head)`。

---

### Task 3：删除 v1 Catalog、Prompt、Profile 和 Plugin 适配层

**文件：**

- 修改：`backend/src/codelens/reviewer_catalog/infrastructure/builtin_agents.py`
- 修改：`backend/src/codelens/reviewer_catalog/domain/models.py`
- 修改：`backend/src/codelens/capabilities/infrastructure/builtin_profiles.py`
- 修改：`backend/src/codelens/review/domain/review_strategy.py`
- 修改：`backend/src/codelens/review/domain/review_plan.py`
- 删除：`backend/src/codelens/plugin/application/config_migration.py`
- 删除：`backend/src/codelens/plugin/application/v1_adapter.py`
- 删除：`backend/tests/plugin/test_config_migration.py`
- 删除：`prompts/correctness/`
- 移动：`prompts/correctness-v2/` 至 `prompts/correctness/`
- 修改：`prompts/**` 中全部运行时 Prompt
- 修改：关联 Reviewer Catalog、Capability、Prompt Loader、Plugin 和前端测试

- [ ] 先写 Catalog 测试，要求所有公开和内部 Agent reference 都以 `:v2` 结束。
- [ ] 将 correctness、security、reliability-concurrency、contract-data、architecture、performance、test-regression、general、review-planner、review-verifier 全部登记为 v2。
- [ ] Capability Profile、Skill Policy 和所有 Tool Contract Reference 全部升级到 v2；删除 `legacy-reviewer` Profile。
- [ ] Comment、Planner、Verdict Output Contract 分别只允许 `comment:2`、`review-plan:2`、`verdict:2`。
- [ ] 删除 Comment v1 Schema/Codec/Collector；将现有 v2 Collector 和 Codec 提升为不带兼容后缀的 canonical 实现并更新所有 imports。
- [ ] 删除旧 correctness Prompt，把当前 correctness-v2 Prompt 提升为 canonical correctness Prompt。
- [ ] 删除 Plugin 配置迁移和 v1 Adapter；Plugin Loader 只接受 v2 manifest/config，错误信息不建议迁移旧配置。
- [ ] 更新 Fixed/General exclusivity 规则为 `general:v2`，默认 Reviewer 为 `correctness:v2` 或 Adaptive v2 Profile。
- [ ] 增加启动期 Catalog 完整性断言：任何 Agent/Profile/Tool 引用 v1 都导致启动失败。

**聚焦验证：**

```bash
uv run --project backend pytest backend/tests/unit/reviewer_catalog backend/tests/unit/capabilities backend/tests/plugin -v
uv run --project backend pytest backend/tests/unit/review/test_i18n_prompt_loader.py -v
rg -n "correctness:v1|general:v1|comment.?v1|legacy-reviewer|v1_adapter|config_migration" backend/src frontend/src prompts
```

预期：最后一个命令无匹配。

---

### Task 4：实现统一文件排除领域模型和 Web 设置

**文件：**

- 创建：`backend/src/codelens/workspace/domain/review_file_scope.py`
- 修改：`backend/src/codelens/workspace/domain/models.py`
- 修改：`backend/src/codelens/workspace/domain/ports.py`
- 创建：`backend/src/codelens/workspace/application/file_exclusion_settings.py`
- 创建：`backend/src/codelens/workspace/infrastructure/file_exclusion_settings.py`
- 修改：`backend/src/codelens/workspace/infrastructure/git_ignore.py`
- 创建：`backend/src/codelens/workspace/infrastructure/binary_file_classifier.py`
- 修改：`backend/src/codelens/interface/http/routers/settings.py`
- 修改：`backend/src/codelens/interface/http/dependencies.py`
- 修改：`backend/src/codelens/bootstrap/unified.py`
- 创建：`backend/tests/unit/workspace/test_review_file_scope.py`
- 创建：`backend/tests/integration/workspace/test_file_exclusions.py`
- 创建：`backend/tests/contract/http/test_file_exclusion_settings_api.py`

- [ ] 先测试 `ReviewFileExclusionPolicy` 的 canonical JSON/hash、后缀规范化、正则编译、规则去重和默认 `exclude_binary=True`。
- [ ] 定义 `ReviewFileExclusionReason`、`ReviewFileExclusion`、`ReviewFileScope` 和纯领域 `ReviewFileScopeResolver`。
- [ ] 后缀按规范化 basename 的大小写无关 literal suffix 匹配；正则对规范化仓库相对 POSIX 路径执行 search。
- [ ] 同一路径保留全部命中原因，并使用固定原因顺序生成稳定 scope hash。
- [ ] `.gitignore` 由 Git Adapter 产生 facts，保持 Git 对 tracked/untracked 的语义；领域模型不执行 Git 命令。
- [ ] 二进制 Adapter 综合 `.gitattributes`、Git diff binary 结果和冻结内容探测。modified/renamed 检查 old/new，deleted 检查 base，added/untracked 检查 current。
- [ ] 对 Review target 和可见 context 使用同一排除策略；Instruction 控制文件独立捕获，但不因排除规则消失。
- [ ] 新增 `GET/PUT /api/settings/file-exclusions`，PUT 支持原子部分更新，保存时拒绝非法正则、空后缀和超限规则。
- [ ] 设置存储沿用本地原子文件设置模式，不把实时设置表作为任务事实；任务创建时另行冻结副本。

**聚焦验证：**

```bash
uv run --project backend pytest backend/tests/unit/workspace/test_review_file_scope.py -v
uv run --project backend pytest backend/tests/integration/workspace/test_file_exclusions.py -v
uv run --project backend pytest backend/tests/contract/http/test_file_exclusion_settings_api.py -v
```

集成测试必须使用真实临时 Git 仓库，覆盖 tracked、untracked、ignored、added、modified、deleted、renamed、symlink、二进制和 `.gitattributes`。

---

### Task 5：冻结并统一消费 ReviewFileScope

**文件：**

- 修改：`backend/src/codelens/workspace/application/plan_scope.py`
- 修改：`backend/src/codelens/workspace/application/capture_overlay.py`
- 修改：`backend/src/codelens/workspace/application/create_snapshot.py`
- 修改：`backend/src/codelens/workspace/infrastructure/git_workspace.py`
- 修改：`backend/src/codelens/workspace/infrastructure/git_overlay.py`
- 修改：`backend/src/codelens/workspace/infrastructure/filesystem_snapshot.py`
- 修改：`backend/src/codelens/workspace/infrastructure/change_index.py`
- 修改：`backend/src/codelens/review/application/commands.py`
- 修改：`backend/src/codelens/review/application/create_triggered_review.py`
- 修改：`backend/src/codelens/review/application/context_builder.py`
- 修改：`backend/src/codelens/review/application/planning.py`
- 修改：`backend/src/codelens/review/application/review_scope.py`
- 修改：`backend/src/codelens/review/application/validate_findings.py`
- 修改：`backend/src/codelens/findings/application/validate_candidates.py`
- 修改：`backend/src/codelens/worker/execution.py`
- 修改：相关 Workspace/Review/Worker 单元与集成测试

- [ ] 将 `ScopePlan.target_paths`、`ReviewTask.target_paths` 和持久化字段统一改名为 `candidate_paths`，不保留属性 alias。
- [ ] 创建任务时读取一次 File Exclusion Settings，将 canonical policy JSON/hash 与任务原子持久化。
- [ ] 在任务自有冻结 worktree 上解析 Repository Instructions 和全部 exclusion facts，再调用唯一的 `ReviewFileScopeResolver`。
- [ ] 首次解析后将 `ReviewFileScope` 按 task 幂等写入 `review_file_scopes`；恢复执行必须读取并验证已有 scope，不重新读取当前 Web 设置。
- [ ] `SnapshotManifest` 直接包含 `review_scope`；删除独立的 `target_paths`/`excluded_paths` 双份事实，提供只读的 `review_paths`/`context_paths` 访问语义。
- [ ] ChangeIndex 只为 `review_paths` 创建 ChangedFile/Hunk；Planner 风险摘要、ContextBuilder、Review Plan、Reviewer 输入、Candidate Validator、Verifier 和 API 统计不得看见已排除变更。
- [ ] 指令解析分两步：Candidate 路径用于发现控制文件和排除规则；最终 Prompt 只绑定有效 Review 路径对应的指令链。
- [ ] 当 `review_paths` 为空时，跳过 Planner/Reviewer/Verifier 模型调用，原子完成任务并发布 0 Findings 和明确的 `review_scope_empty` 事件。
- [ ] 增加恢复测试：创建任务后修改 Web 设置并重启 Worker，Scope/hash 和可见文件必须保持不变。
- [ ] 增加全链路防泄漏测试：被排除文件不得出现在 Snapshot tools、Planner payload、Reviewer payload、Verifier location 或 Finding 中。

**聚焦验证：**

```bash
uv run --project backend pytest backend/tests/unit/workspace backend/tests/integration/workspace -v
uv run --project backend pytest backend/tests/unit/review/test_context_builder.py backend/tests/unit/review/test_planning.py -v
uv run --project backend pytest backend/tests/unit/worker backend/tests/integration/review -v
```

注意：当前工作区的 `backend/src/codelens/workspace/infrastructure/git_cli.py` 和 `backend/tests/integration/workspace/test_repository_inspection.py` 已有用户修改。执行本任务前必须检查并保留这些修改；需要触碰同一文件时进行最小化合并，不得覆盖或回退。

---

### Task 6：实现 get_diff:v2 并删除 review_file_done

**文件：**

- 修改：`backend/src/codelens/review/infrastructure/snapshot_tools.py`
- 修改：`backend/src/codelens/review/infrastructure/comment_collector.py`
- 修改：`backend/src/codelens/review/infrastructure/capability_tools.py`
- 修改：`backend/src/codelens/review/domain/tool_limits.py`
- 修改：`backend/src/codelens/review/infrastructure/file_tool_limits.py`
- 修改：`backend/src/codelens/review/infrastructure/openai_runtime.py`
- 修改：`prompts/sys/en/tools.json`
- 修改：`prompts/sys/zh-CN/tools.json`
- 修改：`prompts/sys/en/review-workflow.md`
- 修改：`prompts/sys/zh-CN/review-workflow.md`
- 修改：相关 Tool Schema、Runtime 和 Snapshot tests

- [ ] 先写 Tool Schema 测试：`review_file_done` 不存在，所有 Reviewer 只暴露 v2 evidence/comment/task tools。
- [ ] 删除 `_reviewed_files`、`complete_files()`、`reviewed_files_batch`、`undeclared_files` 和所有相关 DTO/i18n 文案。
- [ ] 将完成覆盖定义为成功完整返回过 diff 的 `review_paths`；`read_file` 不单独计为变更覆盖。
- [ ] `task_done` 计算 `review_paths - diff_viewed_paths`。缺失时返回稳定错误码、总数和有界路径列表，不 finalize Agent Run，允许模型继续调用工具。
- [ ] `get_diff(path, cursor?)` 先精确匹配文件，否则按目录前缀递归匹配；空路径代表 Review Scope 根目录。
- [ ] 批量结果按 path 稳定排序，使用现有 `max_results` 和 `max_read_bytes` 控制页大小，返回 `has_more` 和 opaque `next_cursor`。
- [ ] 只有完整包含在响应中的文件加入 `diff_viewed_paths`；输出截断、Snapshot 完整性错误或单文件超限不得误记覆盖。
- [ ] 文件路径精确命中优先于同名目录前缀；删除文件和已经不存在的目录仍通过 ChangeIndex 路径前缀工作。
- [ ] 保留现有 incomplete retry/partial fallback 机制，但其中只记录 `missing_diff_files`，不再存在声明完成状态。

**聚焦验证：**

```bash
uv run --project backend pytest backend/tests/unit/review/test_snapshot_tools.py -v
uv run --project backend pytest backend/tests/unit/review/test_capability_tools.py -v
uv run --project backend pytest backend/tests/contract/review/test_openai_runtime.py -v
```

---

### Task 7：收敛 Verdict/Merge v2 和最终发布语义

**文件：**

- 修改：`backend/src/codelens/findings/domain/verdict.py`
- 修改：`backend/src/codelens/findings/infrastructure/verdict_codec.py`
- 修改：`backend/src/codelens/review/infrastructure/verdict_tools.py`
- 修改：`backend/src/codelens/worker/execution.py`
- 修改：`backend/src/codelens/findings/application/publish_findings.py`
- 修改：`backend/src/codelens/review/infrastructure/repositories.py`
- 修改：`prompts/review-verdict/en.md`
- 修改：`prompts/review-verdict/zh-CN.md`
- 修改：相关 Verdict Codec、Publisher、Repository、Runtime tests

- [ ] 先固定严格 Tool Schema：`verdict` 只允许 `cluster_ids`、`action`；`merge` 要求全部模型可编辑字段；二者都拒绝 unknown properties。
- [ ] `cluster_ids` 非空、调用内唯一并且必须来自当前任务 Cluster。未知、已处理或重复 Cluster 在提交时立即拒绝，不等到 finalize 才发现。
- [ ] `verdict(..., accept|deny)` 在 Collector 内展开为逐 Cluster Decision；批量 accept 最终发布多个独立 Findings。
- [ ] `merge` 接受一个或多个 Cluster，产生一个 Decision/Finding；一个 Cluster 不得同时出现在其他 accept、deny 或 merge 中。
- [ ] `finalize_verdicts` 要求当前任务所有 Cluster 恰好覆盖一次；无 Cluster 时允许空 finalize。
- [ ] Verifier 输入为每个 Cluster 提供 canonical 和参与 Candidate 的 path、side、existing_code 及评论字段，避免要求模型在缺少位置数据时构造完整 merge。
- [ ] Merge Location 使用与 Reviewer Comment 相同的冻结内容解析器，允许选择任意有效 `review_paths`，由宿主派生 start/end line、excerpt hash 和 changed_hunk ID。
- [ ] Publisher 对 Accept 使用 Cluster canonical 表示；对 Merge 使用 Verdict 提交的全部字段和已解析 Location，不再回退到第一 Cluster canonical Location。
- [ ] 删除 Candidate severity ceiling、category/dimension 继承和内容相似度检查。增加 low Candidate merge 为 critical Finding 的正向测试。
- [ ] 来源 Cluster、Candidate、Reviewer 关系由 `verdict_decision_clusters` 和 Finding provenance 保存；这些字段不允许模型覆盖。
- [ ] Verdict Decision、关系表、Finding 插入和发布事件继续在同一个幂等事务中完成。

**聚焦验证：**

```bash
uv run --project backend pytest backend/tests/unit/findings/test_verdict_codec.py -v
uv run --project backend pytest backend/tests/unit/findings/test_publish_findings.py -v
uv run --project backend pytest backend/tests/contract/review/test_openai_runtime.py -v
uv run --project backend pytest backend/tests/integration/review/test_sqlite_store.py -v
```

必须覆盖：批量 accept 发布 N 个 Finding、批量 deny 发布 0 个、单 Cluster merge、多 Cluster merge、Merge 改写 Location、严重级别上调、unknown Cluster、重复覆盖、缺失覆盖和事务重放。

---

### Task 8：完成桌面端文件排除设置页

**文件：**

- 修改：`frontend/src/features/settings/types.ts`
- 修改：`frontend/src/features/settings/api.ts`
- 修改：`frontend/src/features/settings/SettingsPage.tsx`
- 修改：`frontend/src/features/settings/SettingsPage.css`
- 修改：`frontend/src/features/settings/SettingsPage.test.tsx`
- 修改：相关 API mock 和 Playwright fixture/spec

- [ ] 增加 `excludeBinary` 开关，初始值为 true。
- [ ] 增加后缀规则和路径正则的新增、编辑、删除、去重和保存交互。
- [ ] 后缀输入展示 literal suffix 语义；正则输入明确作用于仓库相对 POSIX 路径。
- [ ] 后端返回非法规则时将错误定位到具体输入项，不丢失其他未保存编辑。
- [ ] 覆盖加载、空规则、保存成功、校验失败、网络失败、部分编辑和超长规则状态。
- [ ] 在 `1280x800` 验证长正则不溢出、不遮挡保存按钮，不实现移动端布局。

**聚焦验证：**

```bash
pnpm --dir frontend test -- SettingsPage.test.tsx
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

---

### Task 9：清除残留兼容代码并执行全量门禁

**范围：** Backend、Frontend、Prompts、Docs、Tests、Fixtures。

- [ ] 删除旧 Agent/Profile/Tool/Prompt fixtures、v1 输出 golden files 和 migration backfill helpers。
- [ ] 确认 `prompts/` 不包含 v1 correctness 文本或 `review_file_done` 指令。
- [ ] 确认 `backend/migrations/versions/` 只有 `0001_codelens_v2.py`。
- [ ] 确认 runtime source 不包含 Legacy path、v1 Adapter、Comment v1 或严重级别 ceiling。
- [ ] 确认所有数据库测试从新的临时空数据库开始，不复制仓库内数据库文件。
- [ ] 重新索引 CodeGraph/codebase-memory，并检查删除/改名影响没有遗留调用者。
- [ ] 更新 API 示例、事件字段、错误码和中文/英文 UI 文案。

**静态清理检查：**

```bash
test "$(rg --files backend/migrations/versions | wc -l | tr -d ' ')" = "1"
rg -n "correctness:v1|general:v1|comment.?v1|review_file_done|reviewed_files_batch|legacy-reviewer|v1_adapter|migrate_config_to_v2" backend/src backend/tests frontend/src prompts
```

预期：第二个命令无匹配。历史计划目录允许保留 `SUPERSEDED` 文本中的历史名词，但不得被运行时或现行架构文档引用为有效合同。

**后端全量门禁：**

```bash
uv run --project backend pytest backend/tests -v
uv run --project backend ruff check backend
uv run --project backend mypy backend/src
```

**前端全量门禁：**

```bash
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend exec playwright test
```

## 6. 关键验收场景

### 6.1 数据库

- 空数据目录首次启动创建唯一 v2 head，默认 Profile 只引用 v2。
- `alembic current` 为 `0001_codelens_v2`，不存在旧 revision 文件。
- 旧 revision 数据库不会被识别或升级。
- Fresh database 上 Review 创建、Worker 恢复、Verdict 发布和级联删除均通过。

### 6.2 文件范围

- ignored untracked、用户后缀、用户正则和二进制文件不会进入 Snapshot 可见范围。
- tracked 文件遵守 Git 自身 `.gitignore` 语义，不因后来新增 ignore 规则错误消失。
- deleted binary 通过 base 内容排除；renamed binary 检查 old/new 双侧。
- 一个文件同时命中多条规则时保留全部 reasons。
- 任务创建后修改 Web 设置并重启，任务 Scope 不变化。
- 全部 Candidate 被排除时任务不调用模型并以 0 Findings 完成。

### 6.3 Reviewer 工具

- Reviewer 工具列表中不存在 `review_file_done`。
- 目录 `get_diff` 一次返回多个稳定排序文件，游标无遗漏、无重复。
- `task_done` 在存在未完整返回 diff 的 Review 文件时拒绝，补读后允许完成。
- 排除文件不能通过文件路径或目录前缀重新读出。

### 6.4 Verdict

- `verdict` Schema 没有任何 Finding 字段。
- Accept 两个 Cluster 得到两个 Finding；只有 `merge` 能把 Cluster 合成一个 Finding。
- Merge 能完全改写 Comment 字段和 Location，并允许高于来源 Candidate 的 severity。
- 所有 Cluster 恰好裁决一次；未知、重复和遗漏都产生稳定工具错误。
- Final Finding 的 provenance、ID、位置哈希和事务幂等性仍由宿主保证。

## 7. 完成定义

- [ ] `docs/ARCHITECTURE.md` 与 v2 实现一致。
- [ ] v1 Catalog、Prompt、Tool、Plugin Adapter、Comment Codec 和兼容测试已删除。
- [ ] 迁移目录只包含纯净 v2 初始化 revision。
- [ ] 所有文件排除经同一领域 Resolver 处理，所有下游只消费 `ReviewFileScope`。
- [ ] `review_file_done` 已删除，目录 `get_diff` 和 `task_done` 覆盖门禁已完成。
- [ ] Verdict/Merge 的 Schema、存储、发布和 Prompt 语义一致。
- [ ] Backend 与 Frontend 全量门禁实际执行并全部通过。
- [ ] 用户现有工作区修改未被覆盖；未相关的 `pyproject.toml` 保持原样。

