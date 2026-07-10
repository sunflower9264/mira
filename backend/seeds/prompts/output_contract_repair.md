---
key: output_contract_repair
name: 输出契约自动修正
description: generate 节点输出不符合 output_contract 时，要求 Agent 只返回修正后的最终内容。
variables:
  - contract
  - validation_error
  - original_output
  - task_context
---
你需要修正一个 Mira generate 节点的输出，使它严格符合下面的输出契约。

修正原则：
- 原始任务上下文和原始输出是内容事实来源，只做通过校验所需的最小结构或格式修正。
- 完整保留原始输出中的事实、数字、名称、URL、文件路径、列表顺序、语言和已有 HTML；不要总结、翻译、润色或补充新事实。
- 可以去除 Markdown fence、解释性前后缀，修正 JSON 语法，补充契约 wrapper，或在对应关系明确时调整字段结构。
- 原始 HTML 仅做契约包装或必要的格式修正，不要重新设计页面。
- 如果无法在不编造信息的前提下满足契约，不要猜测缺失值；保留可验证信息，即使后端最终仍会判定失败。

不要解释原因，不要输出修正计划，不要添加前后缀；只输出修正后的最终内容。

## 原始任务上下文
$task_context

## 输出契约
$contract

## 校验失败原因
$validation_error

## 原始输出
$original_output
