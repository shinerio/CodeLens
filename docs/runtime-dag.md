# Multi-Agent Review v2 Runtime DAG

本文说明运行期 DAG 的可观察流程；稳定边界以 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 为准。

## 节点顺序

```text
freeze candidate input
  -> resolve and persist ReviewFileScope
  -> compile fixed plan | run review-planner:v2
  -> run v2 reviewers
  -> validate Candidates
  -> cluster Candidates
  -> run review-verifier:v2 when required
  -> publish Findings
  -> complete / partial / failed
```

- Fixed 由宿主直接编译；Adaptive 只允许 `review-planner:v2` 选择冻结 Catalog 中的 v2 Reviewer。
- Reviewer 彼此隔离并持久化 checkpoint。多 Specialist 全部终态后运行单个 Verifier。
- `ReviewFileScope` 在模型调用前持久化，重启不会重新读取当前文件排除设置。
- 全部 Candidate 文件被排除时跳过模型节点并以 0 Findings 完成。
- Verdict 覆盖每个已有 Cluster 恰好一次。Accept/deny 批量参数逐 Cluster 生效，只有 merge 合并 Cluster。
- 任一强制不完整完成产生 sticky partial；取消和失败状态按持久化 DAG 恢复。

## v2 Agent 与工具

| 角色 | 证据工具 | 输出工具 |
| --- | --- | --- |
| Planner | `find_files:v2`、`grep:v2`、`read_file:v2`、`get_diff:v2` | `submit_review_plan:v2`、`finalize_plan:v2` |
| Reviewer | `find_files:v2`、`grep:v2`、`read_file:v2`、`get_diff:v2` | `comment:v2`、`task_done:v2` |
| Verifier | `read_file:v2`、`get_diff:v2` | `verdict:v2`、`merge:v2`、`finalize_verdicts:v2` |

运行时不存在旧版 Agent/Profile/Tool、旧 Comment 输出或文件完成声明工具。
