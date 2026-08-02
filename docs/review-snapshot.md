# ReviewSnapshot 设计与构造说明

## 1. 定位

`ReviewSnapshot` 是一次 Review 运行中所有 Agent 唯一允许访问的仓库证据视图。

它不是整个仓库内容的内存副本，也不是 Git commit 的简单别名。它由隔离的任务 worktree、可见文件清单、内容哈希、变更索引和冻结输入共同构成，负责提供以下长期属性：

- Agent 不直接读取用户的原始工作区；
- branch、commit 和 workspace change 在执行前解析为不可变输入；
- 只有 Manifest 中允许的目标、上下文和规则文件可以被读取；
- Finding 的位置必须能落到冻结的 changed hunk 中；
- worktree 被修改时能够确定性检测并失败；
- Worker 重启后可以从持久化输入恢复同一份证据状态。

核心领域模型定义在 [`backend/src/codelens/workspace/domain/models.py`](../backend/src/codelens/workspace/domain/models.py)。

## 2. 领域结构

```python
@dataclass(frozen=True)
class ReviewSnapshot:
    snapshot_id: str
    worktree: TaskWorktree
    target: ReviewTarget
    fingerprint: RepositoryFingerprint
    manifest: SnapshotManifest
    change_index: ChangeIndex
    manifest_hash: str = ""
    snapshot_artifact: OpaqueArtifact | None = None
```

字段含义如下：

| 字段 | 内容 |
| --- | --- |
| `snapshot_id` | 本次构造生成的随机标识，格式为 `snapshot_<uuid>` |
| `worktree` | CodeLens 所有的 detached Git worktree，以及任务、HEAD 和所有权证明 |
| `target` | 已解析并冻结的 `base_oid`、`head_oid` 和可选 workspace overlay 哈希 |
| `fingerprint` | HEAD、Git index 和 worktree Manifest 三部分指纹 |
| `manifest` | Agent 可见路径的分类清单及逐文件完整性元数据 |
| `change_index` | 文件级变化和 changed hunks，用于约束 Finding 的证据范围 |
| `manifest_hash` | 规范化 Manifest 的 SHA-256 |
| `snapshot_artifact` | 持久化 Snapshot 元数据的不透明 Artifact 引用、哈希和大小 |

`ReviewSnapshot` 是 frozen dataclass，但其不可变语义不仅依靠 Python 对象本身，还依靠 pinned Git OID、隔离 worktree、Manifest 哈希和运行时完整性验证共同保证。

## 3. SnapshotManifest 的内容

`SnapshotManifest` 把可见路径分为不同职责：

```python
@dataclass(frozen=True)
class SnapshotManifest:
    target_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    excluded_paths: tuple[ExcludedPath, ...]
    instruction_paths: tuple[str, ...] = ()
    entries: tuple[SnapshotEntry, ...] = ()
```

- `target_paths`：本次需要 Review、允许产生 Finding 的文件。
- `context_paths`：Agent 可以读取，但不能作为任意 Finding 目标的辅助代码。
- `instruction_paths`：实际作用于目标文件的 `AGENTS.md` 等控制输入。
- `excluded_paths`：被 Git ignore、instruction policy 或 structured skip 排除的路径及原因。
- `entries`：所有可见路径的冻结元数据。

### 3.1 `context_paths` 是否包含仓库全部代码

`context_paths` 不等于代码仓库中的全部文件。它表示当前 Snapshot 中经过过滤、允许 Agent 作为辅助上下文读取的文件集合。

它的初始候选主要来自：

```bash
git ls-files --cached --others --exclude-standard -z
```

也就是 Git 已跟踪文件和未被 ignore 的 untracked 文件。候选路径随后还要经过 Manifest 构造阶段的边界检查，因此以下内容不会作为普通 context 保留：

