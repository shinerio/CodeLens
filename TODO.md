# CodeLens TODO

## 待优化

codelens.worker.singleton.WorkerAlreadyRunningError: another Worker owns the singleton lock 这个报错是什么情况？每个仓库同时只能有一个review吗？需要支持并发创建多个review的，使用不同worktree即可。review任务被删除后，清理对应worktree即可。

【精简测试用例】梳理并清理一下存量测试用例，删除一些已经删除的，不再使用的代码逻辑的测试，删除一些没必要的、已经不存在代码实现的兼容性测试。举例：校验不存在的代码或已删除的，输出不包含xxx之类的测试用例都可以删了，只是重构过程中残留下的无用测试用例。

检查目前的输出中是否包含了标题、说明、建议、类别、严重性和置信度，comment工具是否完整支持了这些能力，这些都是是否都已经在web页面给用户看到的了。不同的严重性意见应该用不同颜色的背景色来加以区分。

大模型的激活按钮超出显示区域了。最右边仅保留日志级别即可，下面的凭证相关说明不需要在web页面体现。

限制每个review.md和agents.md的最大行数，根目录可以适当放端，支持settings页面可配置，并提示建议值。

## 后续功能

- 支持用户人工划词 review，并学习用户的 review 意见，放到根目录 `REVIEW.md`、当前文件夹下`REVIEW.md` 或指定文件的 `<filename>.review.md` 中（放到最合适的一处即可）。
- review.md，支持每个agent定义专属<agent_name>.review.md文件。review.md为所有agent共享约束。
- 主AGENT输出完整报告前，需要二次确认是否存在误报的REVIEW
- 支持REVIEW意见的整理，然后输出为长久记忆·
- 检查是否限制每个agents.md和review.md的大小，已经总instructions大小，避免无法输入其他代码内容或其他提示词
- windows平台兼容性检测

## 前端预览页待接入功能

以下页面已按 demo 提供可浏览的界面和路由；当前没有稳定后端契约的操作统一显示“暂未支持”，不得把
预览数据当作真实运行状态。

- 新建 Review：接入完整 Reviewer 多选、全选、执行预算和置信度阈值；提交时必须由后端校验所选 Agent 和预算，而不是由前端伪造可执行配置。
- Review Agents：接入 Reviewer Catalog 的查询、搜索、来源筛选、刷新、版本历史、创建草稿、编辑、发布与删除；
  静态目录应替换为版本化 API 数据。
- Capabilities：接入 Skill、MCP、静态工具和 Context Provider 的目录、搜索、信任筛选、健康检查、连接、配置与
  审计记录；能力信任决策必须经后端策略边界执行。
- Review Runs：补充刷新、筛选和排序；运行列表仍需以持久化任务数据为唯一来源。
- Review 详情：接入取消任务、复制永久链接、导出报告和 Finding 抑制/确认；取消、抑制和确认必须具有明确的持久化状态与审计语义。
- Artifacts：实现任务 Artifact 浏览、报告导出与下载契约；不得向前端暴露未受控的工作区路径或任意文件访问。
- Settings：补齐 General、Security、Storage、Network 等 demo 设置页的后端配置契约与前端接入；当前已完成的
  模型网关 CRUD/激活功能不应被这些预览项替代。
- 移动端：补齐 demo 的可展开侧栏导航和相关键盘/焦点管理。

## 延期的并发、协作与部署能力

以下事项不属于单用户首版。首版仍支持同仓库不同 feature/ref 的 ReviewTask 并发，以及同一任务内
多个 Reviewer 并发；每个 ReviewTask 使用独立 task-owned worktree。

- 多用户身份认证、会话、CSRF、RBAC、审计主体和租户数据隔离。
- `0.0.0.0`、受信任内网、反向代理和互联网部署；完成认证授权设计前不得开放无鉴权远程模式。
- 同一 data directory 的多 Worker/多实例调度，包括 job lease、heartbeat、generation/fencing token、
  僵尸 Worker 写入防护、任务抢占和进程间公平调度。
- 跨进程/跨主机的 repository/worktree lock、孤儿 worktree 协调回收和分布式限流。
- 多用户/多 Worker 场景下的模型、MCP、命令预算配额、限流公平性和成本归属。
