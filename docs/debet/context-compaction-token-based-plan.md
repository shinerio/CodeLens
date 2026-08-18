# 上下文压缩机制：从字节计数改为 token 计数

> 状态：计划中，待实施  
> 创建时间：2026-08-16  
> 负责 agent：待分配

## 1. 背景与问题

### 1.1 当前实现

CodeLens 的上下文压缩（context compaction）机制使用 **UTF-8 字节长度** 作为上下文大小的代理指标：

- `active_bytes`：计算对话历史中活跃尾部的 UTF-8 字节大小
- `context_compaction_trigger_bytes`：压缩触发阈值（默认 128KB = 131072 字节）
- `_hard_watermark`：硬水位线（2 倍触发阈值 + 64KB 最小间隔）

### 1.2 核心问题

**字节和 token 的转换率因内容类型差异很大，导致压缩触发时机不精确。**

实测数据（来自 review_27de85c35ff1437c84fb872c022364a1 任务）：

| 内容类型 | bytes/token | 说明 |
|---------|-------------|------|
| 英文文本 | ~4.0 | 标准比率 |
| 中文文本 | ~1.5-2.0 | 中文 UTF-8 3 bytes，但 tokenizer 通常 1 token |
| 代码+JSON | ~4.5-5.0 | 结构化开销大 |
| **混合（实测）** | **4.77** | 包含大量结构化数据（JSON、行号前缀等） |

**误差影响**：
- 128KB 阈值按 4 bytes/token 估算 = 32K tokens
- 按实测 4.77 bytes/token = 26.8K tokens
- **误差约 16%**，对于中文内容误差可能更大

### 1.3 根本原因

1. **大模型以 token 为单位**：模型上下文窗口（如 128K、200K）以 token 计
2. **当前使用字节近似**：`len(json.encode("utf-8"))` 不是 token 的准确度量
3. **行业标准**：tiktoken 是 OpenAI 官方开源 tokenizer，本地计算，零延迟，被主流框架（LangChain、AutoGen 等）广泛使用

## 2. 解决方案

### 2.1 核心思路

**使用 tiktoken 进行本地 token 计数，将所有压缩相关设置从字节改为 token。**

### 2.2 关键设计决策

#### 2.2.1 Token 计数范围

**计数完整输入**：包括系统提示（instructions）+ 工具定义（tools）+ 对话历史（input items）

理由：
- 发起 LLM 调用时，完整输入包含这三部分
- tiktoken 可以对任何文本进行编码，包括 JSON 格式的工具定义
- 这样更准确地反映实际上下文压力

#### 2.2.2 Token 计数策略

- **固定部分缓存**：instructions 和 tools 在 agent run 内不变，首次计算后缓存到 tracker
- **活跃部分每次计算**：对话历史（active tail）每次 checkpoint filter 调用时重新计算
- **总计**：`total_tokens = fixed_overhead_tokens + active_tokens`

#### 2.2.3 tiktoken 编码选择

使用 `cl100k_base` 编码：
- 对 OpenAI 模型（GPT-4、GPT-4o）精确
- 对非 OpenAI 模型（GLM、DeepSeek、Qwen）是近似，误差 ±10%
- 但比字节计数（误差 50%+）显著改善

#### 2.2.4 依赖注入模式

遵循现有 `CheckpointSummarizerPort` 的 DI 模式：
- Domain 层定义 `TokenCounterPort`（Protocol）
- Infrastructure 层实现 `TiktokenCounterAdapter`
- 通过构造函数注入到 `OpenAiAgentRuntime`

### 2.3 默认阈值选择

| 参数 | 旧字节值 | 新 token 值 | 依据 |
|------|---------|------------|------|
| TRIGGER | 128 KB | 48000 | 32K tokens(活跃尾部) + ~16K tokens(instructions+tools 固定开销) |
| MIN | 1 KB | 512 | 最小有意义阈值 |
| MAX | 100 MB | 500000 | 任何超过 500K token 的上下文都超出实际模型能力 |
| HARD GAP | 64 KB | 16000 | 64KB ÷ 4 |

## 3. 实施计划

