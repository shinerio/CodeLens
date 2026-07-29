# CodeLens 插件生态设计文档

> 版本：0.3.0 | 日期：2026-07-29 | 状态：已实现

## 1. 文档目的

本文档补充描述插件系统的统一模型、触发机制、报告机制和交互接口；跨边界架构事实与稳定契约以 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 为准。

---

## 2. 架构总览

### 2.1 统一插件模型

CodeLens 插件生态采用**统一插件模型**。一个插件是一个独立安装单元，声明所属平台，并可选提供 **Trigger**（触发）和/或 **Report**（报告）能力：

```
  外部事件                         CodeLens 核心                        外部平台
  ┌──────────┐               ┌─────────────────────┐              ┌──────────┐
  │ Webhook  │──platform───▶ │                     │              │          │
  │ (MR/PR)  │               │  Plugin (platform)  │              │          │
  └──────────┘               │  ┌───────────────┐  │              │          │
                             │  │ Trigger 能力   │  │              │          │
  ┌──────────┐               │  │ (克隆仓库+     │  │              │          │
  │ Git Hook │──local──────▶ │  │  创建 review+  │  │              │          │
  │ (commit) │               │  │  注入上下文)    │  │              │          │
  └──────────┘               │  └───────┬───────┘  │              │          │
                             │          ▼          │              │          │
                             │  ┌──────────────┐   │              │          │
                             │  │ Review       │   │              │          │
                             │  │ Pipeline     │   │              │          │
                             │  │ (context透传) │   │              │          │
                             │  └──────┬───────┘   │              │          │
                             │         ▼           │              │          │
                             │  ┌──────────────┐   │              │          │
                             │  │ Report 能力   │   │              │          │
                             │  │ (按platform   │───┼──comments──▶│ GitHub   │
                             │  │  过滤+导出)    │   │              │ GitLab   │
                             │  └──────────────┘   │              │          │
                             └─────────────────────┘              └──────────┘
```

### 2.2 核心设计原则

| 原则 | 说明 |
|---|---|
| **统一插件模型** | 一个插件 = 一个安装单元，声明 platform，可选提供 trigger 和/或 report |
| **平台无关核心** | CodeLens 核心只透传 `external_context` dict，不感知具体平台 |
| **Trigger 填充上下文** | 触发能力负责注入平台信息（从 webhook payload、CLI 参数等获取） |
| **Report 消费上下文** | 报告能力负责读取平台信息，将 findings 发到对应平台 |
| **平台匹配路由** | Auto-export 按 `platform` 字段路由，GitHub 触发的 review 只走 GitHub 插件的 report |
| **插件解耦** | 插件不导入 CodeLens 代码，通过结构化类型（Protocol duck-typing）实现接口 |
| **通用术语** | 使用 `merge_request` 作为通用术语，兼容 MR（GitLab）和 PR（GitHub） |

### 2.3 平台定义

| 平台标识 | 说明 | Trigger 来源 | Report 目标 |
|---|---|---|---|
| `"local"` | 本地开发环境 | Git hook 脚本 | 本地文件导出 |
| `"github"` | GitHub | GitHub webhook | PR review comment |
| `"gitlab"` | GitLab / GitLab 私有部署 | GitLab webhook | MR discussion comment |

---

## 3. 插件模型

### 3.1 PluginManifest（插件清单）

每个插件在根目录提供 `plugin.json`，声明平台和能力：

```json
{
  "plugin_id": "github",
  "name": "GitHub Plugin",
  "version": "0.1.0",
  "description": "GitHub webhook trigger and PR review comment export",
  "author": "CodeLens Team",
  "platform": "github",
  "capabilities": {
    "trigger": {
      "trigger_type": "webhook",
      "supported_events": ["webhook"],
      "entry_point": "github_trigger:GitHubWebhookTrigger",
      "config_schema": { "type": "object", "properties": { "..." } }
    },
    "report": {
      "entry_point": "github_report:GitHubReportSink",
      "config_schema": { "type": "object", "properties": { "..." } }
    }
  },
  "min_codelens_version": null
}
```

**对应 Python 模型**：

```python
@dataclass(frozen=True)
class TriggerCapability:
    trigger_type: str                          # "local-hook" | "webhook"
    supported_events: tuple[str, ...]          # ("webhook",) 或 ("post-commit", "pre-push")
    entry_point: str                           # "module:ClassName"
    config_schema: dict = field(default_factory=dict)

@dataclass(frozen=True)
class ReportCapability:
    entry_point: str                           # "module:ClassName"
    config_schema: dict = field(default_factory=dict)

@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    description: str
    author: str
    platform: str                              # "local" | "github" | "gitlab"
    capabilities: dict[str, Any]               # {"trigger": TriggerCapability, "report": ReportCapability}
    min_codelens_version: str | None = None
```

