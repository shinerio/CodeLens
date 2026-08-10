# Built-in Review Tools v2

模型只获得冻结 Capability Profile 明确列出的 v2 工具。所有路径使用仓库相对 POSIX 形式，所有正文来自已验证 `ReviewSnapshot`。

| 工具 | 作用 | 关键边界 |
| --- | --- | --- |
| `find_files:v2` | 查找可见 Snapshot 路径 | 目录 + glob，稳定排序，有界结果 |
| `grep:v2` | 搜索可见文本 | 路径、文件 glob、扫描和正则超时限制 |
| `read_file:v2` | 读取 current/base/head 文本 | 行数、字节、哈希和版本限制 |
| `get_diff:v2` | 读取文件或目录下的 Review diff | 目录递归、游标分页、只返回 `review_paths` |
| `comment:v2` | 提交一个或一批 Candidate 评论 | 每项独立校验并保留有效项 |
| `task_done:v2` | 请求结束 Reviewer Loop | 未完整读取所有 Review diff 时拒绝 |
| `submit_review_plan:v2` | 提交 Adaptive 选择 | 只能选择冻结 Catalog 中 Ready Reviewer |
| `finalize_plan:v2` | 完成 Planner | 校验计划完整性 |
| `verdict:v2` | Accept/Deny 已有 Cluster | 参数仅 `cluster_ids`、`action` |
| `merge:v2` | 合并或重写已有 Cluster | 全部 Comment 字段必填，允许单 Cluster |
| `finalize_verdicts:v2` | 完成 Verifier | 每个 Cluster 恰好覆盖一次 |

## get_diff:v2

`path` 精确命中 Review 文件时返回该文件；否则按目录前缀递归读取。空路径表示 Review Scope 根目录。响应按 path 稳定排序并包含 `has_more`、`next_cursor`；只有本页完整返回的文件计入完成覆盖。被统一排除的文件既不能精确读取，也不能通过目录批量读取重新出现。

## Verdict 与 Merge

批量 Verdict 不产生隐式合并。Merge 的内容、类别、严重级别、维度、证据强度和位置以 Verifier 提交为准，不受 Candidate 最高严重级别限制。Cluster/Candidate/Reviewer 来源、行号、excerpt hash、Finding ID 和 fingerprint 始终由宿主产生。

当前工具集中不存在旧版 Tool Contract 或文件完成声明工具。