### 3.1 依赖添加

**文件**：`backend/pyproject.toml`

```toml
[project.dependencies]
# 添加
"tiktoken>=0.7,<1"
```

**验证**：`uv sync --project backend`

### 3.2 提升 `_canonical_json` 到 Domain 层

**新文件**：`backend/src/codelens/review/domain/canonical_json.py`

- 从 `context_checkpoint.py` 的 `_canonical_json` 函数移到独立模块
- `context_checkpoint.py` 和 `token_counter.py` 都从 Domain import，避免 Infrastructure 反向依赖

### 3.3 定义 `TokenCounterPort`（Domain）

**新文件**：`backend/src/codelens/review/domain/token_counter.py`

```python
from typing import Protocol

class TokenCounterPort(Protocol):
    """Token 计数端口，支持注入不同的 tokenizer 实现。"""
    
    def count(self, text: str) -> int:
        """对纯文本计数 token。"""
        ...
    
    def count_json(self, value: object) -> int:
        """对结构化 JSON 计数 token（序列化为 canonical JSON 后计数）。"""
        ...
```

### 3.4 实现 `TiktokenCounterAdapter`（Infrastructure）

**新文件**：`backend/src/codelens/review/infrastructure/token_counter.py`

```python
import tiktoken
from codelens.review.domain.token_counter import TokenCounterPort
from codelens.review.domain.canonical_json import canonical_json

class TiktokenCounterAdapter:
    """基于 tiktoken 的 token 计数器适配器。
    
    使用 cl100k_base 编码，对 OpenAI 模型精确，对非 OpenAI 模型（GLM/DeepSeek/Qwen）
    是近似，误差 ±10%，但比字节计数（误差 50%+）显著改善。
    
    tiktoken encode 是纯 CPU 同步调用且极快（<10ms），可在事件循环内直接调用。
    """
    
    def __init__(self) -> None:
        self._encoding = tiktoken.get_encoding("cl100k_base")
    
    def count(self, text: str) -> int:
        return len(self._encoding.encode_ordinary(text))
    
    def count_json(self, value: object) -> int:
        return self.count(canonical_json(value))
```

### 3.5 改 `ToolLimits` 配置

**文件**：`backend/src/codelens/review/domain/tool_limits.py`

**改动**：

| 旧名 | 新名 | 旧值 | 新值 |
|------|------|------|------|
| `DEFAULT_CONTEXT_COMPACTION_TRIGGER_BYTES` | `DEFAULT_CONTEXT_COMPACTION_TRIGGER_TOKENS` | `128 * 1024` | `48000` |
| `MIN_CONTEXT_COMPACTION_BYTES` | `MIN_CONTEXT_COMPACTION_TOKENS` | `1024` | `512` |
| `MAX_CONTEXT_COMPACTION_BYTES` | `MAX_CONTEXT_COMPACTION_TOKENS` | `100 * 1024 * 1024` | `500000` |
| 字段 `context_compaction_trigger_bytes` | `context_compaction_trigger_tokens` | — | — |
| `_MINIMUM_HARD_WATERMARK_GAP_BYTES` | `_MINIMUM_HARD_WATERMARK_GAP_TOKENS` | `64 * 1024` | `16000` |

**注意**：`__post_init__` 中的边界检查也要同步改名。

### 3.6 改 `context_checkpoint.py` 核心计算

**文件**：`backend/src/codelens/review/infrastructure/context_checkpoint.py`

#### 3.6.1 签名变更

`build_context_checkpoint_filter` 新增参数：

```python
def build_context_checkpoint_filter(
    *,
    limits: ToolLimits,
    prompt: str,
    tracker: ContextCheckpointTracker,
    summarizer: CheckpointSummarizerPort,
    loop_reset_signal: ToolLoopResetSignal | None = None,
    token_counter: TokenCounterPort,  # 新增，必传
) -> Callable[[CallModelData[object]], Awaitable[ModelInputData]]:
```

#### 3.6.2 固定开销缓存

在 `ContextCheckpointTracker` 中新增字段：

