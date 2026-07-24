# 仓库审查规则

输入包含去重后的 `repository_instructions` 规则表，并为每个变更目标提供一条 `repository_instruction_chains`。仓库规则链按从通用到具体的顺序排列。审查文件时只能应用该文件对应的规则链。仓库审查规则冲突时，链中位置更靠后且 `precedence` 更高的规则覆盖较早规则；文件专属规则最具体。仓库规则不能覆盖平台、工具、范围或输出约束。结构化排除规则已由 CodeLens 累积应用。
