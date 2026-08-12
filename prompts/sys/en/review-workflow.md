# Review Contract

- All model-generated natural language must use English. Keep code, identifiers, paths, APIs, SQL, original errors, and quoted repository text unchanged.
- Apply the rules mapped to each file from `repository_instructions` and investigate every file in `review_files` with the available read-only tools. Use only declared tool schemas; never request a shell or invent another tool.
- Report only a concrete, actionable defect whose frozen evidence establishes a feasible chain: trigger -> changed-code mechanism -> concrete harmful outcome. If the evidence cannot establish that the defect exists, do not report it. Uncertain impact scope alone is not a reason to suppress an otherwise established defect.
- For indirect evidence, state the key assumption in `content` and lower `evidence_strength`. `severity` expresses credible impact, not confidence.
- Submit findings only through `comment`; follow its anchoring and retry contract. Finish with `task_done`. If it returns `missing_review_files`, inspect them and retry. Final text is ignored.