- 被 `.gitignore` 等 Git-native ignore 规则排除的文件；
- 被 instruction policy 或 structured skip 排除的路径；
- `AGENTS.md` 等控制输入，这些文件单独进入 `instruction_paths`；
- 目录、submodule gitlink 等没有普通文件内容的条目；
- 不合法、逃逸 worktree 或包含不安全 symlink 的路径。

当前实现中，`context_paths` 与 `target_paths` 不是互斥集合。一个本次发生变化的文件通常同时具有两种语义：

- 位于 `target_paths`，表示它是本次 Review 和 Finding 的合法目标；
- 位于 `context_paths`，表示 Agent 也可以把它作为代码上下文读取。

例如仓库包含：

```text
src/changed.py       # 本次修改
src/support.py       # 未修改的辅助代码
.env                 # 被 ignore
AGENTS.md            # instruction control input
vendor/module        # submodule
```

可能构造出：

```text
target_paths      = ("src/changed.py",)
context_paths     = ("src/changed.py", "src/support.py")
instruction_paths = ("AGENTS.md",)
excluded_paths    = (ExcludedPath(path=".env", ...), ...)
```

因此，`target_paths` 表达“可以针对哪些文件产生 Review 结论”，`context_paths` 表达“允许读取哪些文件来辅助分析”。文件是否属于 target 不能通过它是否存在于 context 中反向推断，调用方应分别使用 `manifest.is_target()` 和 `manifest.is_context()` 检查对应权限。

每个 `SnapshotEntry` 包含：

```python
@dataclass(frozen=True)
class SnapshotEntry:
    path: str
    kind: Literal["file", "symlink", "deleted"]
    mode: int
    size_bytes: int
    content_hash: str
    symlink_target: str | None
    origin: Literal["target", "context", "instruction"]
```

因此，`ReviewSnapshot` 对象通常不直接保存源码正文。源码位于受控 worktree 中，由只读工具按 Manifest 边界访问；Manifest 通过文件类型、权限、大小、symlink target 和 SHA-256 证明读取内容没有变化。

## 4. 总体构造流程

```text
用户 Review Scope
    ↓
解析完整 Git OID 与候选路径
    ↓
捕获可选 workspace overlay
    ↓
创建任务独占的 detached worktree
    ↓
应用经过哈希验证的 overlay
    ↓
解析每个目标文件的 instruction chain
    ↓
构造 Manifest 与逐文件内容哈希
    ↓
构造 ChangeIndex 和 changed hunks
    ↓
持久化 Snapshot 元数据 Artifact
    ↓
生成 ReviewSnapshot
```

## 5. 固定 Review 范围

范围解析由 `GitWorkspaceAdapter.plan_scope()` 完成。用户提供的 branch 或 ref 只在这一阶段使用，随后会被转换为完整 Git object ID。

不同 Scope 的处理规则如下：

- Branch：`head_oid` 是目标分支 commit，`base_oid` 是 base 和 target 的 merge-base。
- Commit：解析显式 base 和 target，并验证 base 是 target 的祖先。
- Uncommitted：`base_oid` 和 `head_oid` 都是当前 HEAD，同时强制捕获 workspace overlay。
- Full repository：`base_oid` 和 `head_oid` 都是目标 commit，候选路径来自该 Git tree 的全部条目。

输出为不可变的 `ScopePlan`：

```python
@dataclass(frozen=True)
class ScopePlan:
    base_oid: str
    head_oid: str
    target_paths: tuple[str, ...]
    capture_workspace_overlay: bool
    scope_type: ReviewScopeType
    warnings: tuple[str, ...] = ()
```

当范围要求包含 workspace changes 时，目标 `head_oid` 必须等于当前 HEAD。CodeLens 不允许把当前工作区修改套用到另一个 commit 上。staged、unstaged 和允许的 untracked 路径会合并进 `target_paths`。

范围解析实现位于 [`backend/src/codelens/workspace/infrastructure/git_workspace.py`](../backend/src/codelens/workspace/infrastructure/git_workspace.py)。