### 3.2 PluginRecord（插件记录）

统一存储每个插件的安装状态和能力开关：

```python
@dataclass(frozen=True)
class PluginRecord:
    plugin_id: str
    manifest: PluginManifest
    is_builtin: bool
    install_path: str | None                   # None for built-in
    trigger_enabled: bool                       # trigger 能力是否启用
    report_enabled: bool                       # report 能力是否启用
    report_auto_export: bool                   # report 是否在 review 完成时自动导出
    trigger_config: dict = field(default_factory=dict)
    report_config: dict = field(default_factory=dict)
```

### 3.3 能力开关规则

| 场景 | trigger_enabled | report_enabled | 合法？ |
|---|---|---|---|
| 仅 trigger | `True` | `False` | ✅ |
| 仅 report | `False` | `True` | 见下方规则 |
| 两者都开 | `True` | `True` | ✅ |
| 两者都关 | `False` | `False` | ✅ |

**Built-in 插件**：trigger 和 report 可**独立开关**，无依赖约束。

**外置安装插件**：report 依赖 trigger。
- 插件清单中**必须同时声明** trigger 和 report 能力（不允许仅声明 report）
- 开启 report 时，必须 trigger 已启用（否则拒绝）
- 关闭 trigger 时，自动级联关闭 report
- 原因：外置插件的 report 需要 trigger 注入的 `external_context` 才能知道目标 MR/PR；没有 trigger 的插件无法获得路由信息

```python
def validate_capability_toggle(
    record: PluginRecord,
    enable_trigger: bool | None = None,
    enable_report: bool | None = None,
) -> None:
    """验证能力开关的合法性。"""
    if record.is_builtin:
        return  # built-in 无约束

    final_trigger = enable_trigger if enable_trigger is not None else record.trigger_enabled
    final_report = enable_report if enable_report is not None else record.report_enabled

    if final_report and not final_trigger:
        raise ValueError(
            f"External plugin '{record.plugin_id}': "
            "report capability requires trigger to be enabled"
        )
```

### 3.4 内置插件清单

| plugin_id | platform | trigger | report | 说明 |
|---|---|---|---|---|
| `local` | `local` | ✅ local-hook | ✅ file-export | 本地 Git hook + 本地文件导出 |

### 3.5 外置插件示例

| plugin_id | platform | trigger | report | 说明 |
|---|---|---|---|---|
| `github` | `github` | ✅ webhook | ✅ PR comment | GitHub 全功能插件 |
| `gitlab` | `gitlab` | ✅ webhook | ✅ MR comment | GitLab 全功能插件 |

---

## 4. external_context：外部平台上下文

### 4.1 概念

`external_context` 是一个**自由结构的 dict**，在 review 创建时注入，随 review 管道透传，最终到达插件的 report 能力。它携带了 review 的来源平台信息，使 report 知道应该将结果发布到哪个平台的哪个 MR/PR。

CodeLens 核心**不校验** `external_context` 的内部结构，只做 dict 透传。其内部 schema 是 trigger 和 report 之间的**约定**。

### 4.2 标准 Schema

```json
{
  "platform": "github",
  "project": "owner/repo",
  "merge_request": 42
}
```

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `platform` | `string` | 目标平台标识 | `"github"`, `"gitlab"`, `"local"` |
| `project` | `string` | 项目标识（平台特定格式） | `"owner/repo"`, `"group/subgroup/project"` |
| `merge_request` | `integer` | MR/PR 编号 | `42` |

**各平台格式**：

| 平台 | `platform` 值 | `project` 格式 | `merge_request` 含义 |
|---|---|---|---|
| GitHub | `"github"` | `owner/repo` | PR number |
| GitLab | `"gitlab"` | 项目路径或 ID | MR IID |
| local | 无 external_context | — | — |

### 4.3 数据流路径

```
创建阶段:
  CreateReviewRequest.external_context          (HTTP API 入参)
       │
       ▼
  CreateReviewCommand.external_context          (Application 层命令)
       │
       ▼
  ReviewTask.external_context                   (Domain 实体)
       │
       ▼
  review_tasks.external_context_json            (数据库 TEXT 列, JSON 序列化)

读取阶段:
  review_tasks.external_context_json            (数据库读取)
       │
       ▼
  ReviewRecord.external_context                 (Domain 记录)
       │
       ▼
  ReviewExportMeta.external_context             (Export 元数据)
       │
       ▼
  FindingExportEnvelope.review.external_context (插件 Report 能力接收)
```

### 4.4 涉及修改的 CodeLens 文件

