# CodeLens 插件生态设计文档

> 版本：0.4.0 | 日期：2026-07-31 | 状态：已实现 + 接入指南

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
  "min_codelens_version": null,
  "name_i18n": { "zh-CN": "GitHub 插件" },
  "description_i18n": { "zh-CN": "GitHub webhook 触发审查，将结果发布为 PR 评论" }
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
    platform: str                              # "local" | "github" | "gitlab" | ...
    capabilities: dict[str, Any]               # {"trigger": TriggerCapability, "report": ReportCapability}
    min_codelens_version: str | None = None
    name_i18n: dict[str, str] = field(default_factory=dict)           # locale → 翻译名称
    description_i18n: dict[str, str] = field(default_factory=dict)    # locale → 翻译描述
    plugin_api_version: PluginApiVersion = PluginApiVersion.V1        # "1" | "2"
```

`plugin_api_version` 未声明时默认为 `PluginApiVersion.V1`（即 `"1"`）。v2 插件必须在 `plugin.json` 中显式声明 `"plugin_api_version": "2"`，详见 [插件 API v2 升级指南](./plugin-upgradev2.md)。

**国际化字段说明**：

| 字段 | 位置 | 作用 |
|---|---|---|
| `name_i18n` | `plugin.json` 顶层 | 覆盖 `name`，前端按用户 locale 匹配显示 |
| `description_i18n` | `plugin.json` 顶层 | 覆盖 `description`，前端按用户 locale 匹配显示 |
| `description_i18n` | `config_schema` 每个属性内 | 覆盖属性的 `description`，前端配置表单按 locale 显示 |

示例（config_schema 属性级 i18n）：

```json
{
  "debounce_seconds": {
    "type": "integer",
    "default": 60,
    "description": "Minimum seconds between triggers for the same MR/branch",
    "description_i18n": {
      "zh-CN": "同一 MR/分支触发评审的最小间隔秒数"
    }
  }
}
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
    git_url: str | None = None                 # 安装来源 Git URL（外置插件）
    git_ref: str | None = None                 # 安装来源 Git ref（外置插件）
    config_revision: int = 1                   # 配置版本号，每次配置更新递增
    profile_source: PluginProfileSource | None = None  # 配置来源 Profile 元数据
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
policy = TriggerReviewPolicy.from_config(config)