```python
@dataclass
class ContextCheckpointTracker:
    # ... 现有字段 ...
    fixed_overhead_tokens: int | None = None  # instructions + tools 的 token 数，首次计算后缓存
```

首次调用时计算并缓存：

```python
if tracker.fixed_overhead_tokens is None:
    instructions_tokens = token_counter.count(data.model_data.instructions or "")
    tools_tokens = token_counter.count_json(_serialize_tool_definitions(data.agent))
    tracker.fixed_overhead_tokens = instructions_tokens + tools_tokens
```

#### 3.6.3 计算变更

| 旧逻辑 | 新逻辑 |
|--------|--------|
| `active_bytes = sum(len(_json_text(item).encode("utf-8")) for item in ...)` | `active_tokens = sum(token_counter.count_json(item) for item in ...)` |
| `if active_bytes < limits.context_compaction_trigger_bytes` | `if fixed_overhead_tokens + active_tokens < limits.context_compaction_trigger_tokens` |
| `_hard_watermark` 使用 `trigger_bytes * 2` | 使用 `trigger_tokens * 2` |
| `selected_bytes` | `selected_tokens = sum(item.token_count for item in round_.evidence)` |
| `tracker.original_bytes += selected_bytes` | `tracker.original_tokens += selected_tokens` |
| `tracker.compressed_bytes += len(_canonical_json(checkpoint_item).encode("utf-8"))` | `tracker.compressed_tokens += token_counter.count_json(checkpoint_item)` |

#### 3.6.4 字段改名

- `ContextCheckpointTracker`：
  - `original_bytes` → `original_tokens`
  - `compressed_bytes` → `compressed_tokens`
- `_EvidenceOutput`：
  - `encoded_size` → `token_count`
- `CheckpointEvidenceReference`（Pydantic Field）：
  - `original_bytes` → `original_tokens`
- `_evidence_reference` 参数：
  - `encoded_size` → `token_count`
- `_complete_rounds`：
  - 新增 `token_counter` 参数
  - 内部计算 `token_count = token_counter.count_json(output)`

#### 3.6.5 工具定义序列化

新增私有函数：

```python
def _serialize_tool_definitions(agent: Agent[Any]) -> str:
    """序列化 agent 的工具定义为 JSON 字符串，供 token 计数。"""
    from agents.tool import FunctionTool
    
    tools_json = []
    for tool in agent.tools:
        if isinstance(tool, FunctionTool):
            tools_json.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.params_json_schema,
                    "strict": tool.strict_json_schema,
                }
            })
    return _canonical_json(tools_json)
```

### 3.7 诊断链改名

以下所有层中 `context_compaction_original_bytes` → `context_compaction_original_tokens`，`context_compaction_compressed_bytes` → `context_compaction_compressed_tokens`：

#### 3.7.1 Domain 层

**文件**：`backend/src/codelens/review/domain/ports.py`

- `UnvalidatedAgentOutput` 字段改名（L119-120）

#### 3.7.2 Application 层

**文件**：`backend/src/codelens/review/application/process_report.py`

- `ReviewProcessReport` 字段改名（L131-132）
- `_AgentAccumulator` 字段改名（L171-172）
- `_accumulate_usage` metadata key 改名（L477-484）：`_non_negative_int(metadata, "context_compaction_original_tokens")`
- `_accumulate_run_diagnostics` metadata key 改名（L535-543）
- `sum(agent.context_compaction_original_tokens ...)`（L398-402）

**文件**：`backend/src/codelens/review/application/orchestrator.py`

- lifecycle event metadata key 改名（L533-537）

#### 3.7.3 Infrastructure 层

**文件**：`backend/src/codelens/review/infrastructure/openai_runtime.py`

- 构造 `UnvalidatedAgentOutput` 时值来源改名（L733-734）：`tracker.original_tokens`/`tracker.compressed_tokens`
- SSE 事件 metadata key 改名（L1052-1056）

**文件**：`backend/src/codelens/worker/execution.py`

- model_output 事件 metadata key 改名（L531-537）

#### 3.7.4 Interface 层

**文件**：`backend/src/codelens/interface/http/dto.py`

