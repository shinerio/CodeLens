# ADR-0001：运行期模型网关 Secret Store

**状态：** Accepted  
**日期：** 2026-07-18

## 背景

CodeLens 的 API 和 Worker 必须能在没有模型凭证时独立启动，同时允许本机用户随后通过 Web 保存多个 OpenAI-compatible 网关并切换当前激活网关。API 与 Worker 是独立进程，不能依赖进程内共享状态；架构又禁止 Secret 进入数据库、普通配置、日志、事件、Prompt 和错误响应。

## 决策

- `/api/settings/model-gateways` 提供网关集合 CRUD，`PUT /api/settings/active-model-gateway` 切换当前网关；`GET/PUT /api/settings/openai` 仅保留兼容性。
- API Key 是只写字段；读取响应只暴露网关 ID、名称、模型、Base URL 和激活状态。
- `reviewer_catalog` 定义网关目录、激活网关不变量和 Secret Store Port。
- 文件适配器将目录写入 data directory 下的 `secrets/model-gateways.json`，目录权限为 `0700`、文件权限为 `0600`，并通过同目录临时文件和原子替换更新。旧 `openai-provider.json` 在首次读取后迁移。
- Worker 组合根不校验 Provider 配置；OpenAI Runtime 只在真正调用 Reviewer 时读取最新配置并构造独立 SDK Client。
- 第一条网关自动激活；之后的创建不会意外改变当前运行时，删除当前网关时选择剩余目录中的第一条作为新激活网关。
- Web 页面提示远程 HTTP Base URL 不具备传输加密，但允许本机操作者在明确受信任的网络边界中使用。

## 结果

- API、Worker 和前端可在未配置模型时启动和重启。
- 独立 API 与 Worker 可通过同一 data directory 共享最新目录与激活选择。
- Secret 不进入 SQLite 或读取 API，但仍以 owner-only 明文文件存在；data directory 的操作系统账户安全成为信任边界。
- 将来接入系统 Keychain、Vault 或其他 Secret Store 时，只需替换 Port Adapter，不改变 Domain、Web 契约或 Worker 编排。
