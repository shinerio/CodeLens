# ADR 0004: MVP 使用内置 Snapshot Review 工具

## 状态

已接受

## 背景

原始 Review Runtime 在一次模型调用前预先组装变更 hunk 和有限文件上下文。大型变更会稀释关键证据，且模型不能自主调查未修改但相关的调用方、测试或旧版本代码。

产品 MVP 必须在所有用户机器上可靠运行，不能假设用户预装 Serena、CodeGraph、codebase-memory、语言服务器或任何 MCP 服务。

## 决策

CodeLens 在每个 Agent Run 中挂载自身实现的只读 Function Tools：`explore`、`glob`、`grep`、`read_file`、`get_change_map`、`get_diff` 与 `read_revision`。

工具只读取当前任务冻结的 `ReviewSnapshot`：文件操作必须限制在 Manifest 的 target/context 项，读取前验证内容哈希；旧版本读取只允许固定 base/head OID。工具调用具有固定的结果、读取和总调用预算。工具不会写文件、执行 Shell、访问网络或访问用户原始工作区。

OpenAI Agent Runtime 在调用前基于该 Run 的 Snapshot 创建工具实例。工具驱动的初始输入不预载变更 hunk 或候选文件正文，仅包含工具使用指引、规则、输出契约和 Snapshot 标识；Agent 先调用 `get_change_map`，再按需调查代码。其输入中不包含 worktree 或原始仓库路径。

## 后果

Review 从预拼装上下文升级为受控的调查式流程，且无需任何额外本机安装。MVP 不提供语义代码导航、第三方 MCP、Skills 或外部代码图；这些功能以后可经版本化 Capability Profile 和 Adapter 增加，而不改变 Agent 可见的稳定工具契约。