- `AgentProcessResponse` 字段改名（L630-634）
- `ReviewProcessReportResponse` 字段改名（L662-666）

**兼容性说明**：旧任务 transcript 中的旧 metadata key 读不到 → `_non_negative_int` 默认 0。这是可接受损失，不写回退路径。

### 3.8 `file_tool_limits.py` 向后兼容

**文件**：`backend/src/codelens/review/infrastructure/file_tool_limits.py`

**改动**：

- `_ToolLimitsPayload` TypedDict：`context_compaction_trigger_bytes` → `context_compaction_trigger_tokens`
- `_INT_FIELDS`：改名
- `save_tool_limits`：写新字段名
- `get_tool_limits`：兼容读取

```python
# 兼容读取逻辑
def get_tool_limits() -> ToolLimits:
    payload = _read_payload()
    
    # 新字段名
    if "context_compaction_trigger_tokens" in payload:
        trigger_tokens = payload["context_compaction_trigger_tokens"]
    # 旧字段名兼容
    elif "context_compaction_trigger_bytes" in payload:
        trigger_tokens = payload["context_compaction_trigger_bytes"] // 4  # 粗略折算
        _LOGGER.warning(
            "Migrating legacy context_compaction_trigger_bytes to tokens, "
            "please re-verify in settings UI"
        )
    else:
        trigger_tokens = DEFAULT_CONTEXT_COMPACTION_TRIGGER_TOKENS
    
    # ... 其他字段处理 ...
```

### 3.9 HTTP 接口层

**文件**：`backend/src/codelens/interface/http/dto.py`

- `ToolLimitsResponse.context_compaction_trigger_tokens`（L203）
- `UpdateToolLimitsRequest.context_compaction_trigger_tokens`（L227-229）
- `Field(ge=512, le=500000)`

**文件**：`backend/src/codelens/interface/http/routers/settings.py`

- `_tool_limits_response` 字段改名（L104）
- `update_tool_limits` 字段改名（L462）

### 3.10 前端 + i18n

#### 3.10.1 TypeScript 类型

**文件**：`frontend/src/features/reviews/api.ts`

- L111-112：`context_compaction_original_bytes` → `_tokens`
- L141-142：`compressed_bytes` → `_tokens`

**文件**：`frontend/src/features/settings/types.ts`

- L123：字段改名

#### 3.10.2 UI 组件

**文件**：`frontend/src/features/reviews/ReviewProcessReport.tsx`

- L138-139：字段引用改名
- L250-254：跨 agent 汇总求和
- 标签语义："bytes" → "tokens"

**文件**：`frontend/src/features/settings/SettingsPage.tsx`

- L1294-1308：
  - 字段名 `context_compaction_trigger_tokens`
  - 显示单位从 KB 改为 token：去掉 `/ BYTES_PER_KILOBYTE` 与 `* BYTES_PER_KILOBYTE`，直接用 token 原值
  - `min={512}`、`max={500000}`、`step={500}`

#### 3.10.3 i18n 翻译

**文件**：`frontend/src/shared/i18n/i18n.tsx`

- EN L407-408：`"Evidence tokens before/after compaction"`
- EN L654：`"Context compaction trigger (tokens)"`
- ZH L1061-1062：`"压缩前/后证据 token 数"`
- ZH L1304：`"上下文压缩触发阈值（token）"`

### 3.11 测试修改

#### 3.11.1 核心测试

**文件**：`backend/tests/unit/review/test_context_compaction.py`

**策略**：使用 mock `TokenCounterPort`（返回可控整数），而不是真实 tiktoken。

```python
class FakeTokenCounter:
    """测试用假 token 计数器，返回可控整数。"""
    
    def count(self, text: str) -> int:
        return len(text) // 4  # 简化估算
    
    def count_json(self, value: object) -> int:
        return len(canonical_json(value)) // 4
```

- 所有 `build_context_checkpoint_filter` 调用补 `token_counter=` 参数
- `ToolLimits(context_compaction_trigger_tokens=3000)` 替换旧字节阈值
- 配合 `FakeTokenCounter` 重现"软水位触发""硬水位抛错"等场景

