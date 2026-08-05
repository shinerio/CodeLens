# CodeLens 插件 API v2 升级指南

## 1. 文档定位

本文面向 CodeLens Trigger/Report 插件作者，说明如何从当前插件 API v1 迁移到支持多 Agent Review 的插件 API v2。

CodeLens `0.2.0` 已实现本文的 v2 契约。历史 v1 插件仍可在兼容窗口内加载；新插件应直接使用公共 `codelens.plugin.api.v2` 类型与 Envelope 2.0。

示例假设首个支持插件 API v2 的 CodeLens 版本为 `0.2.0`。如果实际发布版本不同，只需相应调整 Manifest 的 `min_codelens_version`，其他 v2 契约不变。

## 2. 升级摘要

| 关注点 | 插件 API v1 | 插件 API v2 |
| --- | --- | --- |
| Manifest API 标识 | 没有，缺省视为 v1 | `plugin_api_version: "2"` |
| Reviewer 配置 | `selected_agents: string[]` | `reviewer_selection` 判别联合 |
| Reviewer 决策 | 插件传递 Agent 列表 | Fixed 传递列表；Adaptive 不传列表 |
| 资源边界 | 主要依赖全局配置 | 全局配置（执行限制与工具限制） |
| Review Profile | 不支持 | 配置页可从 Profile 复制策略；保存后为插件独立快照 |
| 高频触发 | 插件自行 debounce，核心按 base/head 去重 | 增加 `supersede_policy`，核心按完整策略幂等 |
| ReviewCreator Port | `selected_agents` 参数 | `TriggerReviewPolicy` 值对象 |
| Correctness Reviewer | `correctness:v1` | 新配置使用 `correctness:v2`，旧配置保持 v1 |
| Report Envelope | `schema_version: "1.0"` | `schema_version: "2.0"`，增加 Plan/Coverage 元数据 |
| Finding | 单 Agent 产物 | 仍只导出 Published Finding，不暴露 Candidate/Cluster |
| Tool/MCP/Skill | 插件不参与 | 仍由 Reviewer Version 的 Capability Profile 决定 |

v2 的核心原则是：插件只决定触发时机、Review Scope 和 Review Selection Policy，不参与 Planner、Agent 工具、MCP、Skill、Verifier 或 Finding 发布决策。

## 3. 兼容性与发布策略

### 3.1 Manifest 版本

v2 插件必须：

- 把插件自身 SemVer 主版本升级，例如 `1.4.2` 升到 `2.0.0`；
- 声明 `plugin_api_version: "2"`；
- 把 `min_codelens_version` 设置为第一个实现插件 API v2 的 CodeLens 版本；
- 不尝试使用同一份运行时代码同时兼容结构不兼容的 v1/v2 `ReviewCreatorPort`。

需要继续支持 CodeLens 0.1.x 时，应保留插件 v1 发布分支或 v1 Release，不要在运行时通过反射猜测 Port 签名。

未声明 `plugin_api_version` 的历史插件按 v1 处理。CodeLens v2 可以在一个兼容窗口内继续加载 v1 插件，并通过防腐层把 `selected_agents` 转换为 Fixed，但 v1 插件不能使用 Adaptive 或 Supersede Policy。

### 3.2 Core 前置要求

插件 API v2 上线前，CodeLens 核心必须先完成：

- 解析和持久化 `plugin_api_version`；
- 在安装、更新和加载前强制校验 `min_codelens_version`；
- 提供 v2 `ReviewCreatorPort` 和公共 `TriggerReviewPolicy` 值对象；
- 提供 v1 到 v2 的公共 Trigger 配置迁移；
- 生成 `FindingExportEnvelope` 2.0；
- 在更新失败时恢复旧插件目录、Manifest 和配置。

插件不能仅靠修改 `plugin.json` 绕过这些 Core 前置条件。

## 4. Manifest 升级

### 4.1 v1 示例

```json
{
  "plugin_id": "example-review",
  "name": "Example Review Plugin",
  "version": "1.4.2",
  "platform": "example",
  "capabilities": {
    "trigger": {
      "trigger_type": "webhook",
      "supported_events": ["webhook"],
      "entry_point": "trigger:ExampleTrigger",
      "config_schema": {
        "type": "object",
        "properties": {
          "selected_agents": {
            "type": "array",
            "items": { "type": "string" },
            "minItems": 1,
            "default": ["correctness:v1"]
          }
        }
      }
    }
  }
}
```