ReviewCreatorAdapter.create_review_from_trigger(
    repository_path=clone_dir,
    scope_type="branch",
    scope_params={"base_ref": target_branch, "target_ref": source_branch},
    review_policy=policy,
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

#### CompositePluginLoader

```python
class CompositePluginLoader:
    """组合加载器：内置插件 + importlib 外部插件。同时处理 Trigger 和 Report。"""

    def load_plugin(
        self,
        plugin_id: str,
        review_creator: ReviewCreatorPort,
        *,
        manifest: PluginManifest | None = None,
        install_path: Path | None = None,
    ) -> TriggerSinkPort:
        ...

    def load_sink(
        self,
        plugin_id: str,
        *,
        manifest: PluginManifest | None = None,
        install_path: Path | None = None,
    ) -> ReportSinkPort:
        ...
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
    "base_ref": { "type": ["string", "null"] },
    "target_ref": { "type": ["string", "null"] },
    "reviewer_selection": {
      "type": "object",
      "description": "Reviewer 选择策略（fixed 或 adaptive）"
    },
    "supersede_policy": {
      "type": "string",
      "enum": ["latest_snapshot", "preserve_all"],
      "default": "latest_snapshot"
    },
    "prompt_locale": { "type": "string", "enum": ["en", "zh-CN"], "default": "en" },
    "debounce_seconds": { "type": "integer", "minimum": 0, "default": 10 }
  },
  "required": ["repository_paths", "events", "reviewer_selection", "supersede_policy", "prompt_locale", "debounce_seconds"]
}
```

`reviewer_selection` 的详细结构和校验规则参见 [插件 API v2 升级指南](./plugin-upgradev2.md) §5。

**github / gitlab 平台（webhook）**：

```json
{
  "type": "object",
  "properties": {
    "clone_dir": {
      "type": "string",
      "description": "仓库克隆目录，默认为 {plugin_data_dir}/repos"
    },
    "reviewer_selection": {
      "type": "object",
      "description": "Reviewer 选择策略（fixed 或 adaptive）"
    },
    "supersede_policy": {
      "type": "string",
      "enum": ["latest_snapshot", "preserve_all"],
      "default": "latest_snapshot"
    },
    "prompt_locale": { "type": "string", "enum": ["en", "zh-CN"], "default": "en" },
    "webhook_secret": { "type": "string", "description": "Webhook 签名验证密钥" },
    "debounce_seconds": { "type": "integer", "minimum": 0, "default": 30 }
  },
  "required": ["reviewer_selection", "supersede_policy", "prompt_locale"]
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
        envelope: FindingExportEnvelopeV2,     # v2 Envelope（schema_version: "2.0"）
        config: Mapping[str, object],
        repository_path: Path,
    ) -> ExportResult: ...
```

**约束**：
- 实现不得抛出异常；所有错误必须捕获并返回 `ExportResult(success=False, error=...)`
- 插件不导入 CodeLens 代码，通过结构化类型满足协议
- `envelope` 中的 `external_context` 通过属性访问（duck-typing）

### 7.3 FindingExportEnvelope 数据结构

Report 插件接收的 `envelope` 参数结构如下（插件通过属性访问获取字段，不需要导入这些类型）：

```python
@dataclass(frozen=True)
class FindingExportEnvelopeV2:
    schema_version: Literal["2.0"]               # 导出结构版本号
    exported_at: datetime                        # 导出时间
    review: ReviewExportMetaV2                   # Review 元数据
    findings: tuple[FindingExportItem, ...]      # 发现项列表

@dataclass(frozen=True)
class ReviewExportMetaV2:
    task_id: str                                 # Review 任务 ID
    repository_name: str                         # 仓库名称
    scope_type: str                              # "commit" | "branch" | "uncommitted"
    base_oid: str                                # 基准 commit SHA
    head_oid: str                                # 目标 commit SHA
    base_ref: str | None                         # 基准分支 ref
    target_ref: str | None                       # 目标分支 ref
    status: Literal["completed", "partial"]      # V2 Envelope 只有两种终态
    selection_request: SelectionRequestDto       # 请求的 Reviewer 选择模式
    plan_summary: ReviewPlanSummaryDto           # Plan 执行摘要
    coverage: ReviewCoverageDto                  # Reviewer 覆盖情况
    created_at: datetime                         # 创建时间
    external_context: dict | None = None         # 平台上下文（用于路由）

@dataclass(frozen=True)
class SelectionRequestDto:
    mode: Literal["fixed", "adaptive"]           # 选择模式
    reviewer_versions: tuple[str, ...] = ()      # Fixed 模式下的 Reviewer 版本列表

@dataclass(frozen=True)
class ReviewPlanSummaryDto:
    strategy: Literal["fixed", "adaptive"]       # 实际执行策略
    selected_reviewer_versions: tuple[str, ...]  # 实际执行的 Reviewer 版本
    planner_version: str | None                  # Planner 版本
    plan_hash: str                               # Plan 哈希

@dataclass(frozen=True)
class ReviewCoverageDto:
    completed_reviewer_versions: tuple[str, ...] # 成功完成的 Reviewer
    failed_reviewer_versions: tuple[str, ...]    # 执行失败的 Reviewer
    omitted_reviewer_versions: tuple[str, ...]   # 被省略的 Reviewer

@dataclass(frozen=True)
class FindingExportItem:
    finding_id: str                              # 发现项 ID
    fingerprint: str                             # 唯一指纹
    reviewer_id: str                             # 审查 Agent ID
    category: str                                # 类别（如 "null_safety"）
    title: str                                   # 标题
    severity: str                                # "critical" | "high" | "medium" | "low"
    disposition: str                             # "blocking" | "non_blocking"
    confidence: float                            # 置信度 0.0-1.0
    change_origin: str                           # 变更来源
    changed_hunk_id: str | None                  # 变更块 ID
    primary_location: SourceLocation             # 主要位置（file, line, column）
    related_locations: tuple[SourceLocation, ...]# 关联位置
    evidence: tuple                              # 证据
    impact: str                                  # 影响描述
    explanation: str                             # 详细说明
    reproduction: str | None                     # 复现步骤
    recommendation: str                          # 修复建议
    rule_sources: tuple[RuleReference, ...]      # 规则来源
    source_excerpt: SourceSnippet                # 源代码片段
```

**Report 插件常用字段**：

| 字段路径 | 用途 | 示例 |
|---|---|---|
| `envelope.review.task_id` | 关联 Review 任务 | `"review_abc123"` |
| `envelope.review.external_context` | 获取平台路由信息 | `{"platform": "github", "project": "owner/repo", "merge_request": 42}` |
| `envelope.review.head_oid` | 获取目标 commit SHA | `"abc123def"` |
| `envelope.findings` | 遍历所有发现项 | `for finding in envelope.findings:` |
| `finding.severity` | 严重级别映射 | `"high"` → 平台对应的严重级别 |
| `finding.title` | 评论标题 | `"空指针引用风险"` |
| `finding.explanation` | 评论正文 | `"变量 config 在使用前未做空值检查..."` |
| `finding.recommendation` | 修复建议 | `"添加空值判断或使用 Optional 类型..."` |
| `finding.primary_location` | 行级评论位置 | `SourceLocation(file="main.py", line=42, column=5)` |

### 7.4 ExportResult 返回格式

Report 插件的 `export()` 方法必须返回一个与 `ExportResult` 字段完全一致的 dict（无需导入 CodeLens 类型）：

```python
{
    "plugin_id": "github",                       # 插件 ID，与 manifest.plugin_id 一致
    "task_id": "review_abc123",                  # Review 任务 ID
    "success": True,                             # 是否成功
    "output_path": None,                         # 输出路径（本地导出用，远端发布填 None）
    "error": None,                               # 错误信息（失败时填写）
    "exported_at": "2026-07-31T10:30:00+08:00"   # ISO 8601 格式时间戳
}
```

**插件示例**：

```python
return {
    "plugin_id": self.sink_id,
    "task_id": envelope.review.task_id,
    "success": True,
    "output_path": None,
    "error": None,
    "exported_at": datetime.now().isoformat(),
}
```

### 7.5 插件解耦机制

Report 能力作为插件的一部分，**不导入任何 CodeLens 模块**。解耦通过以下机制实现：

1. **结构化类型**：Python Protocol 的 duck-typing，只需实现 `sink_id`、`display_name`、`export`
2. **本地 ExportResult 镜像**：插件定义与 CodeLens `ExportResult` 字段完全一致的本地 dataclass
3. **属性访问**：`envelope.review.external_context` 等字段通过属性访问获取
4. **importlib 加载**：`ImportlibPluginLoader` 通过 `spec_from_file_location()` 加载，检查 `hasattr(sink, "sink_id")` 和 `hasattr(sink, "export")`

### 7.6 触发方式

| 方式 | 触发时机 | 说明 |
|---|---|---|
| **自动导出** | Review 到达终态 | `report_auto_export=true` 时，`ExportOrchestrator` 自动执行（受平台路由过滤） |
| **手动导出** | 用户通过 API 触发 | `POST /api/reviews/{task_id}/export` + `{"plugin_id": "github"}` |

### 7.7 错误处理

| 错误类型 | 处理策略 |
|---|---|
| **Pre-flight**（CLI 不存在、无 external_context） | 立即返回失败 `ExportResult`，不尝试发布 |
| **单条 finding 失败**（认证过期、行号无效） | 记录错误，继续处理后续 finding |
| **全部失败** | `ExportResult.success=False`，`error` 包含所有失败详情 |

### 7.8 评论内容格式

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

### 10.2 ReviewCreatorPort（v2）

```python
class ReviewCreatorPort(Protocol):
    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        review_policy: TriggerReviewPolicy,
        external_context: dict[str, object] | None = None,
    ) -> str:
        ...
```

`TriggerReviewPolicy` 是只读值对象，由 `TriggerReviewPolicy.from_config(config)` 从插件配置构造。详见 [插件 API v2 升级指南](./plugin-upgradev2.md) §7。

### 10.3 FindingExportEnvelope（v2）

```python
@dataclass(frozen=True)
class ReviewExportMetaV2:
    task_id: str
    repository_name: str
    scope_type: str
    base_oid: str
    head_oid: str
    base_ref: str | None
    target_ref: str | None
    status: Literal["completed", "partial"]
    selection_request: SelectionRequestDto
    plan_summary: ReviewPlanSummaryDto
    coverage: ReviewCoverageDto
    created_at: datetime
    external_context: dict | None = None
```

V1 插件接收 `FindingExportEnvelopeV1`（含 `selected_agent_versions`），由 Core 从 V2 自动投影。

---

## 11. 插件项目结构

每个平台插件是一个独立项目，同时包含 trigger 和 report 实现。以 **GitHub 插件**为例：

```
CodeLens-GitHub-Plugin/
├── plugin.json                    # 统一清单（platform: "github"）
├── github_trigger.py              # TriggerSinkPort 实现（webhook 处理）
├── github_report.py               # ReportSinkPort 实现（PR review comment）
├── repo_manager.py                # 仓库克隆/更新/清理（辅助模块）
├── README.md                      # 插件说明文档
└── .gitignore                     # Git 忽略规则
```

**关键文件说明**：

| 文件 | 作用 | 必需 |
|---|---|---|
| `plugin.json` | 插件清单，声明身份、平台、能力和配置 Schema | ✅ |
| `<platform>_trigger.py` | Trigger 实现，处理 webhook 并创建 review | ✅（若声明 trigger 能力） |
| `<platform>_report.py` | Report 实现，将 findings 发布到平台 | ✅（若声明 report 能力） |
| `repo_manager.py` | 仓库管理辅助模块，处理克隆/更新/清理 | 推荐（webhook 插件） |

### 11.1 plugin.json 完整示例（GitHub）

```json
{
  "plugin_id": "github",
  "name": "GitHub Webhook Plugin",
  "name_i18n": {
    "zh-CN": "GitHub Webhook 插件"
  },
  "version": "1.0.0",
  "description": "Automatically trigger CodeLens reviews on GitHub PR/push events and post findings as PR review comments",
  "description_i18n": {
    "zh-CN": "在 GitHub PR/Push 事件时自动触发 CodeLens 代码审查，并将审查结果以 PR Review 评论形式回写"
  },
  "author": "CodeLens Team",
  "platform": "github",
  "capabilities": {
    "trigger": {
      "trigger_type": "webhook",
      "supported_events": ["webhook"],
      "entry_point": "github_trigger:GitHubTrigger",
      "config_schema": {
        "type": "object",
        "properties": {
          "pr_events": {
            "type": "array",
            "items": {
              "type": "string",
              "enum": ["opened", "synchronize", "reopened", "closed"]
            },
            "default": ["opened", "synchronize"],
            "description": "Pull request actions to trigger reviews",
            "description_i18n": {
              "zh-CN": "触发审查的 PR 事件（opened, synchronize, reopened, closed）"
            }
          },
          "debounce_seconds": {
            "type": "integer",
            "default": 60,
            "minimum": 0,
            "description": "Minimum seconds between triggers for the same PR/branch (0 = disabled)",
            "description_i18n": {
              "zh-CN": "同一 PR/分支触发评审的最小间隔秒数（0 = 禁用）"
            }
          },
          "github_token": {
            "type": "string",
            "default": "",
            "description": "GitHub API token for git clone (or set GITHUB_TOKEN env var)",
            "description_i18n": {
              "zh-CN": "GitHub API 令牌，用于 git clone（或设置 GITHUB_TOKEN 环境变量）"
            }
          },
          "reviewer_selection": {
            "default": {
              "mode": "fixed",
              "reviewer_versions": ["correctness:v2"]
            },
            "oneOf": [
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "mode": { "const": "fixed" },
                  "reviewer_versions": {
                    "type": "array",
                    "items": { "type": "string", "minLength": 1 },
                    "minItems": 1,
                    "maxItems": 32,
                    "uniqueItems": true
                  }
                },
                "required": ["mode", "reviewer_versions"]
              },
              {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "mode": { "const": "adaptive" }
                },
                "required": ["mode"]
              }
            ],
            "description": "Reviewer selection strategy",
            "description_i18n": {
              "zh-CN": "Reviewer 选择策略（fixed 指定固定列表，adaptive 由 Planner 动态决定）"
            }
          },
          "supersede_policy": {
            "type": "string",
            "enum": ["latest_snapshot", "preserve_all"],
            "default": "latest_snapshot",
            "description": "How to handle existing tasks when a new snapshot arrives",
            "description_i18n": {
              "zh-CN": "新 Snapshot 到达时如何处理旧任务（latest_snapshot 替换，preserve_all 保留全部）"
            }
          },
          "prompt_locale": {
            "type": "string",
            "enum": ["en", "zh-CN"],
            "default": "en",
            "description": "Locale for review prompts (en, zh-CN)",
            "description_i18n": {
              "zh-CN": "审查提示词的语言（en, zh-CN）"
            }
          }
        }
      }
    },
    "report": {
      "entry_point": "github_report:GitHubReportSink",
      "config_schema": {
        "type": "object",
        "properties": {
          "gh_binary": {
            "type": "string",
            "default": "gh",
            "description": "Path to gh CLI binary",
            "description_i18n": {
              "zh-CN": "gh CLI 二进制文件路径"
            }
          },
          "post_summary": {
            "type": "boolean",
            "default": true,
            "description": "Post a summary comment before individual findings",
            "description_i18n": {
              "zh-CN": "在具体问题评论前发布一条汇总评论"
            }
          }
        }
      }
    }
  }
}
```

**字段说明**：

| 字段 | 必需 | 说明 |
|---|---|---|
| `plugin_id` | ✅ | 唯一标识符，用于路由、存储和 API 路径。不能是保留值 `"local"` |
| `name` | ✅ | 插件英文名称 |
| `name_i18n` | 推荐 | 国际化名称，key 为 locale（如 `"zh-CN"`） |
| `version` | ✅ | 语义化版本号（如 `"1.0.0"`） |
| `description` | ✅ | 插件英文描述 |
| `description_i18n` | 推荐 | 国际化描述 |
| `author` | ✅ | 作者或团队名称 |
| `platform` | ✅ | 平台标识，用于路由。必须与 `external_context.platform` 一致 |
| `capabilities` | ✅ | 能力声明，至少包含 `trigger` 或 `report` 之一 |
| `capabilities.trigger` | 可选 | Trigger 能力声明 |
| `capabilities.trigger.trigger_type` | ✅ | `"webhook"` 或 `"local-hook"` |
| `capabilities.trigger.supported_events` | ✅ | 支持的事件列表，webhook 插件填 `["webhook"]` |
| `capabilities.trigger.entry_point` | ✅ | 格式 `"module:ClassName"`，如 `"github_trigger:GitHubTrigger"` |
| `capabilities.trigger.config_schema` | ✅ | JSON Schema，描述 trigger 配置项 |
| `capabilities.report.entry_point` | ✅ | 格式 `"module:ClassName"`，如 `"github_report:GitHubReportSink"` |
| `capabilities.report.config_schema` | ✅ | JSON Schema，描述 report 配置项 |

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

**GitHub 适配细节**：

- **Trigger**：接收 `action` 为 `opened` 或 `synchronize` 的 pull_request webhook
- **Report**：使用 GitHub REST API 或 `gh` CLI 发布 PR review 评论
- **认证**：通过 `gh` CLI 的配置文件管理（`gh auth login`）
- **严重级别映射**：`critical`→`critical`，`high`→`major`，`medium`→`minor`，`low`→`suggestion`

### 12.3 external_context 各平台示例

**GitHub**：
```json
{"platform": "github", "project": "owner/repo", "merge_request": 42}
```

**GitLab**：
```json
{"platform": "gitlab", "project": "group/subgroup/project", "merge_request": 123}
```

**注意**：`merge_request` 字段在 push 事件触发的 review 中可能缺失，此时 report 需要通过 commit SHA 查找对应的 MR。

---

## 13. 安全考量

| 风险 | 缓解措施 |
|---|---|
| Webhook 伪造 | 签名验证（HMAC-SHA256 / Token），密钥通过 `trigger_config.webhook_secret` 配置 |
| 仓库克隆路径穿越 | `RepoManager` 使用 `project_path_hash` 作为目录名，不接受用户指定路径 |
| CLI 凭证泄露 | 认证由各平台 CLI 自行管理（keychain / env），插件不接触 token |
| 评论内容注入 | Finding 内容经过 Markdown 转义，通过 stdin 传递避免 shell 注入 |

---

## 14. 新插件接入指南

> 本章节提供从零开发一个新 CodeLens 插件的完整步骤。

### 14.1 接入流程概览

```
① 创建插件 Git 仓库
   │ 包含 plugin.json + trigger 实现 + report 实现
   ▼
② 安装到 CodeLens
   │ POST /api/plugins/install  {"git_url": "...", "ref": "main"}
   ▼
③ 启用 Trigger 能力
   │ PUT /api/plugins/{id}/trigger/enable
   │ PUT /api/plugins/{id}/trigger/config  (配置 webhook 参数)
   ▼
④ 启用 Report 能力（需先启用 Trigger）
   │ PUT /api/plugins/{id}/report/enable
   │ PUT /api/plugins/{id}/report/config
   │ PUT /api/plugins/{id}/report/auto-export  {"enabled": true}
   ▼
⑤ 在平台侧配置 Webhook
   │ 指向 CodeLens 的 POST /api/webhooks/{platform}
   ▼
⑥ 验证端到端流程
   │ 触发 webhook → 创建 review → 自动导出到平台
```

### 14.2 Step 1：创建插件项目

#### 14.2.1 目录结构

```
CodeLens-<Platform>-Plugin/
├── plugin.json                  # 必需：插件清单
├── <platform>_trigger.py        # 必需：Trigger 实现
├── <platform>_report.py         # 必需：Report 实现
├── repo_manager.py              # 推荐：仓库管理辅助
├── README.md                    # 推荐：插件说明
└── .gitignore
```

**命名约定**：
- 仓库名：`CodeLens-<Platform>-Plugin`（如 `CodeLens-GitHub-Plugin`）
- 模块名：`<platform>_trigger.py` / `<platform>_report.py`
- 类名：`<Platform>Trigger` / `<Platform>ReportSink`

#### 14.2.2 编写 plugin.json

参考 [第 11 节](#111-pluginjson-完整示例github) 的完整示例。关键要点：

1. `plugin_id` 唯一且不含空格/特殊字符，不能是保留值 `"local"`
2. `platform` 值必须与 trigger 注入的 `external_context["platform"]` 一致
3. 外置插件**必须同时声明** trigger 和 report 能力
4. `entry_point` 格式为 `"module_name:ClassName"`
5. `config_schema` 使用标准 JSON Schema，推荐提供 `default` 和 `description`/`description_i18n`

### 14.3 Step 2：实现 Trigger 能力

Trigger 实现 `TriggerSinkPort` 协议。以下是完整模板：

```python
"""<Platform> webhook trigger for CodeLens."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

# 可导入同目录的辅助模块（loader 会将插件目录加入 sys.path）
from repo_manager import RepoManager

_LOGGER = logging.getLogger(__name__)


class <Platform>Trigger:
    """Trigger reviews from <Platform> webhook events."""

    def __init__(self, review_creator):
        """初始化 Trigger。

        Args:
            review_creator: ReviewCreatorPort，由 CodeLens loader 注入。
                            调用其 create_review_from_trigger() 创建 review。
        """
        self._review_creator = review_creator
        # 防抖记录：key → 上次触发时间
        self._last_trigger: dict[str, datetime] = {}

    @property
    def trigger_id(self) -> str:
        """稳定标识符，必须与 plugin.json 的 plugin_id 一致。"""
        return "<plugin_id>"

    @property
    def display_name(self) -> str:
        """UI 显示名称。"""
        return "<Platform> Webhook Trigger"

    async def handle_event(
        self,
        event: Any,              # HookEvent 枚举，webhook 插件始终为 HookEvent.WEBHOOK
        repository_path: Path,   # webhook 模式下为 Path(".")，实际仓库路径由插件管理
        config: dict[str, Any],  # trigger 配置，来自 plugin.json config_schema
        event_payload: dict[str, Any],
        # 结构：{"payload": <webhook_body_dict>, "headers": <dict>}
        external_context: dict[str, Any] | None = None,
    ) -> str | None:
        """处理一个 webhook 事件。

        Returns:
            创建的 task_id，或 None（被防抖/过滤跳过时）。
        """
        payload = event_payload.get("payload", {})
        # 1. 解析 payload，提取事件类型和关键信息
        # 2. 按 config 过滤不需要的事件
        # 3. 防抖检查
        # 4. 构建 external_context
        # 5. 克隆/更新仓库
        # 6. 调用 review_creator 创建 review
        ...
```

#### 14.3.1 handle_event 内部模式

以下是 webhook 插件展示的典型模式：

**事件解析**：

```python
async def handle_event(self, event, repository_path, config, event_payload, external_context=None):
    payload = event_payload.get("payload", {})
    object_kind = payload.get("object_kind")

    if object_kind == "merge_request":
        return await self._handle_mr_event(payload, config)
    elif object_kind == "push":
        return await self._handle_push_event(payload, config)
    return None  # 忽略不关心的事件类型
```

**防抖机制**：

```python
def _is_debounced(self, key: str, config: dict) -> bool:
    """检查是否在防抖窗口内。"""
    debounce_seconds = config.get("debounce_seconds", 60)
    if debounce_seconds <= 0:
        return False
    now = datetime.now()
    last = self._last_trigger.get(key)
    if last and (now - last).total_seconds() < debounce_seconds:
        return True
    self._last_trigger[key] = now
    return False
```

**构建 external_context**：

```python
# external_context 是 trigger 和 report 之间的约定
# 必须包含 "platform" 字段用于路由
external_context = {
    "platform": "<platform_id>",     # 必需，与 plugin.json 的 platform 一致
    "project": project_path,         # 项目标识（平台特定格式）
    "merge_request": mr_iid,         # MR/PR 编号（若适用）
}
```

**创建 Review**：

```python
# 从配置构造 v2 策略值对象
policy = TriggerReviewPolicy.from_config(config)

# 调用注入的 review_creator 创建 review
task_id = await self._review_creator.create_review_from_trigger(
    repository_path=repo_path,         # 本地仓库路径
    scope_type="branch",               # "commit" | "branch"
    scope_params={
        "base_ref": target_branch,     # 基准分支
        "target_ref": source_branch,   # 目标分支
    },
    review_policy=policy,              # TriggerReviewPolicy 值对象
    external_context=external_context, # 平台上下文，透传到 report
)
return task_id
```

#### 14.3.2 scope_type 选择

| scope_type | 适用场景 | scope_params |
|---|---|---|
| `"branch"` | MR/PR 级别审查 | `{"base_ref": "main", "target_ref": "feature/xxx"}` |
| `"commit"` | 单次 push/commit 级别审查 | `{"base_commit": "<before_sha>", "target_ref": "<after_sha>"}` |

**常见实践**：MR 事件使用 `branch`；Push 事件优先尝试 `commit`（如果 before SHA 是 after SHA 的祖先），否则回退到 `branch`。

#### 14.3.3 仓库管理

Webhook Trigger 需要自行管理仓库克隆。推荐将仓库管理封装为独立的 `RepoManager` 类：

```python
class RepoManager:
    """管理 webhook trigger 的本地仓库克隆。"""

    def __init__(self, base_dir: Path, token: str = ""):
        self._base_dir = base_dir
        self._token = token

    def get_or_update_repo(
        self, clone_url: str, project_path: str, ref: str, base_ref: str = None
    ) -> Path:
        """确保本地仓库存在并更新到指定分支。

        首次调用：git clone
        后续调用：git fetch + git checkout + git reset --hard
        """
        ...
```

**关键设计决策**：

| 决策 | 理由 |
|---|---|
| 目录名使用项目路径的 SHA256 前 16 位 | 避免文件系统特殊字符问题 |
| 使用 `git fetch + reset` 而非 `pull` | 确保本地分支与远端完全一致 |
| Token 注入到 clone URL | 支持私有仓库认证 |
| 同步方法包装在 `asyncio.to_thread()` 中 | 避免阻塞事件循环 |

### 14.4 Step 3：实现 Report 能力

Report 实现 `ReportSinkPort` 协议。与 Trigger 不同，Report **无构造函数参数**：

```python
"""<Platform> report sink for CodeLens."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)


class <Platform>ReportSink:
    """Post review findings to <Platform> as MR/PR comments."""

    @property
    def sink_id(self) -> str:
        """稳定标识符，必须与 plugin.json 的 plugin_id 一致。"""
        return "<plugin_id>"

    @property
    def display_name(self) -> str:
        """UI 显示名称。"""
        return "<Platform> MR Comments"

    async def export(
        self,
        envelope: Any,             # FindingExportEnvelope（通过属性访问字段）
        config: dict[str, Any],    # report 配置
        repository_path: Path,     # 仓库路径
    ) -> Any:                      # 返回 ExportResult 兼容的 dict
        """导出 findings 到平台。

        关键约束：
        - 不得抛出异常，所有错误必须捕获并返回失败结果
        - 通过 envelope.review.external_context 获取平台路由信息
        """
        # 1. 提取 external_context
        external_context = envelope.review.external_context
        if not external_context:
            return {
                "plugin_id": self.sink_id,
                "task_id": envelope.review.task_id,
                "success": False,
                "output_path": None,
                "error": "No external_context in envelope",
                "exported_at": datetime.now().isoformat(),
            }

        platform = external_context.get("platform")
        project = external_context.get("project")
        mr_iid = external_context.get("merge_request")

        # 2. 遍历 findings 并发布
        errors = []
        for finding in envelope.findings:
            try:
                self._post_finding(project, mr_iid, finding, config)
            except Exception as e:
                errors.append(f"Finding {finding.finding_id}: {e}")

        # 3. 返回结果
        return {
            "plugin_id": self.sink_id,
            "task_id": envelope.review.task_id,
            "success": len(errors) == 0,
            "output_path": None,
            "error": "; ".join(errors) if errors else None,
            "exported_at": datetime.now().isoformat(),
        }
```

#### 14.4.1 Report 关键模式

**external_context 缺失时的处理**：

Report 必须处理 `external_context` 为 `None` 或缺少必要字段的情况。这是 push 事件触发的 review 可能出现的场景（没有 MR 信息）。推荐的应对策略是主动查找 MR：

```python
# 如果没有 merge_request 信息，尝试通过 commit SHA 查找 MR
if not mr_iid:
    head_oid = envelope.review.head_oid
    mr_iid = await self._find_mr_by_commit(project, head_oid)
    if not mr_iid:
        return {"success": False, "error": "No MR found for commit", ...}
```

**严重级别映射**：

各平台的严重级别命名不同，需要在 Report 中做映射。典型的映射示例：

| CodeLens severity | 平台 severity |
|---|---|
| `critical` | `critical` |
| `high` | `major` |
| `medium` | `minor` |
| `low` | `suggestion` |

**评论内容格式**：

大多数平台使用 Markdown 格式发布行级评论：

```python
body_lines = [f"**{finding.title}**", ""]
if finding.explanation:
    body_lines.append(finding.explanation)
    body_lines.append("")
if finding.recommendation:
    body_lines.append("**Recommendation:**")
    body_lines.append(finding.recommendation)
body = "\n".join(body_lines)
```

通过 stdin 传递评论内容（`--body-file -`），避免 shell 转义问题。

### 14.5 Step 4：安装与配置

#### 14.5.1 安装插件

```bash
# 通过 API 安装
curl -X POST http://localhost:8000/api/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"git_url": "https://github.com/your-org/CodeLens-<Platform>-Plugin.git", "ref": "main"}'
```

安装流程：
1. CodeLens 克隆插件仓库到临时目录
2. 读取并验证 `plugin.json`
3. 移动到 `{data_dir}/plugins/{plugin_id}/`
4. 创建 `PluginRecord`（trigger 和 report 默认关闭）
5. 从 `config_schema` 的 `default` 值生成默认配置

#### 14.5.2 启用能力

```bash
# 1. 启用 Trigger
curl -X PUT http://localhost:8000/api/plugins/<plugin_id>/trigger/enable

# 2. 配置 Trigger
curl -X PUT http://localhost:8000/api/plugins/<plugin_id>/trigger/config \
  -H "Content-Type: application/json" \
  -d '{
    "debounce_seconds": 60,
    "reviewer_selection": {"mode": "fixed", "reviewer_versions": ["correctness:v2"]},
    "supersede_policy": "latest_snapshot",
    "prompt_locale": "zh-CN"
  }'

# 3. 启用 Report（必须先启用 Trigger）
curl -X PUT http://localhost:8000/api/plugins/<plugin_id>/report/enable

# 4. 配置 Report
curl -X PUT http://localhost:8000/api/plugins/<plugin_id>/report/config \
  -H "Content-Type: application/json" \
  -d '{"gh_binary": "gh", "post_summary": true}'

# 5. 启用自动导出
curl -X PUT http://localhost:8000/api/plugins/<plugin_id>/report/auto-export \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

#### 14.5.3 配置平台 Webhook

在目标平台的项目设置中配置 Webhook：

| 配置项 | 值 |
|---|---|
| URL | `http(s)://<codelens-host>:<port>/api/webhooks/<platform>` |
| 方法 | POST |
| Content-Type | application/json |
| 触发事件 | MR/PR 创建、更新等（根据插件支持的事件选择） |

### 14.6 加载机制详解

理解加载机制有助于排查问题：

#### 14.6.1 动态加载流程

```
CompositePluginLoader.load_plugin(plugin_id)
    │
    ├── 检查缓存 _trigger_instances[plugin_id]
    │   └── 命中 → 直接返回
    │
    ├── 尝试内置加载（plugin_id == "local"）
    │   └── 成功 → 缓存并返回
    │
    └── 外部加载（importlib）
        │
        ├── 解析 entry_point: "github_trigger:GitHubTrigger"
        │   → module_name = "github_trigger"
        │   → class_name = "GitHubTrigger"
        │
        ├── 定位模块文件: install_path / "github_trigger.py"
        │
        ├── 将 install_path 加入 sys.path（append，最低优先级）
        │   → 使同目录的 repo_manager.py 等辅助模块可导入
        │
        ├── 使用 importlib.util.spec_from_file_location() 创建模块
        │   → 模块名: "codelens_ext_plugin_{plugin_id}_{generation}"
        │
        ├── 编译并 exec() 源码
        │
        ├── 提取类: getattr(module, class_name)
        │
        ├── 实例化: trigger_class(review_creator)
        │   → review_creator 是 ReviewCreatorPort 的适配器实例
        │
        ├── 验证: hasattr(instance, "trigger_id") and hasattr(instance, "handle_event")
        │
        └── 缓存并返回实例
```

#### 14.6.2 关键约束

| 约束 | 说明 |
|---|---|
| **不导入 CodeLens 代码** | 插件是独立项目，不能 `import codelens.*`。所有交互通过 Protocol duck-typing |
| **模块名唯一** | entry_point 的 module 部分对应插件目录下的 `.py` 文件名 |
| **Trigger 构造函数接收 review_creator** | `__init__(self, review_creator)` 是唯一参数 |
| **Report 无构造参数** | `__init__(self)` 无参数 |
| **可导入同目录模块** | loader 会将 `install_path` 加入 `sys.path`，因此 `from repo_manager import RepoManager` 可行 |
| **缓存失效** | 安装/卸载/重新配置插件时自动清除缓存，下次调用重新加载 |

### 14.7 测试与调试

#### 14.7.1 手动触发 Webhook 测试

```bash
# 模拟 webhook 请求
curl -X POST http://localhost:8000/api/webhooks/<platform> \
  -H "Content-Type: application/json" \
  -d '{
    "object_kind": "merge_request",
    "object_attributes": {
      "iid": 1,
      "action": "open",
      "source_branch": "feature/test",
      "target_branch": "main"
    },
    "project": {
      "path_with_namespace": "group/project"
    }
  }'
```

#### 14.7.2 查看插件状态

```bash
# 列出所有插件及其状态
curl http://localhost:8000/api/plugins | python -m json.tool
```

#### 14.7.3 日志排查

插件日志通过 CodeLens 的日志系统输出。Trigger 和 Report 中的 `_LOGGER` 调用会出现在 CodeLens 的运行日志中。关键排查点：

| 场景 | 检查点 |
|---|---|
| Webhook 未触发 review | 检查 `platform` 是否匹配、`trigger_enabled` 是否为 `true` |
| Review 创建失败 | 检查 `repository_path` 是否有效、`scope_params` 是否正确 |
| Report 未导出 | 检查 `report_enabled` + `report_auto_export` 是否都为 `true` |
| Report 导出失败 | 检查 `external_context` 是否包含正确的 `platform`、`project`、`merge_request` |
| 模块加载失败 | 检查 `entry_point` 格式是否正确、类名是否匹配 |

#### 14.7.4 配置验证

配置通过 `jsonschema` 严格验证。`config_schema` 中的约束（如 `enum`、`minimum`、`additionalProperties: false`）会自动执行。修改配置时如果违反 schema，API 会返回 422 错误。

### 14.8 接入检查清单

开发完成后，逐项确认：

- [ ] `plugin.json` 格式正确，`plugin_id` 唯一且非保留值
- [ ] `platform` 值与 trigger 注入的 `external_context["platform"]` 一致
- [ ] `entry_point` 格式为 `"module:ClassName"`，模块文件存在于插件根目录
- [ ] Trigger 类实现 `trigger_id`（property）、`display_name`（property）、`handle_event()`
- [ ] Trigger 构造函数接收 `review_creator` 单参数
- [ ] Report 类实现 `sink_id`（property）、`display_name`（property）、`export()`
- [ ] Report 类无构造参数
- [ ] `export()` 返回 ExportResult 兼容的 dict，不抛出异常
- [ ] `config_schema` 中所有配置项都有 `default` 值和 `description`
- [ ] 提供 `name_i18n` / `description_i18n` 国际化字段
- [ ] 仓库管理使用 `asyncio.to_thread()` 包装同步操作（避免阻塞事件循环）
- [ ] 防抖机制合理配置（默认 60 秒）
- [ ] Webhook 端点 URL 格式为 `/api/webhooks/{platform}`
- [ ] 端到端验证通过：webhook → review → auto-export → 平台评论

### 14.9 参考实现速查

| 文件 | 行数 | 关键功能 |
|---|---|---|
| `plugin.json` | 130 行 | 完整清单，含 trigger（8 个配置项）和 report（3 个配置项）的 config_schema |
| `<platform>_trigger.py` | ~200 行 | MR/Push 事件处理、防抖、scope 选择、external_context 构建 |
| `<platform>_report.py` | ~200 行 | MR 发现（含 push 事件的 MR 查找）、行级评论发布、API 调用 |
| `repo_manager.py` | ~150 行 | SHA256 哈希目录名、clone/fetch/reset、token 注入、超时控制 |

---

## 15. 实现状态

所有核心功能已实现并验证。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **Phase 1** | CodeLens 核心：统一插件模型 + 存储 + API | ✅ 已完成 |
| **Phase 2** | CodeLens 核心：external_context 透传 + 平台路由 | ✅ 已完成 |
| **Phase 3** | CodeLens 核心：webhook 端点 + trigger 外部加载 | ✅ 已完成 |
| **Phase 4** | 插件生态：trigger + report + repo_manager 参考实现 | ✅ 已验证 |