#### 3.11.2 tiktoken 集成测试

**新文件**：`backend/tests/unit/review/test_token_counter.py`

```python
def test_tiktoken_counter_known_string():
    """验证 TiktokenCounterAdapter 对已知字符串返回预期值。"""
    from codelens.review.infrastructure.token_counter import TiktokenCounterAdapter
    
    counter = TiktokenCounterAdapter()
    
    # "hello world" 在 cl100k_base 下是 2 个 token
    assert counter.count("hello world") == 2
    
    # 中文 "你好世界" 在 cl100k_base 下约 4-5 个 token
    assert 4 <= counter.count("你好世界") <= 6
```

#### 3.11.3 其他测试

**文件**：`backend/tests/unit/review/test_tool_limits.py`

- L75：默认值 `48000`，边界 `512`/`500000`

**文件**：`backend/tests/unit/review/test_process_report.py`

- L469-470, L495-496：metadata key 改 `_tokens`

**文件**：`backend/tests/contract/http/test_tool_limits_api.py`

- L32, L59：API 字段名 + 约束范围

**文件**：`frontend/src/features/reviews/ReviewConsole.test.tsx`

- L514-518：测试数据字段改名

### 3.12 ARCHITECTURE.md 同步

**文件**：`docs/ARCHITECTURE.md`

- L258：把"被覆盖 round/结果/字节"中的"字节"改"token"
  - 新增一句："软/硬水位以本地 tiktoken 估算的 token 计，包含系统提示+工具定义+对话历史，对非 OpenAI 模型使用 cl100k_base 近似"
- L260：过程报告统计项里"被覆盖 round/结果/字节"改"被覆盖 round/结果/token"

## 4. 验证步骤

### 4.1 后端验证

```bash
# 安装依赖
uv sync --project backend

# 代码检查
uv run --project backend ruff check backend
uv run --project backend mypy backend/src

# 单元测试
uv run --project backend pytest backend/tests -v

# 特别关注
uv run --project backend pytest backend/tests/unit/review/test_context_compaction.py -v
uv run --project backend pytest backend/tests/unit/review/test_token_counter.py -v
uv run --project backend pytest backend/tests/unit/review/test_tool_limits.py -v
uv run --project backend pytest backend/tests/unit/review/test_process_report.py -v
uv run --project backend pytest backend/tests/contract/http/test_tool_limits_api.py -v
```

### 4.2 前端验证

```bash
# 安装依赖
pnpm --dir frontend install

# 测试
pnpm --dir frontend test

# 构建
pnpm --dir frontend build
```

### 4.3 集成验证

1. 启动后端服务：`uv run --project backend codelens-review start`
2. 启动前端：`pnpm --dir frontend dev`
3. 创建一个 review 任务
4. 观察 process report 中的压缩指标是否正确显示 token 数
5. 在设置页面修改压缩触发阈值，验证单位是否为 token

### 4.4 向后兼容验证

1. 在旧版本（字节计数）下创建一个 review 任务
2. 升级到新版本（token 计数）
3. 验证旧任务的 process report 是否能正常显示（压缩指标应为 0）
4. 验证 `tool-limits.json` 中的旧 `context_compaction_trigger_bytes` 字段是否能正确迁移

## 5. 风险与取舍

### 5.1 tiktoken 对非 OpenAI 模型是近似

- GLM/DeepSeek/Qwen 的真实 tokenizer 不同，误差 ±10%
- 但比字节计数（误差 50%+）显著改善
- 在 `TiktokenCounterAdapter` docstring 记录该取舍

### 5.2 token 计数性能

- 每次 `build_context_checkpoint_filter` 调用全量重算 active tail
- tiktoken encode 比字节计数慢约 100x，但绝对值仍 <10ms（千行内）
- 不构成瓶颈，若后续发现超长 context 慢，再在 tracker 加增量缓存

### 5.3 旧 transcript 的 bytes metadata

- 恢复中的旧任务该指标读不到 → 0
- `usage_is_complete` 会正确标记 false
- 这是可接受损失，按 `feedback_data_compatibility_refactoring` 不写回退路径