| 层 | 文件路径 | 改动说明 |
|---|---|---|
| Interface | `interface/http/dto.py` | `CreateReviewRequest` 增加 `external_context: dict \| None = None` |
| Interface | `interface/http/routers/reviews.py` | `create_review()` 将 `external_context` 传入 `CreateReviewCommand` |
| Application | `review/application/commands.py` | `CreateReviewCommand` 增加 `external_context`；`CreateReviewHandler.handle()` 传递到 `ReviewTask.create()` |
| Domain | `review/domain/models.py` | `ReviewTask` 增加 `external_context: dict \| None = None`；`create()` 接受该参数 |
| Infrastructure | `review/infrastructure/tables.py` | `review_tasks` 表增加 `Column("external_context_json", Text)` |
| Infrastructure | `review/infrastructure/repositories.py` | `_review_record()` 反序列化 `external_context_json`；`create_with_job()` 序列化写入 |
| Domain | `review/domain/ports.py` | `ReviewRecord` 增加 `external_context: dict \| None = None` |
| Application | `review/application/export_findings.py` | `ReviewExportMeta` 增加 `external_context`；`_build_envelope_from_findings()` 包含该字段 |
| Trigger | `trigger/domain/ports.py` | `ReviewCreatorPort.create_review_from_trigger()` 增加 `external_context` 参数 |
| Trigger | `trigger/application/review_creator_adapter.py` | `ReviewCreatorAdapter` 将 `external_context` 传递到 `CreateReviewCommand` |

### 4.5 向后兼容

- `external_context` 为可选字段，默认 `None`
- 数据库列 `external_context_json` 为 nullable
- 现有 API 调用不传 `external_context` 时行为不变
- 现有内置插件（如 `local` 的 file-export）不读取该字段，不受影响

---

## 5. 平台匹配与路由

### 5.1 路由规则

`ExportOrchestrator.auto_export_if_enabled()` 对每个插件的 report 能力执行以下过滤：

```
对于每个 report_enabled + report_auto_export 的 plugin:
  review_platform = envelope.review.external_context?.platform ?? "local"
  1. 如果 plugin.manifest.platform == review_platform → 执行
  2. 否则 → 跳过
```

**核心逻辑**：review 的来源平台与插件的 platform **严格匹配**。

### 5.2 路由矩阵

| Review 来源 | external_context | review_platform | local (platform=local) | github (platform=github) | gitlab (platform=gitlab) |
|---|---|---|---|---|---|
| GitHub webhook | `{platform: "github", ...}` | `"github"` | ❌ 跳过 | ✅ 执行 | ❌ 跳过 |
| GitLab webhook | `{platform: "gitlab", ...}` | `"gitlab"` | ❌ 跳过 | ❌ 跳过 | ✅ 执行 |
| 本地 Git hook | `None` | `"local"` | ✅ 执行 | ❌ 跳过 | ❌ 跳过 |
| 手动 API（指定 github） | `{platform: "github", ...}` | `"github"` | ❌ 跳过 | ✅ 执行 | ❌ 跳过 |

**规则解释**：
- 每个 review **只路由到与其来源平台匹配的插件**
- 本地 Git hook 触发的 review（无 external_context）视为 `platform="local"`，仅执行 local 插件
- GitHub webhook 触发的 review 仅执行 github 插件，不执行 local 或 gitlab 插件
- 这种严格匹配确保 review 结果只发布到触发它的平台

### 5.3 涉及修改的 CodeLens 文件

| 文件路径 | 改动说明 |
|---|---|
| `plugin/application/export_orchestrator.py` | `auto_export_if_enabled()` 按来源平台路由 |

---

## 6. Trigger 能力（触发）

### 6.1 概述

插件的 Trigger 能力负责监听外部事件并自动创建 review。支持两种触发模式：

| 模式 | trigger_type | 事件来源 | 仓库状态 | 典型场景 |
|---|---|---|---|---|
| 本地钩子 | `local-hook` | Git hook 脚本 | 本地已有仓库 | 开发者本地 commit/push |
| Webhook | `webhook` | HTTP webhook | 需要克隆/更新 | 远端 MR/PR 更新 |

### 6.2 本地钩子模式（local-hook）

#### 6.2.1 工作原理

```
开发者 git commit
       │
       ▼
.git/hooks/post-commit (hook 脚本)
       │
       ▼ curl POST /api/trigger-events
CodeLens TriggerOrchestrator
       │
       ▼ 按 repository_path 匹配 platform=local 的插件
LocalHookTriggerAdapter.handle_event()
       │
       ▼ 防抖 + 事件过滤
ReviewCreatorAdapter.create_review_from_trigger()
       │
       ▼
CreateReviewHandler → ReviewTask (external_context = None)
```

