# ADR 0010：按目标文件保留仓库审查规则链

## 状态

已接受

## 背景

Review 可以同时包含多个目录下的目标文件。若只把发现到的 `AGENTS.md` 和 `REVIEW.md` 正文合并成全局数组，模型无法判断嵌套规则适用于哪个目标，也无法可靠解决根目录、子目录和文件专属规则之间的冲突。仅依赖数组顺序或提示词描述不能形成可验证契约。

## 决策

Instruction Policy 对每个目标独立建立从仓库根目录到目标目录的规则链。每一级以大小写不敏感方式依次发现 `AGENTS.md` 和 `REVIEW.md`，文件专属 `<target-file>.review.md` 位于链尾；磁盘实际文件名进入 Snapshot。仅大小写不同的重复逻辑名称视为歧义并拒绝。

规则文档记录 kind、scope path、precedence、内容和内容哈希。precedence 随目录深度递增，同一目录 `REVIEW.md` 高于 `AGENTS.md`，文件专属规则最高。多目标合并只去重物理文档，不合并目标规则链。Agent 输入使用 `repository_instructions` 文档表和 `repository_instruction_chains` 逐目标引用，Context Builder 在模型调用前验证链引用、作用域、哈希和 precedence 顺序。

自然语言规则冲突时，更具体且 precedence 更高的仓库规则覆盖更通用的仓库规则；平台安全、工具权限、Snapshot 范围和输出契约始终优先。结构化 exclude 保持累积并集语义，不参与自然语言覆盖。

## 后果

Reviewer 可以确定性地为每个变更文件选择适用规则，嵌套目录规则不会泄漏到其他目标。模型输入契约增加显式规则元数据和逐目标链；新增规则类型时必须定义作用域与 precedence，并同步更新 Context Builder 校验和通用系统提示词。