## 6. 捕获 workspace overlay

如果 `ScopePlan.capture_workspace_overlay` 为 `true`，`ReviewInputCaptureService` 会捕获未提交状态：

1. 对目标路径计算捕获前指纹；
2. 捕获 staged、unstaged 和 untracked 状态为规范化 overlay；
3. 将 overlay 写入不透明 Artifact；
4. 再次计算源工作区指纹；
5. 两次指纹一致才接受结果。

如果捕获过程中工作区发生变化，当前 Artifact 会被丢弃并重试一次。第二次仍不稳定时抛出 `SnapshotStaleError`，避免生成由两个不同时刻内容拼成的 Snapshot。

成功后得到：

```python
CapturedReviewInput(
    target=ReviewTarget(
        base_oid=scope_plan.base_oid,
        head_oid=scope_plan.head_oid,
        overlay_hash=artifact.content_hash,
    ),
    overlay_artifact=artifact,
)
```

实现位于 [`backend/src/codelens/workspace/application/capture_overlay.py`](../backend/src/codelens/workspace/application/capture_overlay.py)。

## 7. 创建隔离 worktree

`ReviewWorktreeLifecycle` 在冻结的 `head_oid` 上创建 detached worktree，而不是让 Agent 读取用户仓库。

worktree 的所有权记录包含：

- `worktree_id`；
- task ID；
- Git common-dir 哈希；
- checkout path 哈希；
- HEAD OID；
- 随机 ownership token 哈希。

这些信息同时写入注册表和 task worktree marker。后续访问或清理必须验证 registry、marker、路径、Git common-dir 和 token 一致，防止错误操作不属于当前任务的目录。

如果存在 overlay，生命周期服务会通过 `OpaqueArtifact.reference` 加载字节，并验证 `content_hash` 后 materialize 到任务 worktree。失败时只清理当前 CodeLens 所有的 worktree。

相关实现：

- [`backend/src/codelens/workspace/application/worktree_lifecycle.py`](../backend/src/codelens/workspace/application/worktree_lifecycle.py)
- [`backend/src/codelens/workspace/infrastructure/git_worktrees.py`](../backend/src/codelens/workspace/infrastructure/git_worktrees.py)

## 8. 解析规则输入

`SnapshotService.resolve_instructions()` 会为每个目标路径独立解析 instruction chain，然后合并：

- instruction documents；
- 每个 target 对应的 rule chain；
- excludes；
- warnings。

如果同一个 instruction document 或 target chain 在不同目标的解析过程中出现不一致，构造会直接失败。这避免在 `AGENTS.md` 等控制输入变化时生成内部不一致的 Snapshot。

只有实际作用于当前 active target 的规则文件才会进入 `instruction_paths`。

## 9. 构造 Manifest

`FilesystemSnapshotBuilder.build()` 首先通过以下 Git 命令枚举 tracked 和允许的 untracked 文件：

```bash
git ls-files --cached --others --exclude-standard -z
```

随后依次执行：

1. 将路径规范化为仓库相对 POSIX 路径；
2. 应用 Git-native ignore 解析；
3. 应用 instruction structured-skip；
4. 将路径分类为 target、context 或 instruction；
5. 校验文件类型和路径 containment；
6. 计算逐文件 SHA-256；
7. 生成规范化 Manifest JSON 和 `manifest_hash`。

安全约束包括：

- 拒绝空路径、绝对路径、包含 `..` 或 NUL 的路径；
- regular file 的解析路径必须位于 worktree 内；
- symlink 必须是相对链接，解析后不能逃出 worktree；
- 删除文件记录为 `kind="deleted"`，其内容哈希为空字节的 SHA-256；
- submodule gitlink、目录等没有普通文件内容的条目会被跳过；
- 文件按 64 KiB 分块计算哈希，不要求把整个文件加载进内存。