#### 6.2.2 事件分发机制

`POST /api/trigger-events` 接收本地钩子事件：

```json
{
  "event": "post-commit",
  "repository_path": "/path/to/repo",
  "commit_sha": "abc123"
}
```

`TriggerOrchestrator.handle_event()` 分发逻辑：
1. 查询所有 `trigger_enabled` 且 `trigger_type == "local-hook"` 的插件
2. 按 `trigger_config.repository_paths` 包含 `repository_path` 过滤
3. 对每个匹配插件调用 `handle_event()`

#### 6.2.3 内置 local 插件的 Trigger 能力

- `HookInstaller` 管理 `.git/hooks/` 脚本（非破坏性注入）
- 支持 `post-commit` 和 `pre-push` 事件
- 内存防抖（`debounce_seconds`）
- 不注入 `external_context`（本地触发无平台信息）

### 6.3 Webhook 模式

#### 6.3.1 工作原理

```
远端平台 MR/PR 更新
       │
       ▼ HTTP POST webhook
CodeLens POST /api/webhooks/{platform}
       │
       ▼ 按 platform 匹配插件
WebhookTriggerPlugin.handle_event()
       │
       ▼ 解析 payload (project, MR/PR, branches)
       │
       ▼ 克隆/更新本地仓库
RepoManager.ensure_repo(project, source_branch)
       │
       ▼
ReviewCreatorAdapter.create_review_from_trigger(
    repository_path=clone_dir,
    scope_type="branch",
    scope_params={"base_ref": target_branch, "target_ref": source_branch},
    external_context={
        "platform": "github",
        "project": "owner/repo",
        "merge_request": 42
    }
)
```

#### 6.3.2 Webhook 事件接收端点

**新增端点** `POST /api/webhooks/{platform}`：

**GitHub 示例**：
```
POST /api/webhooks/github
Content-Type: application/json
X-Hub-Signature-256: sha256=<signature>

{
  "action": "opened",
  "pull_request": {
    "number": 42,
    "head": { "ref": "feature/xxx", "sha": "abc123" },
    "base": { "ref": "main", "sha": "def456" }
  },
  "repository": {
    "full_name": "owner/repo",
    "clone_url": "https://github.com/owner/repo.git"
  }
}
```

**GitLab 示例**：
```
POST /api/webhooks/gitlab
Content-Type: application/json
X-Gitlab-Token: <token>

{
  "object_kind": "merge_request",
  "object_attributes": {
    "iid": 123,
    "source_branch": "feature/xxx",
    "target_branch": "main",
    "action": "update",
    "state": "opened"
  },
  "project": {
    "path_with_namespace": "group/subgroup/project",
    "git_ssh_url": "git@gitlab.com:group/subgroup/project.git"
  }
}
```

**分发逻辑**：
1. 接收 `POST /api/webhooks/{platform}` + payload + headers
2. 查询所有 `trigger_enabled` 且 `manifest.platform == {platform}` 的插件
3. 调用每个匹配插件的 `handle_event()`，传入 webhook payload

**与本地钩子分发的区别**：

| 维度 | 本地钩子 | Webhook |
|---|---|---|
| 端点 | `POST /api/trigger-events` | `POST /api/webhooks/{platform}` |
| 过滤依据 | `repository_path` | `platform` |
| 事件类型 | `HookEvent.POST_COMMIT / PRE_PUSH` | `HookEvent.WEBHOOK` |
| 仓库状态 | 本地已有 | 需要克隆/更新 |
| external_context | 无（`None`） | 有（从 payload 提取） |

#### 6.3.3 Webhook 签名验证

各平台使用不同的签名机制，由对应插件的 trigger 能力自行验证：

| 平台 | 签名头 | 算法 |
|---|---|---|
| GitHub | `X-Hub-Signature-256` | HMAC-SHA256 |
| GitLab | `X-Gitlab-Token` | 简单 token 比对 |

签名密钥通过插件的 `trigger_config.webhook_secret` 配置。

#### 6.3.4 HookEvent 扩展

```python
class HookEvent(StrEnum):
    POST_COMMIT = "post-commit"
    PRE_PUSH = "pre-push"
    WEBHOOK = "webhook"              # 新增
```

### 6.4 Webhook Trigger 的仓库管理

#### 6.4.1 问题

Webhook 触发时，CodeLens 服务器本地没有目标仓库的克隆。需要先克隆仓库才能构建 review 上下文（scope planning、snapshot、diff 计算等）。

#### 6.4.2 仓库管理策略

```
{plugin_data_dir}/
  └── repos/
      └── {project_path_hash}/       # 每个项目一个目录
          └── .git/                  # 完整 git 仓库
```

