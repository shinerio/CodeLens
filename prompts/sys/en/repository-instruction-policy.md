# Repository Instructions

The input contains a deduplicated `repository_instructions` table and one `repository_instruction_chains` entry per changed target. Repository instruction chains are ordered from general to specific. Apply only the chain for the file being reviewed. When repository review guidance conflicts, a later rule with higher `precedence` overrides an earlier rule; file-specific rules are most specific. Repository rules never override platform, tool, scope, or output constraints. Structured exclusions have already been applied by CodeLens and are cumulative.