### 4.2 v2 示例

```json
{
  "plugin_id": "example-review",
  "name": "Example Review Plugin",
  "version": "2.0.0",
  "plugin_api_version": "2",
  "min_codelens_version": "0.2.0",
  "description": "Creates CodeLens reviews from Example webhooks",
  "author": "Example Team",
  "platform": "example",
  "capabilities": {
    "trigger": {
      "trigger_type": "webhook",
      "supported_events": ["webhook"],
      "entry_point": "trigger:ExampleTrigger",
      "config_schema": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
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
            ]
          },
          "supersede_policy": {
            "type": "string",
            "enum": ["latest_snapshot", "preserve_all"],
            "default": "latest_snapshot"
          },
          "prompt_locale": {
            "type": "string",
            "enum": ["en", "zh-CN"],
            "default": "en"
          },
          "debounce_seconds": {
            "type": "integer",
            "minimum": 0,
            "default": 10
          }
        },
        "required": [
          "reviewer_selection",
          "supersede_policy",
          "prompt_locale",
          "debounce_seconds"
        ]
      }
    },
    "report": {
      "entry_point": "report:ExampleReportSink",
      "config_schema": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "destination": {
            "type": "string",
            "minLength": 1
          }
        },
        "required": ["destination"]
      }
    }
  }
}
```

新安装的 v2 插件可以把 `correctness:v2` 作为 Fixed 默认值。已经安装的 v1 插件升级时，不能使用这个默认值覆盖用户原有 `selected_agents`。

## 5. Reviewer Selection 配置

### 5.1 Fixed

```json
{
  "reviewer_selection": {
    "mode": "fixed",
    "reviewer_versions": [
      "correctness:v2",
      "security:v1"
    ]
  }
}
```

规则：

- 插件必须原样传递用户配置，不能根据 Diff 自行增删 Reviewer；
- 至少包含一个 Reviewer，不能重复；
- `general:v1` 必须单独出现；
- `correctness:v1` 只能用于历史 Legacy Single Reviewer 配置，不能与 v2 Reviewer 混组；
- 最终合法性以 CodeLens Reviewer Catalog 和 Review Application 校验为准，不能只依赖 Manifest JSON Schema。

### 5.2 Adaptive

```json
{
  "reviewer_selection": {
    "mode": "adaptive"
  }
}
```

规则：

- Adaptive 对象中不得出现 `reviewer_versions`；
- 插件不得在触发前调用模型预选 Reviewer；
- 插件不得在 Planner 产生 Plan 后增选或取消 Reviewer；
- Planner 失败时 Review 失败，插件不得重新创建一个 Fixed 或 General Review 作为隐式回退。

### 5.3 General

预算敏感或极简变更应显式配置：

```json
{
  "reviewer_selection": {
    "mode": "fixed",
    "reviewer_versions": ["general:v1"]
  }
}
```

如果插件已经明确知道只需要 General，使用 Fixed 比 Adaptive 更省成本，因为不会额外调用 Planner。

### 5.4 Profile 模板与配置快照

CodeLens v2 的插件配置页可以列出 Review Profile，帮助用户复用已有策略，但 Profile 不是 Trigger 运行时依赖：

1. 用户选择 Profile 时，Core 把其中的 `reviewer_selection` 复制到插件配置草稿。
2. 用户可以继续调整草稿；调整不会修改原 Profile。
3. 保存时，插件配置与 `supersede_policy`、`prompt_locale`、Debounce 和插件自有字段一起原子持久化。
4. Profile 后续变化不会自动更新插件。只有用户显式选择“从 Profile 重新载入”并再次保存时才更新。
5. Trigger 事件只使用已保存配置，不查询 Profile、不弹出确认，也不等待用户选择 Reviewer。

Core 可以在插件配置之外保存来源 Profile ID、Revision 和复制时间，用于界面提示。该来源元数据不属于插件 Manifest Schema，不传给插件，不参与 Review 策略指纹，也不能被插件用于改变执行语义。

插件作者仍需在 Manifest 中声明标准 v2 字段。CodeLens 前端识别 `reviewer_selection` 的公共契约，并用共享 Review Strategy 编辑器渲染；插件自有字段继续由通用 JSON Schema 表单渲染。插件不得提供另一套 Reviewer 选择 UI 或在自有字段中编码隐藏 Reviewer 列表。