Manifest 使用排序 key 和紧凑分隔符序列化，因此相同内容会得到确定性的 `manifest_hash`。

实现位于 [`backend/src/codelens/workspace/infrastructure/filesystem_snapshot.py`](../backend/src/codelens/workspace/infrastructure/filesystem_snapshot.py)。

## 10. RepositoryFingerprint

Manifest 构造同时生成：

```python
RepositoryFingerprint(
    head_sha=<worktree HEAD>,
    index_hash=<staged binary diff SHA-256>,
    worktree_hash=<manifest_hash>,
)
```

三部分分别标识：

- detached worktree 指向的 commit；
- index 中相对 HEAD 的 staged 状态；
- Manifest 可见内容的完整状态。

该指纹不存储源码正文。

## 11. 构造 ChangeIndex

Manifest 完成后，`SnapshotService.freeze()` 使用以下输入构造 `ChangeIndex`：

```python
worktree
captured.target.base_oid
manifest.target_paths
scope_plan.scope_type
```

`ChangeIndex` 包含两类证据：

- `files`：added、modified、deleted、renamed 等文件级变化；
- `hunks`：路径、old/new side、开始/结束行和 excerpt hash。

`ChangeIndex.contains()` 要求一个位置的完整范围落在同路径、同 side 的 frozen hunk 内：

```python
def contains(self, path: str, start_line: int, end_line: int, side: str) -> bool:
    return any(
        hunk.path == path
        and hunk.side == side
        and start_line >= hunk.start_line
        and end_line <= hunk.end_line
        for hunk in self.hunks
    )
```

Candidate Finding 的位置校验依赖这个索引。Agent 给出的路径和行号不会仅因为格式合法就被接受。

## 12. 最终冻结与元数据 Artifact

最终构造由 `SnapshotService.freeze()` 完成：

```python
build = await manifest_builder.build(...)
change_index = await change_index_builder.build(...)
snapshot_id = f"snapshot_{uuid.uuid4().hex}"
artifact = await artifacts.write_bytes(snapshot_metadata)

return ReviewSnapshot(
    snapshot_id=snapshot_id,
    worktree=worktree,
    target=captured.target,
    fingerprint=build.fingerprint,
    manifest=build.manifest,
    change_index=change_index,
    manifest_hash=build.manifest_hash,
    snapshot_artifact=artifact,
)
```

持久化的 Snapshot metadata 是 canonical JSON，包含：

```json
{
  "schema_version": 1,
  "snapshot_id": "snapshot_...",
  "worktree_id": "worktree-...",
  "worktree_path_hash": "...",
  "repository_common_dir_hash": "...",
  "base_oid": "...",
  "head_oid": "...",
  "overlay_hash": "...",
  "scope_type": "branch",
  "manifest_hash": "...",
  "manifest": {},
  "change_index": {}
}
```

Artifact 保存的是 Snapshot 结构、哈希和变更证据，不是整个仓库的源码正文。Artifact 存储通过不透明引用和 expected hash 读取，调用方不能依赖或传播内部文件路径。

构造实现位于 [`backend/src/codelens/workspace/application/create_snapshot.py`](../backend/src/codelens/workspace/application/create_snapshot.py)。

## 13. 运行时完整性验证

`FilesystemSnapshotBuilder.verify()` 会重新构造 Manifest 中每个 entry 的当前元数据，并与冻结值逐项比较：

- path；
- kind；
- mode；
- size；
- content hash；
- symlink target；
- origin。

任何差异、I/O 错误或路径逃逸都会转换为：

```text
WorktreeMutatedError("review worktree content changed")
```

这使得 Agent 或其他进程即使修改了受控 worktree，也不能让后续验证静默接受变化后的证据。

## 14. Worker 重启恢复

Worker 的 `prepare()` 不依赖一个长期驻留内存的 `ReviewSnapshot` 对象。它会从持久化执行数据恢复：

