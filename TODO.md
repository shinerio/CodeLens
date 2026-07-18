# CodeLens TODO

## 后续功能

- 支持用户人工划词 review，并学习用户的 review 意见，放到根目录 `REVIEW.md`、当前文件夹下
  `REVIEW.md` 或指定文件的 `<filename>.review.md` 中（放到最合适的一处即可）。
- 支持 `rules/` 目录下每种 Agent 提供一个单独的同名 `.review.md` 文件。
- 主AGENT输出完整报告前，需要二次确认是否存在误报的REVIEW
- 支持REVIEW意见的整理，然后输出为长久记忆
- 每个AGENT支持修改提示词（保留默认提示词，支持重置为默认）

## 前端预览页待接入功能

以下页面已按 demo 提供可浏览的界面和路由；当前没有稳定后端契约的操作统一显示“暂未支持”，不得把
预览数据当作真实运行状态。

- 新建 Review：接入完整 Reviewer 多选、全选、执行预算、置信度阈值和 Fix 模式的表单契约；提交时必须由后端
  校验所选 Agent、预算和模式，而不是由前端伪造可执行配置。
- Review Agents：接入 Reviewer Catalog 的查询、搜索、来源筛选、刷新、版本历史、创建草稿、编辑、发布与删除；
  静态目录应替换为版本化 API 数据。
- Capabilities：接入 Skill、MCP、静态工具和 Context Provider 的目录、搜索、信任筛选、健康检查、连接、配置与
  审计记录；能力信任决策必须经后端策略边界执行。
- Review Runs：补充刷新、筛选、排序以及 Review/Fix 混合运行记录；运行列表仍需以持久化任务数据为唯一来源。
- Review 详情：接入取消任务、复制永久链接、导出报告、Finding 抑制/确认，以及从 Finding 创建 Fix 草稿；
  取消、抑制和确认必须具有明确的持久化状态与审计语义。
- Artifacts：实现任务 Artifact 浏览、报告导出与下载契约；不得向前端暴露未受控的工作区路径或任意文件访问。
- Fix 工作区：实现草稿创建、隔离补丁、验证门禁、重新运行门禁、审批、请求修改、下载与应用流程；必须遵守
  `ARCHITECTURE.md` 中的隔离 worktree、fingerprint、冲突检查和人工审批边界。
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
- 同一 apply target 的并发自动合入、跨 FixTask 冲突仲裁、队列优先级和失败恢复。
- Reviewer 可写工作区时的 per-Agent copy-on-write worktree/sandbox；首版 Reviewer 只读共享 task worktree。
- 多用户/多 Worker 场景下的模型、MCP、命令预算配额、限流公平性和成本归属。