## 6. v1 配置迁移

### 6.1 平台迁移规则

`selected_agents` 是 CodeLens 公共字段，因此由 CodeLens 核心迁移，不由每个插件重复实现：

```text
selected_agents: [A, B]
    ->
reviewer_selection:
  mode: fixed
  reviewer_versions: [A, B]
```

迁移必须：

- 保持顺序和 Reviewer 版本不变；
- 删除 `selected_agents`；
- 不切换到 Adaptive；
- 不把 `correctness:v1` 自动替换为 `correctness:v2`；
- 为缺失的 `supersede_policy` 添加 `latest_snapshot`；
- 保留 `prompt_locale`、Debounce、Scope 和插件自有字段；
- 迁移后使用新 Manifest Schema 和 Review Application 再次校验；
- 失败时保持插件禁用并保留原配置与可读错误，不能部分保存。

示例：

```json
{
  "selected_agents": ["correctness:v1"],
  "prompt_locale": "zh-CN",
  "debounce_seconds": 10
}
```

迁移为：

```json
{
  "reviewer_selection": {
    "mode": "fixed",
    "reviewer_versions": ["correctness:v1"]
  },
  "supersede_policy": "latest_snapshot",
  "prompt_locale": "zh-CN",
  "debounce_seconds": 10
}
```

这里故意保留 `correctness:v1`。用户以后编辑插件配置并明确选择升级时，才改为 `correctness:v2`。

### 6.2 插件自有字段

更新时，Core 先按新 Schema 保留同名字段并补充 Default，再执行 v1→v2 策略迁移与完整校验；代码目录和配置记录只有在整组操作成功后才对用户生效。插件作者应：

- 尽量保持自有字段名称和类型稳定；
- 为新增可选字段提供 Default；
- 把必填且没有安全默认值的新字段作为显式升级前置条件；
- 不利用字段改名顺带改变 Review 语义；
- 在配置迁移失败时提示用户修复，不能在 Trigger 事件发生后才发现。

## 7. TriggerReviewPolicy 与 ReviewCreatorPort v2

### 7.1 公共值对象

v2 不再把选择策略拆成一组容易漏传的参数。CodeLens 插件公共 API 提供只读值对象：

```python
@dataclass(frozen=True)
class FixedReviewerSelection:
    mode: Literal["fixed"]
    reviewer_versions: tuple[str, ...]


@dataclass(frozen=True)
class AdaptiveReviewerSelection:
    mode: Literal["adaptive"]


type ReviewerSelection = FixedReviewerSelection | AdaptiveReviewerSelection


@dataclass(frozen=True)
class TriggerReviewPolicy:
    reviewer_selection: ReviewerSelection
    supersede_policy: Literal["latest_snapshot", "preserve_all"]
    prompt_locale: Literal["en", "zh-CN"]

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "TriggerReviewPolicy": ...
```

插件应调用公共 `from_config`，不能复制判别联合、General 互斥或版本兼容校验。

### 7.2 Port 签名

v1：

```python
await review_creator.create_review_from_trigger(
    repository_path=repository_path,
    scope_type=scope_type,
    scope_params=scope_params,
    selected_agents=("correctness:v1",),
    prompt_locale="en",
    external_context=external_context,
)
```

v2：

```python
policy = TriggerReviewPolicy.from_config(config)

await review_creator.create_review_from_trigger(
    repository_path=repository_path,
    scope_type=scope_type,
    scope_params=scope_params,
    review_policy=policy,
    external_context=external_context,
)
```

目标 Port：

```python
class ReviewCreatorPort(Protocol):
    async def create_review_from_trigger(
        self,
        repository_path: Path,
        scope_type: str,
        scope_params: dict[str, str | None],
        review_policy: TriggerReviewPolicy,
        external_context: dict[str, object] | None = None,
    ) -> str: ...
```

`ReviewCreatorAdapter` 负责把 Plugin 拥有的值对象转换为 Review 上下文的 `ReviewProfileSnapshot`。外部插件不能导入 `review.application`、Reviewer Catalog Repository 或 Capability Infrastructure。

### 7.3 Trigger 实现示例

