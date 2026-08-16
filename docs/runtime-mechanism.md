# Multi-Agent Review v2 Runtime Mechanism

本文是运行机制导读；权威约束见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)，执行改造见 [`2026-08-09-multi-agent-review-v2-hard-cut.md`](debet/plans/2026-08-09-multi-agent-review-v2-hard-cut.md)。

## 冻结输入

任务创建时冻结 Git target、workspace overlay、v2 Reviewer Selection、Prompt/Capability artifacts 和 File Exclusion Policy。Worker 在任务自有 worktree 中解析 Repository Instructions、`.gitignore`、用户后缀/正则和二进制 facts，生成并持久化唯一 `ReviewFileScope`。

Snapshot、ChangeIndex、Planner、Reviewer、Verifier 和 Finding 只消费该 Scope。Instruction 控制文件独立捕获，不因模型不可见而丢失。

## Agent Loop

Reviewer 从 `review_files` 开始调查，使用只读 Snapshot 工具获取证据，通过 `comment:v2` 提交 Candidate。`get_diff:v2` 可读取文件或目录；完整返回的 Review 文件由宿主自动记录。`task_done:v2` 在仍有未读取 diff 时拒绝并返回缺失路径，接受后立即结束模型 Loop。

## 聚合与发布

有效 Candidate 经过冻结位置和证据校验后聚类。多 Specialist 的 `review-verifier:v2` 只能引用已有 Cluster：

- `verdict(cluster_ids, accept|deny)` 分别处理各 Cluster；
- `merge(cluster_ids, ...)` 用完整模型字段产生一个 Finding；
- `finalize_verdicts()` 检查完整且无重复覆盖。

Merge 可覆盖 Candidate 的评论字段和严重级别。宿主仍派生位置行号、excerpt hash、Finding ID、指纹和来源关系，并在一个事务中提交 Verdict、Finding 与事件。

## 恢复

Plan、Scope、Agent execution spec、checkpoint、Candidate、Cluster 和 Verdict 均持久化。普通重启从最后稳定边界继续，不重新解析当前 Catalog、Prompt 或 File Exclusion Settings。