| 操作 | 触发条件 | 行为 |
|---|---|---|
| 首次克隆 | 目录不存在 | `git clone <repo_url> <clone_dir>` |
| 后续更新 | 目录已存在 | `git fetch origin` + `git checkout <branch>` + `git reset --hard origin/<branch>` |
| 清理 | 用户手动触发 | `rm -rf repos/` 或 `rm -rf repos/{project_path_hash}/` |

#### 6.4.3 设计决策

| 决策 | 理由 |
|---|---|
| **持久化克隆，不自动删除** | 避免每次 review 都重新 clone，减少网络和时间开销 |
| **git fetch + reset 代替 pull** | 确保本地分支与远端完全一致，避免 merge conflict |
| **按项目哈希分目录** | 支持同一插件管理多个项目的仓库 |
| **清理功能放插件管理页面** | 每个插件独立管理自己的仓库，提供单独的清理按钮 |

#### 6.4.4 RepoManager 接口

```python
class RepoManager:
    """管理 webhook trigger 的本地仓库克隆。"""

    def __init__(self, repos_dir: Path):
        self._repos_dir = repos_dir

    async def ensure_repo(
        self,
        repo_url: str,
        project_path: str,
        branch: str,
    ) -> Path:
        """确保本地仓库存在并更新到指定分支，返回仓库路径。

        首次调用：git clone
        后续调用：git fetch + git checkout + git reset --hard
        """
        ...

    async def cleanup(self, project_path: str | None = None) -> None:
        """清理仓库。project_path=None 时清理所有仓库。"""
        ...

    def get_repo_path(self, project_path: str) -> Path | None:
        """返回仓库路径，不存在时返回 None。"""
        ...
```

### 6.5 Trigger 能力的外部加载

当前 `BuiltinTriggerPluginLoader` 只支持内置的 `local` 插件。外置插件的 trigger 能力需要支持从 `install_path` 动态加载。

#### CompositeTriggerPluginLoader

```python
class CompositeTriggerPluginLoader(TriggerPluginLoaderPort):
    """组合加载器：内置插件 + importlib 外部插件。"""

    def __init__(self, builtin_loader: BuiltinTriggerPluginLoader):
        self._builtin = builtin_loader
        self._external_cache: dict[str, TriggerSinkPort] = {}

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
        *,
        manifest: PluginManifest | None = None,
        install_path: Path | None = None,
    ) -> TriggerSinkPort:
        # 1. 尝试内置加载器
        try:
            return self._builtin.load_plugin(plugin_id, review_creator)
        except ValueError:
            pass

        # 2. 从 install_path 加载外部插件（importlib）
        if manifest and install_path:
            return self._load_external(manifest, install_path, review_creator)

        raise ValueError(f"Unsupported trigger plugin: {plugin_id}")
```

加载机制与 Report 能力的 `ImportlibPluginLoader` 相同：
1. 解析 trigger 的 `entry_point` 为 `"module_name:ClassName"`
2. 使用 `importlib.util.spec_from_file_location()` 加载模块
3. 实例化类并验证 `TriggerSinkPort` 协议

### 6.6 Trigger 配置 Schema

**local 平台（local-hook）**：

```json
{
  "type": "object",
  "properties": {
    "repository_paths": {
      "type": "array", "items": { "type": "string" },
      "description": "要监控的本地仓库路径"
    },
    "events": {
      "type": "array", "items": { "enum": ["post-commit", "pre-push"] },
      "description": "触发 review 的 git hook 事件"
    },
    "scope_type": { "enum": ["commit", "branch", "uncommitted"] },
    "base_ref": { "type": "string" },
    "target_ref": { "type": "string" },
    "selected_agents": { "type": "array", "items": { "type": "string" } },
    "prompt_locale": { "type": "string", "default": "en" },
    "debounce_seconds": { "type": "integer", "default": 10 }
  },
  "required": ["repository_paths", "events", "selected_agents"]
}
```

**github / gitlab 平台（webhook）**：

```json
{
  "type": "object",
  "properties": {
    "clone_dir": {
      "type": "string",
      "description": "仓库克隆目录，默认为 {plugin_data_dir}/repos"
    },
    "selected_agents": { "type": "array", "items": { "type": "string" } },
    "prompt_locale": { "type": "string", "default": "en" },
    "webhook_secret": { "type": "string", "description": "Webhook 签名验证密钥" },
    "debounce_seconds": { "type": "integer", "default": 30 }
  },
  "required": ["selected_agents"]
}
```

### 6.7 仓库清理 API

**新增端点** `DELETE /api/plugins/{plugin_id}/repos`：