1. pinned `base_oid` 和 `head_oid`；
2. frozen `target_paths`；
3. overlay Artifact 引用和哈希；
4. 已登记的 task worktree；
5. 已持久化的 Review Plan、执行规格和 checkpoints。

如果 worktree 仍存在，则验证所有权；如果缺失，则从 pinned HEAD 和 overlay Artifact 重建。随后重新解析 instructions，并在恢复后的受控 worktree 上再次执行 `SnapshotService.freeze()`。

因此需要区分两种身份：

- `snapshot_id` 是一次 `freeze()` 调用的实例 ID，重建后可以变化；
- Snapshot 的证据身份由 pinned OID、overlay hash、Manifest 内容、`manifest_hash` 和 `ChangeIndex` 决定。

恢复实现位于 [`backend/src/codelens/worker/execution.py`](../backend/src/codelens/worker/execution.py)。

## 15. Review 终态后的清理

Review 进入终态后，`ReviewSnapshot` 的不同组成部分具有不同生命周期，不能把“清理 Snapshot”理解为删除所有相关数据。

```text
Review 完成
├── ReviewSnapshot 内存对象：释放
├── detached task worktree：立即尝试删除
├── Snapshot metadata Artifact：成为 orphan，后续启动时清理
├── workspace overlay Artifact：继续保留
└── Plan、Finding、checkpoint、Agent 输出和 transcript：继续保留
```

### 15.1 内存中的 ReviewSnapshot

`ReviewSnapshot` 本身是 Worker 在 `prepare()` 中构造的运行时对象。Review 执行结束且不再有引用后，它会随普通 Python 对象生命周期释放，不存在单独的持久化 Snapshot 对象删除操作。

持久化的是构造和恢复所需的 OID、target paths、overlay identity、Manifest metadata、执行规格、Plan 和 checkpoint 等数据。

### 15.2 task worktree

正常 Worker 执行路径在 orchestrator 返回后依次执行：

```python
await orchestrator.execute(task_id)
await self._finalize_if_terminal(task_id)
await self._cleanup_terminal_worktree(task_id)
```

当 Review 状态为 `completed`、`partial`、`failed` 或 `canceled` 时，`_cleanup_terminal_worktree()` 会尝试删除对应的任务 worktree：

1. 从 worktree registry 取得 task 对应的记录；
2. 验证 registry、ownership marker、task ID、checkout path、Git common-dir、HEAD OID 和 ownership token；
3. 对对应 Git common-dir 加锁；
4. 执行 `git worktree remove --force`；
5. 删除 registry 记录；
6. 删除该任务的 worktree 根目录。

删除操作只针对经过所有权验证的 CodeLens task worktree，不执行全局 Git worktree 清理，也不会操作用户原始 checkout。

清理失败不会把已经完成的 Review 改成失败。Worker 当前会捕获终态 worktree 清理异常，残留的 registry/worktree 由下次服务启动时的 recovery reconciliation 再次处理。

相关实现：

- [`backend/src/codelens/worker/execution.py`](../backend/src/codelens/worker/execution.py)
- [`backend/src/codelens/workspace/infrastructure/git_worktrees.py`](../backend/src/codelens/workspace/infrastructure/git_worktrees.py)

### 15.3 workspace overlay Artifact

Review 完成后，捕获 workspace changes 得到的 overlay Artifact 不会立即删除。ReviewTask 仍持有 `overlay_artifact_ref` 和 `overlay_hash`，使系统能够：

- 在 Worker 重启时重建相同 worktree 内容；
- 验证 Review 使用的未提交输入身份；
- 避免历史 Review 的冻结输入退化为对当前用户工作区的实时引用。

服务启动时，`list_input_artifact_references()` 会收集所有 durable ReviewTask 仍引用的 overlay Artifact；`prune_orphans()` 只删除不在该引用集合中的 Artifact 和未完成的 staging 文件。

