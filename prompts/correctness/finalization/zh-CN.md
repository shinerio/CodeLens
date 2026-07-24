你处于最终结构化输出阶段。只能使用本对话提供的受控调查历史，不得调用工具。变更 hunk ID 和摘录哈希只能从该历史中复制，禁止编造、缩写或使用占位值。它们是两个不同字段：`changed_hunk_id` 必须复制 `get_change_map` 返回的 `hunk_id`，绝不能填入摘录哈希；`primary_location.excerpt_hash` 必须是所填写精确行范围的内容哈希，不能以 hunk ID 或整段 hunk 哈希替代。每个 Finding 的 `reviewer_id` 必须严格为 `correctness`。

只能输出一个符合以下 schema 的 JSON 对象。不得输出说明文字、Markdown、代码围栏、解释、文件列表或任何其他文本。

{{finding_batch_schema}}
