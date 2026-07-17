# CodeLens TODO

## 后续功能

- 支持用户人工划词 review，并学习用户的 review 意见，放到根目录 `REVIEW.md`、当前文件夹下
  `REVIEW.md` 或指定文件的 `<filename>.review.md` 中（放到最合适的一处即可）。
- 支持 `rules/` 目录下每种 Agent 提供一个单独的同名 `.review.md` 文件。
- 主AGENT输出完整报告前，需要二次确认是否存在误报的REVIEW
- 支持REVIEW意见的整理，然后输出为长久记忆
- 每个AGENT支持修改提示词（保留默认提示词，支持重置为默认）

## 延期的并发、协作与部署能力

以下事项不属于单用户首版。首版仍支持同仓库不同 feature/ref 的 ReviewTask 并发，以及同一任务内
多个 Reviewer 并发；每个 ReviewTask 使用独立 task-owned worktree。

- 多用户身份认证、会话、CSRF、RBAC、审计主体和租户数据隔离。
- `0.0.0.0`、受信任内网、反向代理和互联网部署；完成认证授权设计前不得开放无鉴权远程模式。
- 同一 data directory 的多 Worker/多实例调度，包括 job lease、heartbeat、generation/fencing token、
  僵尸 Worker 写入防护、任务抢占和进程间公平调度。
- 跨进程/跨主机的 repository/worktree lock、孤儿 worktree 协调回收和分布式限流。
- 同一 apply target 的并发自动合入、跨 FixTask 冲突仲裁、队列优先级和失败恢复。
- Reviewer 可写工作区时的 per-Agent copy-on-write worktree/sandbox；首版 Reviewer 只读共享 task worktree。
- 多用户/多 Worker 场景下的模型、MCP、命令预算配额、限流公平性和成本归属。