```
DELETE /api/plugins/github/repos
DELETE /api/plugins/github/repos?project=owner/repo
```

- 无 `project` 参数：清理该插件的所有克隆仓库
- 有 `project` 参数：清理指定项目的仓库

---

## 7. Report 能力（报告）

### 7.1 概述

插件的 Report 能力接收完成的 review findings，导出到外部目标。每个 Report 实现 `ReportSinkPort` 协议。

### 7.2 ReportSinkPort 协议

```python
class ReportSinkPort(Protocol):
    @property
    def sink_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    async def export(
        self,
        envelope: FindingExportEnvelope,
        config: dict,
        repository_path: Path,
    ) -> ExportResult: ...
```

**约束**：
- 实现不得抛出异常；所有错误必须捕获并返回 `ExportResult(success=False, error=...)`
- 插件不导入 CodeLens 代码，通过结构化类型满足协议
- `envelope` 中的 `external_context` 通过属性访问（duck-typing）

### 7.3 插件解耦机制

Report 能力作为插件的一部分，**不导入任何 CodeLens 模块**。解耦通过以下机制实现：

1. **结构化类型**：Python Protocol 的 duck-typing，只需实现 `sink_id`、`display_name`、`export`
2. **本地 ExportResult 镜像**：插件定义与 CodeLens `ExportResult` 字段完全一致的本地 dataclass
3. **属性访问**：`envelope.review.external_context` 等字段通过属性访问获取
4. **importlib 加载**：`ImportlibPluginLoader` 通过 `spec_from_file_location()` 加载，检查 `hasattr(sink, "sink_id")` 和 `hasattr(sink, "export")`

### 7.4 触发方式

| 方式 | 触发时机 | 说明 |
|---|---|---|
| **自动导出** | Review 到达终态 | `report_auto_export=true` 时，`ExportOrchestrator` 自动执行（受平台路由过滤） |
| **手动导出** | 用户通过 API 触发 | `POST /api/reviews/{task_id}/export` + `{"plugin_id": "github"}` |

### 7.5 错误处理

| 错误类型 | 处理策略 |
|---|---|
| **Pre-flight**（CLI 不存在、无 external_context） | 立即返回失败 `ExportResult`，不尝试发布 |
| **单条 finding 失败**（认证过期、行号无效） | 记录错误，继续处理后续 finding |
| **全部失败** | `ExportResult.success=False`，`error` 包含所有失败详情 |

### 7.6 评论内容格式

每条 Finding 格式化为 Markdown 行级评论：

```markdown
**[MAJOR] 空指针引用风险**

**Impact:** 可能导致运行时崩溃
**Explanation:** 变量 `config` 在使用前未做空值检查...
**Recommendation:** 添加空值判断或使用 Optional 类型...

---
*CodeLens Finding `f_abc123` | Category: null_safety | Confidence: 92% | Disposition: blocking*
```

---

## 8. 完整数据流

### 8.1 Webhook → Review → Export 端到端流程

```
① GitHub PR 更新
   │
   ▼ POST /api/webhooks/github
② Webhook 端点接收
   │ 验证签名（X-Hub-Signature-256）
   │ 按 platform="github" 匹配插件
   ▼
③ github 插件的 Trigger 能力
   │ 解析 payload → project, pr_number, source_branch, target_branch
   │ RepoManager.ensure_repo() → 克隆/更新本地仓库
   ▼
④ ReviewCreatorAdapter.create_review_from_trigger()
   │ repository_path = /data/repos/{hash}/
   │ scope_type = "branch"
   │ scope_params = {base_ref: "main", target_ref: "feature/xxx"}
   │ external_context = {platform: "github", project: "owner/repo", merge_request: 42}
   ▼
⑤ CreateReviewHandler.handle()
   │ ScopePlanner.plan() → target_paths, base_oid, head_oid
   │ ReviewTask.create(..., external_context={...})
   │ SqlReviewStore.create_with_job() → 持久化
   ▼
⑥ Review Pipeline (Worker)
   │ PROVISIONING_WORKTREE → SNAPSHOTTING → PREPARING
   │ → REVIEWING → VALIDATING → SYNTHESIZING → COMPLETED
   ▼
⑦ SqlReviewStore 终态钩子
   │ _terminal_hook(task_id, "completed")
   ▼
⑧ ExportOrchestrator.auto_export_if_enabled()
   │ 构建 FindingExportEnvelope (含 external_context)
   │ 平台路由：
   │   local 插件 → ✅ 执行（始终）
   │   github 插件 → ✅ 执行（platform 匹配）
   │   gitlab 插件 → ❌ 跳过（platform 不匹配）
   ▼
⑨ github 插件的 Report 能力
   │ 读取 envelope.review.external_context
   │ 提取 project + merge_request
   │ 遍历 findings → gh pr review / GitHub API
   ▼
⑩ GitHub PR 上出现行级 review comment
```

