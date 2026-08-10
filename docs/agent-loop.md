# Reviewer Agent Loop v2

Reviewer 的模型可见循环只有三类动作：读取冻结证据、提交 Comment v2、请求完成。

```text
receive review_files
  -> get_diff(file or directory)
  -> optional find_files / grep / read_file
  -> comment(...)
  -> task_done(summary)
       |- missing diff files: reject and continue
       `- complete: stop loop
```

## 完成语义

- 宿主以 `ReviewFileScope.review_paths` 为全集。
- 只有模型可见 `get_diff` 完整返回的文件计入 diff coverage。
- `read_file` 用于上下文调查，不替代变更 diff 覆盖。
- `task_done` 不接收文件计数或已完成文件列表。
- 缺失时返回稳定错误码、剩余数量和有界路径列表；模型可以用目录 `get_diff` 批量补齐。
- 超过不完整重试上限后沿现有 partial fallback 结束，checkpoint 保存准确缺失路径。

## Comment

`comment:v2` 要求模型提交 path、side、existing code、标题、正文、建议和分类轴。宿主在冻结 Snapshot 中重新解析位置和证据；同一批次中的无效项不会丢弃其他有效项。

运行时不提供文件写入、Shell、网络、任意 Git、动态工具发现或文件完成声明工具。