### 15.4 Snapshot metadata Artifact

`SnapshotService.freeze()` 会为每次构造写入一个 Snapshot metadata Artifact，并将其放入运行时对象的 `snapshot_artifact` 字段。

当前实现没有把这个 Artifact reference 保存到 ReviewTask，也没有在 Review 进入终态时立即调用 `discard()`。因此，在进程继续运行期间，它可能暂时保留在 input Artifact namespace 中；服务下次启动执行 orphan pruning 时，由于没有 durable ReviewTask 引用它，会被识别为 orphan 并删除。

这与 overlay Artifact 的策略不同：overlay 是恢复 Review 冻结输入所需的 durable input，Snapshot metadata Artifact 当前只是一次 `freeze()` 的派生元数据。

### 15.5 Review 结果与执行证据

以下数据不会随着 task worktree 删除：

- ReviewTask 及其终态；
- frozen Review Plan；
- DAG checkpoints 和 coverage 状态；
- Candidate、Resolution、Verification 和已发布 Finding；
- Agent 输出 Artifact 与冻结执行规格；
- transcript 和 process report 数据；
- export history。

worktree 删除后，结果页和导出功能应继续完全依赖这些持久化投影，不得重新读取用户当前工作区来补充历史结果。

### 15.6 `superseded` 状态注意事项

仓储和 HTTP 层把 `superseded` 视为终态，但 Worker 当前用于 `_cleanup_terminal_worktree()` 的 `_TERMINAL_STATUSES` 集合只包含：

```python
{"completed", "partial", "failed", "canceled"}
```

因此，`superseded` Review 的 worktree 及时清理语义目前与其他终态不完全一致。实现后续应统一终态集合，并增加 supersede 后 registry 和 task worktree 都被清理的回归测试。在修复前，不能假设 superseded worktree 会沿普通终态路径立即删除。

## 16. 消费边界

`ReviewSnapshot` 的主要消费者包括：

- Context Builder：只为 Agent 生成有界的目标文件与规则输入；
- Filesystem Review Tools：按 Manifest 限制可读路径；
- Reviewer、Planner、Resolver、Verifier runtime：共享同一冻结证据范围；
- Finding validator：使用 `ChangeIndex` 校验位置与 changed hunk；
- source preview：从 pinned base/head 和目标路径生成可复现的源码视图；
- worktree cleanup：只清理通过所有权校验的任务 worktree。

Planner 可以提供 focus path 或 reason code，但不能扩大 Snapshot 的证据范围。Plugin、Skill、Prompt、模型输出和远程工具也不能在运行时向 Snapshot 增加路径或能力。

## 17. 核心不变量

实现和修改 `ReviewSnapshot` 相关逻辑时必须保持以下不变量：

1. 所有 Git ref 必须在任务创建前解析为完整 OID。
2. Agent 不得读取原始用户 checkout。
3. workspace overlay 必须以稳定读取方式捕获并经过 Artifact 哈希验证。
4. 所有可见路径必须规范化并限制在 worktree 内。
5. symlink 不得逃出 worktree。
6. target、context 和 instruction 的职责必须显式分离。
7. 排除路径必须保留原因，不能只从列表中静默消失。
8. Candidate Finding 必须落在冻结的 target changed hunk 内。
9. Snapshot 元数据不得包含普通日志不应传播的源码、Prompt、Transcript 或 Secret。
10. worktree 的恢复、验证和清理必须经过所有权边界。
11. Planner、Agent 或插件不能在运行时扩大 Manifest。
12. 重启恢复必须使用持久化的 OID、overlay、target paths 和执行状态，不重新解释用户的原始可变 ref。

概括而言，`ReviewSnapshot` 是一个由受控 worktree 承载、以 Manifest 和内容哈希封闭、以 ChangeIndex 限定证据范围，并可从持久化冻结输入重建的只读 Review 证据边界。