```python
from pathlib import Path
from typing import Any

from codelens.plugin.api.v2 import (
    HookEvent,
    ReviewCreatorPort,
    TriggerReviewPolicy,
)


class ExampleTrigger:
    def __init__(self, review_creator: ReviewCreatorPort) -> None:
        self._review_creator = review_creator

    @property
    def trigger_id(self) -> str:
        return "example-review"

    @property
    def display_name(self) -> str:
        return "Example Review"

    async def handle_event(
        self,
        event: HookEvent,
        repository_path: Path,
        config: dict[str, Any],
        event_payload: dict[str, Any],
        external_context: dict[str, Any] | None = None,
    ) -> str | None:
        if event is not HookEvent.WEBHOOK:
            return None

        policy = TriggerReviewPolicy.from_config(config)
        scope_params = self._scope_from_payload(event_payload)
        return await self._review_creator.create_review_from_trigger(
            repository_path=repository_path,
            scope_type="branch",
            scope_params=scope_params,
            review_policy=policy,
            external_context=external_context,
        )
```

示例省略平台 Payload 验证；实际插件必须在创建 Review 前验证事件真实性、仓库映射和所需字段。

## 8. 自动触发与幂等

### 8.1 Debounce 与幂等不是一回事

- `debounce_seconds`：插件层减少短时间重复事件；
- Core Idempotency：相同冻结 Snapshot 与完整执行策略只创建一个逻辑 Review；
- `supersede_policy`：新 Snapshot 到达时如何处理同一触发槽位内的旧任务。

插件可以继续 Debounce，但不能把内存 Debounce 当成跨进程幂等保证。

### 8.2 Supersede Policy

`latest_snapshot`：

- 旧排队任务进入 `superseded`；
- 旧运行任务发起协作取消；
- 新 Snapshot 创建新任务；
- 已发布历史结果不删除。

`preserve_all`：

- 每个不同 Snapshot 都保留独立任务；
- 适合逐 Commit CI 或审计场景。

插件只能传递策略，不能直接取消 ReviewTask 或修改其状态。

### 8.3 幂等指纹

Core 幂等键至少包含：

```text
repository + base/head Snapshot
+ reviewer selection policy fingerprint
+ planner/catalog version
+ capability/skill policy fingerprint
```

因此，同一代码使用不同 Reviewer 策略不会被错误合并。

## 9. Report 插件升级

### 9.1 Envelope 2.0

Report 插件只接收最终 Published Finding，不接收 CandidateFinding、FindingCluster、ResolutionDecision、Prompt 或原始模型输出。

目标结构：

```json
{
  "schema_version": "2.0",
  "exported_at": "2026-07-31T12:00:00Z",
  "review": {
    "task_id": "review_...",
    "repository_name": "example",
    "scope_type": "branch",
    "base_oid": "...",
    "head_oid": "...",
    "base_ref": "main",
    "target_ref": "feature",
    "status": "partial",
    "created_at": "2026-07-31T11:55:00Z",
    "external_context": {},
    "selection_request": {
      "mode": "adaptive"
    },
    "plan_summary": {
      "strategy": "specialist_team",
      "selected_reviewer_versions": [
        "correctness:v2",
        "security:v1"
      ],
      "planner_version": "review-planner:v1",
      "plan_hash": "..."
    },
    "coverage": {
      "completed_reviewer_versions": ["correctness:v2"],
      "failed_reviewer_versions": ["security:v1"],
      "omitted_reviewer_versions": []
    }
  },
  "findings": []
}
```

`findings[]` 保持现有 Published Finding 字段语义。一个 Finding 的 `reviewer_id` 表示规范主 Reviewer；跨 Reviewer Provenance 仍属于 CodeLens 内部审计数据，不要求 Report 插件处理。

### 9.2 Report Sink 要求

- 按 `schema_version` 显式分派，不能默认所有 Envelope 都是 1.0；
- 使用 `plan_summary.selected_reviewer_versions` 展示实际执行团队；
- 不把 Adaptive 请求误显示成“未选择 Reviewer”；
- 对 `partial` 显示缺失视角，不能把它格式化为完整成功；
- 不导出内部被抑制、合并或未确认的 Candidate；
- `failed`、`canceled`、`superseded` 默认不自动导出；
- `completed` 或 `partial` 且存在 Published Finding 时可以导出；
- 继续把 `external_context` 视为路由元数据，不记录其中的 Secret。

### 9.3 v1/v2 双格式