### 5.4 旧 `tool-limits.json`

- `get_tool_limits` 兼容读 + 折算 + 迁移保存
- 用户零感知
- 折算 `// 4` 粗略但可在 UI 校准

### 5.5 API 破坏性

- `context_compaction_trigger_bytes` 字段改名是破坏性 API 改动
- 但 CodeLens 是本地工作台（无外部消费者），前端同批改动即可，无需版本化 API

## 6. 关键文件清单

### 6.1 新增文件

- `backend/src/codelens/review/domain/canonical_json.py`
- `backend/src/codelens/review/domain/token_counter.py`
- `backend/src/codelens/review/infrastructure/token_counter.py`
- `backend/tests/unit/review/test_token_counter.py`

### 6.2 修改文件

**后端**：
- `backend/pyproject.toml`
- `backend/src/codelens/review/domain/tool_limits.py`
- `backend/src/codelens/review/domain/ports.py`
- `backend/src/codelens/review/infrastructure/context_checkpoint.py`
- `backend/src/codelens/review/infrastructure/openai_runtime.py`
- `backend/src/codelens/review/infrastructure/file_tool_limits.py`
- `backend/src/codelens/review/application/process_report.py`
- `backend/src/codelens/review/application/orchestrator.py`
- `backend/src/codelens/worker/execution.py`
- `backend/src/codelens/interface/http/dto.py`
- `backend/src/codelens/interface/http/routers/settings.py`
- `backend/tests/unit/review/test_context_compaction.py`
- `backend/tests/unit/review/test_tool_limits.py`
- `backend/tests/unit/review/test_process_report.py`
- `backend/tests/contract/http/test_tool_limits_api.py`

**前端**：
- `frontend/src/features/reviews/api.ts`
- `frontend/src/features/reviews/ReviewProcessReport.tsx`
- `frontend/src/features/reviews/ReviewConsole.test.tsx`
- `frontend/src/features/settings/SettingsPage.tsx`
- `frontend/src/features/settings/types.ts`
- `frontend/src/shared/i18n/i18n.tsx`

**文档**：
- `docs/ARCHITECTURE.md`

## 7. 实施顺序

按"先改 Domain → 再改 Infrastructure 核心 → 再改诊断链 → 再改 HTTP/前端 → 最后改测试与文档"的顺序，保证 mypy strict 在每一步都能局部通过：

1. `pyproject.toml` + `uv sync`（依赖到位）
2. `domain/canonical_json.py`（提升 helper）
3. `domain/token_counter.py`（Port）
4. `infrastructure/token_counter.py`（Adapter）
5. `domain/tool_limits.py`（字段改名）
6. `infrastructure/context_checkpoint.py`（核心计算 + tracker/reference 字段）
7. `domain/ports.py` + `application/process_report.py` + `application/orchestrator.py` + `infrastructure/openai_runtime.py` + `worker/execution.py`（诊断链）
8. `infrastructure/file_tool_limits.py`（兼容读）
9. `interface/http/dto.py` + `routers/settings.py`（HTTP）
10. 前端 6 文件
11. 测试文件
12. `docs/ARCHITECTURE.md`

每步后跑 `uv run --project backend ruff check backend` + `uv run --project backend mypy backend/src`（前端 `pnpm --dir frontend build`），最后整跑验证。

## 8. 参考资源

- [How to count tokens with tiktoken](https://developers.openai.com/cookbook/examples/how_to_count_tokens_with_tiktoken/)
- [Token 计数逻辑模块化方案](https://github.com/deep-copilot/DeepCopilot/issues/149)
- [AI 实践(3)Token 与上下文窗口](https://blog.csdn.net/Once_day/article/details/158839105)

## 9. 总结

本方案将 CodeLens 的上下文压缩机制从字节计数改为 token 计数，使用 tiktoken 进行本地精确计数。改动涉及 9 个层级，需要严格遵循 DDD 分层和依赖方向。通过 Port-Adapter 模式保持依赖注入的灵活性，通过向后兼容处理保证旧数据平滑迁移。最终目标是更精确地控制上下文窗口，避免字节近似带来的误差。
