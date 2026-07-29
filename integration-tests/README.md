# Review 集成测试基线

本目录包含确定性的跨层 Review 回归测试。测试运行时会创建一个临时 Git 仓库，仓库中包含 `main` 分支和 `fixture-change` 分支。目标分支包含一个新增文件、一个删除文件和一个修改文件。测试不需要 Git submodule、本地专用分支、API Key 或网络模型。

Fake 模型 Runtime 会通过生产环境的 `comment` 收集器提交固定的 Review 意见，并故意重复提交其中一条。校验器应当把四条意见去重为三个 Finding，且不能导致 Review 失败。该基线以稳定方式覆盖代码行定位、Finding 校验、持久化、HTTP 响应、执行报告聚合和 React UI。

## 运行自动化集成测试

在项目根目录执行：

```bash
uv sync --project backend
pnpm install
uv run --project backend pytest integration-tests/test_review_pipeline.py -v
pnpm --dir integration-tests test
```

Playwright 测试会创建一个 Review，在 `1280x800` 桌面视口下检查三个 Finding，并确保没有额外创建 Review。启动器会在全新的临时数据目录中启动仅绑定回环地址的后端和前端服务，无论测试成功或失败，结束后都会删除该目录。

可以通过以下环境变量覆盖端口和临时目录的父目录：

- `CODELENS_INTEGRATION_BACKEND_PORT`
- `CODELENS_INTEGRATION_FRONTEND_PORT`
- `CODELENS_INTEGRATION_DATA_DIR`

## 使用真实模型手工验证

以下流程会生成一个可 Review 的本地 Git 仓库，再通过正式 Web 流程调用当前配置的真实模型。不要使用 `run_fake_server.py`，该脚本只适用于确定性自动化测试。

### 1. 安装依赖

在项目根目录执行：

```bash
uv sync --project backend
pnpm --dir frontend install
```

### 2. 生成待 Review 仓库

```bash
uv run --project backend python -c 'import asyncio; from pathlib import Path; from codelens.testing.correctness_fixture import prepare_simple_branch_repository; fixture = asyncio.run(prepare_simple_branch_repository(Path("integration-tests/.tmp/manual-fixture"))); print(fixture.repository.resolve())'
```

命令最后会打印仓库的绝对路径，默认是：

```text
<CodeLens 项目根目录>/integration-tests/.tmp/manual-fixture/simple-branch
```

每次执行都会重新创建该 `simple-branch` 仓库。仓库包含：

- `main`：Review 基线分支。
- `fixture-change`：待 Review 的目标分支。
- `src/cache.py`：新增文件。
- `src/permissions.py`：相对 `main` 被删除的文件。
- `src/state.py`：相对 `main` 被修改的文件。

可以先确认差异：

```bash
git -C integration-tests/.tmp/manual-fixture/simple-branch diff --stat main...fixture-change
```

### 3. 启动正式后端

在一个终端中执行：

```bash
uv run --project backend codelens-review start
```

后端默认监听 `http://127.0.0.1:8800`。该命令启动正式 API、Worker 和真实模型 Runtime；不要与自动化测试使用的 Fake Server 同时占用此端口。

### 4. 启动前端

另开一个终端，在项目根目录执行：

```bash
pnpm --dir frontend dev
```

浏览器打开 `http://127.0.0.1:5173`。

### 5. 配置真实模型网关

打开 **Settings / 设置**，添加并激活一个真实可用的 OpenAI-compatible 模型网关，填写：

- **API Key**：真实访问凭证，不要把它写入仓库、命令历史、日志或截图。
- **Base URL**：例如 `https://api.openai.com/v1`。
- **Model**：网关实际提供的模型 ID。

保存后确认该网关处于激活状态。真实 Review 会把冻结后的仓库内容发送给此网关；非 HTTPS 地址会明文传输凭证和 Review 内容，只能用于明确受信任的网络。

### 6. 在 Web 页面发起 Review

1. 打开 **New Review / 新建 Review**。
2. 点击 **Browse folders / 浏览文件夹**。
3. 按第 2 步打印的绝对路径逐级进入目录，并选择 `simple-branch` Git 仓库。
4. 选择 **Branch diff**。
5. Base 分支选择 `main`，Target 分支选择 `fixture-change`。
6. 不启用工作区改动；选择 `correctness:v1` Reviewer。
7. 点击 **Start review / 开始 Review**。

页面应进入 Review 执行页并实时显示进度。任务终态应为 `completed` 或在覆盖不完整时明确显示 `partial`；随后可以在 **Findings** 和执行报告中检查真实模型产生并通过校验的结果。真实模型输出具有非确定性，因此 Finding 数量和措辞不要求与自动化测试固定的三个 Finding 完全一致；若任务为 `failed`，应在页面的执行过程和 `logs/worker.log` 中检查失败原因。