如果一个导出目标需要同时消费历史 Artifact，可以让离线格式化代码同时读取 Envelope 1.0 与 2.0。但运行中的插件 v2 只依赖 v2 Port；不要让 Trigger Runtime 为兼容旧格式承担分支。

## 10. Capability、MCP 与 Skill 限制

插件 v2 配置不能出现以下字段：

- `tools` 或任意模型工具名；
- MCP Server、MCP Tool 或 MCP Resource URI；
- Skill ID、Skill 内容或 Skill 加载开关；
- Shell、网络、文件系统或 Secret 权限；
- Verifier 或 Planner Prompt。

Reviewer Version 静态绑定 Capability Profile 和 Skill Policy。插件选择 Reviewer 后，CodeLens Core 冻结其 built-in tools、未来 MCP Binding 和确定性 Skill 激活结果。插件不能通过 `external_context`、配置或事件 Payload 扩大这些权限。

## 11. 错误处理

插件必须区分：

- **配置错误**：保存或启用 Trigger 时失败，不等待事件触发；
- **事件不匹配**：正常返回 `None`；
- **Review 创建失败**：记录脱敏错误并返回 `None`，不做隐式策略回退；
- **Review 运行失败**：任务已经创建，由 Review 状态和 Report 流程处理；
- **Report 失败**：返回结构化 `ExportResult`，不能影响 Review 终态或其他插件；
- **插件更新失败**：恢复旧版本代码、Manifest、配置和实例缓存。

不得记录 Webhook Secret、Authorization、Cookie、模型凭证、源码正文、Prompt 或 MCP 原始输出。

## 12. 升级步骤

1. 为当前 v1 插件发布最后一个维护版本并保留 Release。
2. 把插件自身版本提升到 `2.0.0`。
3. 在 Manifest 增加 `plugin_api_version: "2"` 和正确的 `min_codelens_version`。
4. 用 `reviewer_selection` 和 `supersede_policy` 替换 Trigger Schema 中的 `selected_agents`。
5. 保持插件自有配置字段稳定，为新增字段设置安全 Default。
6. 把 Trigger 调用改为 `TriggerReviewPolicy.from_config` 和 `ReviewCreatorPort` v2。
7. 删除插件内部 Reviewer 推断、自动增删或失败回退逻辑。
8. 如果提供 Report Capability，升级为 Envelope 2.0 并展示 Plan/Coverage。
9. 使用 CodeLens v2 的更新流程验证 v1 配置迁移，不手工编辑持久化文件。
10. 验证插件配置从 Profile 复制后，编辑或删除原 Profile 不会改变已保存插件配置。
11. 在启用生产 Trigger 前验证 Fixed、Adaptive、General、Partial 和 Superseded 场景。

## 13. 验证清单

### Manifest 与配置

- Manifest API 版本和最小 CodeLens 版本正确。
- Trigger Schema 设置 `additionalProperties: false`。
- Fixed 和 Adaptive 使用 `oneOf`，Adaptive 不接受 `reviewer_versions`。
- General 与专业 Reviewer 互斥由 Core 再次校验。
- 新安装默认与迁移默认分开处理。
- v1 `selected_agents` 迁移后版本和值完全不变。
- Profile 只初始化草稿；保存后的插件配置不再实时引用 Profile。
- “从 Profile 重新载入”必须经过显式保存才影响后续 Trigger。

### Trigger

- Fixed 原样传递 Reviewer。
- Adaptive 不传 Reviewer 列表。
- General 使用 Fixed + Lean。
- 同一事件重试不会创建重复任务。
- 新 Snapshot 按配置 Supersede 或 Preserve。
- Planner 或 Review 失败时不创建隐藏的回退任务。

### Report

- 只处理 Published Finding。
- 正确展示实际 Reviewer Team，而不是仅展示请求。
- Partial 显示失败 Reviewer。
- Superseded 不自动导出。
- Envelope 版本不支持时返回结构化错误。

### 安全

- `external_context` 不含凭证。
- 日志不含 Payload Secret 或源码。
- 插件不能传入 Capability、MCP、Skill 或 Prompt。
- 插件更新失败后旧版本仍可加载，配置没有部分迁移。

## 14. 相关设计

- [CodeLens 多 Agent Review 设计](./superpowers/specs/2026-07-31-multi-agent-review-design.md)
- [CodeLens 架构](./ARCHITECTURE.md)
- [当前插件运行机制](./runtime-mechanism.md)