### 8.2 本地 Git Hook → Review → Export 流程

```
① 开发者 git commit
   │
   ▼ .git/hooks/post-commit
② Hook 脚本 POST /api/trigger-events
   │ {event: "post-commit", repository_path: "/path/to/repo", commit_sha: "abc"}
   ▼
③ TriggerOrchestrator.handle_event()
   │ 按 repository_path 匹配 platform=local 的插件
   ▼
④ local 插件的 Trigger 能力
   │ 防抖检查
   │ scope_params = {base_commit: "abc~1", target_ref: "abc"}
   │ external_context = None  ← 本地触发无平台信息
   ▼
⑤ Review Pipeline → COMPLETED
   ▼
⑥ ExportOrchestrator.auto_export_if_enabled()
   │ external_context = None
   │ 平台路由：仅 local 插件执行
   ▼
⑦ local 插件的 Report 能力：写入本地文件（CodeLensReview/）
```

---

## 9. CodeLens 核心修改

### 9.1 统一插件存储

插件状态统一存储到 `data/plugins.json`，由 `FilesystemPluginStore` 持久化统一的 `PluginRecord`。

| 文件路径 | 改动说明 |
|---|---|
| `plugin/domain/models.py` | 统一 `PluginManifest`、`PluginRecord` 与能力错误 |
| `plugin/domain/ports.py` | 定义存储、安装、加载、触发和报告 Port |
| `plugin/infrastructure/plugin_store.py` | 原子、串行地持久化 `data/plugins.json` |

### 9.2 统一插件管理 API

