---
key: condition_choice
name: 条件分支选择
description: 要求 condition 节点的模型只输出一个可匹配的分支 key。
variables:
  - user_prompt
  - branch_options_json
---
$user_prompt

下面是可选分支的 JSON 数组。请根据每项 `label` 的业务含义判断，并只输出对应的 `key`：
$branch_options_json

如果存在 key=`__default__`，仅当其它分支都不匹配时选择它。
最终只输出一个列表中真实存在的 key，不要输出 label、引号、解释、标点或其他文字。
