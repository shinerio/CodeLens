# ADR-0007：使用任务内 `comment` 工具生成最终 Review 意见

## 状态

已接受。

## 背景

部分 OpenAI 兼容网关不支持原生结构化输出，且模型最终文本可能错误地重放调查工具调用或夹带不可信 JSON。直接从模型最终文本解析 FindingBatch 会把 hunk ID、摘要哈希和代码位置等不可信值带入输出路径。

`open-code-review` 的模式是在调查期间通过评论工具收集意见，再由 diff 解析器定位评论。CodeLens 需要保留其冻结 Snapshot、Manifest 与内容哈希安全边界。

## 决策

Review Runtime 提供每 Agent Run 独有的有状态 `comment` 和 `task_done` 工具。模型用 `comment` 批量提交候选评论的路径、行范围、标题、说明、建议、类别、严重性和置信度，并用 `task_done` 声明调查完成及已检查的变更文件数。

该工具只保存进程内候选列表。它使用任务冻结的 Snapshot 验证路径和范围，要求范围完整位于唯一的新侧变更 hunk，并从冻结内容读取派生 excerpt hash 与 hunk ID。未解析的候选不会保留。运行结束时，Runtime 把已解析候选转换为现有版本化 FindingBatch，再交由既有 FindingValidator 做最终领域校验、去重和报告。

调查 Agent 的最终文本不参与最终意见解析，也不再进行单独的模型 JSON 生成请求。

## 后果

- 不依赖 `response_format` 支持，并消除模型最终文本解析路径的工具调用干扰。
- 模型不能伪造输出位置、hunk ID 或内容哈希；不在变更内的意见不会出现在报告中。
- `comment` 是受限的任务内状态，不是通用可变 Agent 权限；它不写文件、数据库或 Artifact。
- 空评论集合表示该 Agent 没有已验证的 Findings。