插件管理统一使用 `/api/plugins`：

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/plugins` | GET | 列出所有插件 |
| `/api/plugins/install` | POST | 从 Git 安装插件 |
| `/api/plugins/{plugin_id}` | DELETE | 卸载插件 |
| `/api/plugins/{plugin_id}/trigger/enable` | PUT | 启用 trigger 能力 |
| `/api/plugins/{plugin_id}/trigger/disable` | PUT | 禁用 trigger 能力 |
| `/api/plugins/{plugin_id}/report/enable` | PUT | 启用 report 能力（外置插件需 trigger 已启用） |
| `/api/plugins/{plugin_id}/report/disable` | PUT | 禁用 report 能力 |
| `/api/plugins/{plugin_id}/trigger/config` | PUT | 更新 trigger 配置 |
| `/api/plugins/{plugin_id}/report/config` | PUT | 更新 report 配置 |
| `/api/plugins/{plugin_id}/report/auto-export` | PUT | 开关 report 自动导出 |
| `/api/plugins/{plugin_id}/trigger/install-hooks` | POST | 安装或重装已配置 Git Hook |
| `/api/plugins/{plugin_id}/trigger/uninstall-hooks` | POST | 移除 CodeLens Git Hook 片段 |
| `/api/plugins/{plugin_id}/trigger/hook-status` | GET | 查询实际 Git Hook 状态 |

### 9.3 Webhook 端点

新增 `POST /api/webhooks/{platform}`：

| 文件路径 | 改动说明 |
|---|---|
| `interface/http/routers/webhooks.py` | **新增** webhook 接收端点 |
| `interface/http/routers/__init__.py` | 注册 webhook router |

### 9.4 Trigger 外部加载

| 文件路径 | 改动说明 |
|---|---|
| `plugin/infrastructure/plugin_loader.py` | `CompositePluginLoader` 统一加载内置与外部 Trigger/Report 实现 |

---

## 10. 交互接口汇总

### 10.1 HTTP API 总览

| 端点 | 方法 | 说明 |
|---|---|---|
| `/api/reviews` | POST | 创建 review（支持 `external_context`） |
| `/api/reviews/{task_id}/export` | POST | 手动触发插件 report 导出 |
| `/api/trigger-events` | POST | 接收本地 Git Hook 事件 |
| `/api/webhooks/{platform}` | POST | 接收远端平台 webhook |
| `/api/plugins` | GET | 列出所有插件 |
| `/api/plugins/install` | POST | 安装插件 |
| `/api/plugins/{id}/trigger/*` | PUT/POST/GET | Trigger 能力、Hook 与状态管理 |
| `/api/plugins/{id}/report/*` | PUT | Report 能力与自动导出管理 |

### 10.2 ReviewCreatorPort 扩展

```python
class ReviewCreatorPort(Protocol):
    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        selected_agents: tuple[str, ...],
        prompt_locale: str,
        external_context: dict | None = None,   # 新增
    ) -> str:
        ...
```

### 10.3 FindingExportEnvelope 扩展

```python
@dataclass(frozen=True)
class ReviewExportMeta:
    task_id: str
    repository_name: str
    scope_type: str
    base_oid: str
    head_oid: str
    selected_agent_versions: tuple[str, ...]
    status: str
    created_at: datetime
    external_context: dict | None = None        # 新增
```

---

## 11. 插件项目结构

每个平台插件是一个独立项目，同时包含 trigger 和 report 实现：

```
CodeLens-GitHub-Plugin/
├── plugin.json                    # 统一清单（platform: "github"）
├── github_trigger.py              # TriggerSinkPort 实现（webhook 处理）
├── github_report.py               # ReportSinkPort 实现（PR comment）
├── repo_manager.py                # 仓库克隆/更新/清理
└── tests/
    ├── test_trigger.py
    ├── test_report.py
    └── test_repo_manager.py

CodeLens-GitLab-Plugin/
├── plugin.json                    # 统一清单（platform: "gitlab"）
├── gitlab_trigger.py              # TriggerSinkPort 实现（webhook 处理）
├── gitlab_report.py               # ReportSinkPort 实现（MR comment）
├── repo_manager.py                # 仓库克隆/更新/清理
└── tests/
```

### plugin.json 示例（GitHub）

```json
{
  "plugin_id": "github",
  "name": "GitHub Plugin",
  "version": "0.1.0",
  "description": "GitHub webhook trigger and PR review comment export",
  "author": "CodeLens Team",
  "platform": "github",
  "capabilities": {
    "trigger": {
      "trigger_type": "webhook",
      "supported_events": ["webhook"],
      "entry_point": "github_trigger:GitHubWebhookTrigger",
      "config_schema": {
        "type": "object",
        "properties": {
          "clone_dir": { "type": "string" },
          "selected_agents": { "type": "array", "items": { "type": "string" } },
          "prompt_locale": { "type": "string", "default": "en" },
          "webhook_secret": { "type": "string" },
          "debounce_seconds": { "type": "integer", "default": 30 }
        },
        "required": ["selected_agents"]
      }
    },
    "report": {
      "entry_point": "github_report:GitHubReportSink",
      "config_schema": {
        "type": "object",
        "properties": {
          "gh_binary": { "type": "string", "default": "gh" }
        }
      }
    }
  }
}
```

---

## 12. 平台兼容性

### 12.1 扩展新平台

添加新平台支持只需实现一个插件项目，包含：
1. **Trigger 能力**：处理该平台的 webhook，注入 `external_context`
2. **Report 能力**：读取 `external_context`，调用该平台的 API 发布评论

**无需修改 CodeLens 核心**。

### 12.2 各平台适配要点

| 平台 | Trigger | Report | CLI/API |
|---|---|---|---|
| local | Git hook 脚本 | 本地文件导出（CodeLensReview/） | 文件系统 |
| GitHub | GitHub webhook payload | `gh pr review` / GitHub REST API | `gh` CLI |
| GitLab | GitLab webhook payload | GitLab Discussions API / `glab` CLI | REST API |

### 12.3 external_context 各平台示例

**GitHub**：
```json
{"platform": "github", "project": "owner/repo", "merge_request": 42}
```

**GitLab**：
```json
{"platform": "gitlab", "project": "group/subgroup/project", "merge_request": 123}
```

---

## 13. 安全考量

| 风险 | 缓解措施 |
|---|---|
| Webhook 伪造 | 签名验证（HMAC-SHA256 / Token），密钥通过 `trigger_config.webhook_secret` 配置 |
| 仓库克隆路径穿越 | `RepoManager` 使用 `project_path_hash` 作为目录名，不接受用户指定路径 |
| CLI 凭证泄露 | 认证由各平台 CLI 自行管理（keychain / env），插件不接触 token |
| 评论内容注入 | Finding 内容经过 Markdown 转义，通过 stdin 传递避免 shell 注入 |

---

## 14. 实现计划

| 阶段 | 内容 | 涉及项目 |
|---|---|---|
| **Phase 1** | CodeLens 核心：统一插件模型 + 存储 + API | CodeLens |
| **Phase 2** | CodeLens 核心：external_context 透传 + 平台路由 | CodeLens |
| **Phase 3** | CodeLens 核心：webhook 端点 + trigger 外部加载 | CodeLens |
| **Phase 4** | GitHub 插件：trigger + report + repo_manager | CodeLens-GitHub-Plugin |
| **Phase 5** | GitLab 插件：trigger + report + repo_manager | CodeLens-GitLab-Plugin |
| **Phase 6** | 集成测试 + 端到端验证 | 全部 |
