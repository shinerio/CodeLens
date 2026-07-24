# ADR 0009：启动时加载本地化系统提示词

## 决策

模型可见的平台规则、仓库规则优先级、通用 Review 工作流、输出约束、内置工具描述和语言校验反馈统一存放在 `prompts/sys/<locale>/`。组合根创建 `I18nPromptLoader` 时加载并校验全部语言目录，之后把不可变语言包注入 Context Builder 与 OpenAI Runtime。

OpenAI Runtime 按平台边界、仓库规则策略、通用工作流、输出契约、Agent 专属策略的顺序组成真正的系统指令。`prompts/<agent_id>/<locale>.md` 只保存可编辑的 Agent 专属审查准则；其内容不能替代通用系统层。Reviewer Prompt Settings 根据合法 Agent ID 从提示词目录解析专属策略，不以 `correctness` 字面量限制未来 Reviewer。

语言由任务的 `prompt_locale` 选择；不存在的语言回退到配置的默认语言。每个语言包必须完整提供七个只读 Snapshot 工具和两个有状态 Review 工具的说明，以及平台边界、仓库规则策略、通用工作流、输出契约和语言校验反馈五类 Markdown 系统文本。缺失、空文件或 JSON 结构错误均使进程在启动阶段失败。

## 后果

运行中的 Review 不读通用系统提示词文件，避免同一进程中因文件变更得到不一致的模型指令。新增语言只需新增完整目录与前端/API 可选语言契约；新增 Reviewer 只需注册 Agent 定义并提供对应的本地化专属策略，不需要复制平台、工具或输出规则。工具名和字段名保留英文稳定契约，避免破坏 SDK 调用与输出解析。
