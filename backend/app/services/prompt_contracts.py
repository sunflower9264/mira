from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.services.structured_output import (
    NL_COMPILE_PATCH_OUTPUT_SCHEMA,
    NL_COMPILE_PLAN_OUTPUT_SCHEMA,
    PROMPT_ASSISTANT_OUTPUT_SCHEMA,
)


@dataclass(frozen=True)
class PromptContract:
    key: str
    output_schema: dict[str, Any] | None
    max_attempts: int = 1


PROMPT_CONTRACTS: dict[str, PromptContract] = {
    "nlcompile_plan": PromptContract(
        key="nlcompile_plan",
        output_schema=NL_COMPILE_PLAN_OUTPUT_SCHEMA,
        max_attempts=3,
    ),
    "nlcompile_graph_patch": PromptContract(
        key="nlcompile_graph_patch",
        output_schema=NL_COMPILE_PATCH_OUTPUT_SCHEMA,
        max_attempts=3,
    ),
    "prompt_assistant": PromptContract(
        key="prompt_assistant",
        output_schema=PROMPT_ASSISTANT_OUTPUT_SCHEMA,
        max_attempts=3,
    ),
}


PATCH_PROTOCOL = """
## Mira graph patch 协议（后端强制）

只能输出以下 patch op：add_node、remove_node、update_node、add_edge、remove_edge。
删除连线必须使用 remove_edge；不要使用 delete_edge、delete_node、update_edge 或其它 op 名称。
patches 至少包含 1 项，必须共同完成已确认方案，并能按数组顺序应用到当前 graph，通过结构、prompt 节点和拓扑校验。
新增节点 id 和连线 id 必须在当前 graph 与本次 patches 中全局唯一。创建数据流时按 add_node、update_node、add_edge 的顺序输出；删除节点前先 remove_edge 删除仍需显式移除的连线。

结构化输出字段固定如下；不用的字段必须填 null：
{"op":"add_node","id":null,"node_json":"{...node JSON...}","patch_json":null,"edge_id":null,"edge_source":null,"edge_target":null,"edge_source_handle":null}
{"op":"remove_node","id":"...","node_json":null,"patch_json":null,"edge_id":null,"edge_source":null,"edge_target":null,"edge_source_handle":null}
{"op":"update_node","id":"...","node_json":null,"patch_json":"{...node patch JSON...}","edge_id":null,"edge_source":null,"edge_target":null,"edge_source_handle":null}
{"op":"add_edge","id":null,"node_json":null,"patch_json":null,"edge_id":"...","edge_source":"...","edge_target":"...","edge_source_handle":null}
{"op":"remove_edge","id":"...","node_json":null,"patch_json":null,"edge_id":null,"edge_source":null,"edge_target":null,"edge_source_handle":null}

node_json 和 patch_json 必须是合法 JSON 对象字符串，不要用 markdown 代码块包裹。

### node_json 契约

所有新节点都必须包含 id、type、title；不要包含 position、agent 或 agent_session_id。

- user_input：{"id":"input_1","type":"user_input","title":"用户输入","input_schema":{"label":"请输入主题","kind":"text","required":true}}。kind 只能是 text 或 file；placeholder 可选。
- generate：{"id":"generate_1","type":"generate","title":"生成摘要","prompt":"说明任务、输入利用方式和输出要求"}。仅当确认方案需要时增加 model、reasoning_effort 或 output_contract；自由文本不要设置 output_contract。
- output：{"id":"output_1","type":"output","title":"输出","prompt":"将上游结果完整渲染为 HTML","source_node_id":"generate_1"}。必须另有一条 edge_source 为 source_node_id、edge_target 为该 output 的入边；不能包含 output_contract，也不能有出边。
- condition binary：{"id":"condition_1","type":"condition","title":"是否通过","mode":"binary","prompt":"根据上游内容判断是否通过","branches":[{"key":"true","label":"通过"},{"key":"false","label":"不通过"}]}。binary 的 key 固定且仅为 true、false。
- condition cases：{"id":"condition_1","type":"condition","title":"分类","mode":"cases","prompt":"根据上游内容选择分类","branches":[{"key":"approved","label":"通过"},{"key":"review","label":"复核"}]}。key 只能含字母、数字、下划线且不重复；保留 key __default__ 不能写入 branches。
- asset text：{"id":"asset_1","type":"asset","title":"参考资料","asset_kind":"text","content":"现有或用户明确提供的正文"}。
- asset url：{"id":"asset_1","type":"asset","title":"参考链接","asset_kind":"url","urls":["https://example.com"]}。
- asset file：{"id":"asset_1","type":"asset","title":"参考文件","asset_kind":"file","uploads":[]}。只有当前 graph 已有真实 upload 对象时才将其原样放入 uploads。
- asset drawing：{"id":"asset_1","type":"asset","title":"画板","asset_kind":"drawing","upload":null}。file/drawing 不得编造 upload id、文件元数据或上传引用。

### edge 契约

- 普通边：{"op":"add_edge","id":null,"node_json":null,"patch_json":null,"edge_id":"edge_input_generate","edge_source":"input_1","edge_target":"generate_1","edge_source_handle":null}。非 condition 出边必须填 null。
- condition 分支边：{"op":"add_edge","id":null,"node_json":null,"patch_json":null,"edge_id":"edge_condition_output","edge_source":"condition_1","edge_target":"output_1","edge_source_handle":"approved"}。source_handle 必须是 branches 中真实 key；cases 可使用未声明的保留 key __default__ 表示其它情况。
- user_input 和 asset 不能作为 target；output 不能作为 source；同一普通 source/target 不得重复，同一 condition 分支最多一条出边，且所有连线必须保持无环。
""".strip()


def contract_for(key: str) -> PromptContract:
    return PROMPT_CONTRACTS[key]


def output_schema_for(key: str) -> dict[str, Any] | None:
    return contract_for(key).output_schema


def max_attempts_for(key: str) -> int:
    return contract_for(key).max_attempts


def append_patch_protocol(prompt: str) -> str:
    return _join_sections(prompt, PATCH_PROTOCOL)


def append_nlcompile_ask_user_rules(prompt: str, ask_user_protocol: str, *, final_shape: str) -> str:
    rules = f"""
## 自然语言编辑提问规则

- 需要提问时直接调用真实 ask_user 工具并等待 tool_result；不要把问题写成普通文本或 JSON 回复。
- 调用 ask_user 拿到用户回答后，必须继续输出方案 JSON；不要生成 graph patch 或 new_graph。
- 如果 ask_user 工具返回内部错误、连接失败或取消，必须停止；不得声称已提问，也不得继续输出方案 JSON。
- 最终必须严格输出形状为 {final_shape} 的 JSON。

{ask_user_protocol}
""".strip()
    return _join_sections(prompt, rules)


def append_prompt_assistant_ask_user_rules(prompt: str, ask_user_protocol: str) -> str:
    rules = f"""
## 生成提示词提问规则

- 只有在缺少会明显改变当前节点 prompt 或 output_contract 的关键决策时，才调用 ask_user。
- 用户输入、当前节点、方案上下文或上下游已经给出足够依据时，直接输出最终 JSON。
- 最多发起 1 次 ask_user；拿到回答后必须吸收 answers / text / attachments，并继续输出最终 JSON。
- 最终必须严格输出 JSON：{{"prompt":"完整 prompt 正文","output_contract_json":"{{...output_contract JSON...}}" 或 null}}。
- output_contract_json 为字符串时，内容必须是合法 JSON 对象；自由文本输出填 null。

{ask_user_protocol}
""".strip()
    return _join_sections(prompt, rules)


def build_structured_repair_prompt(
    *,
    task_name: str,
    original_prompt: str,
    previous_output: str,
    validation_error: str,
    output_shape: str,
    output_schema: dict[str, Any] | None,
) -> str:
    sections = [
        f"你刚才执行「{task_name}」时没有返回可用的结构化输出。请只修正输出格式和缺失字段，不要重新解释或扩展任务。",
        "要求：",
        f"- 只输出一个 JSON 对象，形状必须是 {output_shape}。",
        "- 不要输出 markdown、解释、注释、代码块或额外字段。",
        "- 不要调用 ask_user 或其它工具。",
        "原始任务提示：",
        original_prompt,
        "上一轮输出：",
        previous_output,
        "校验失败原因：",
        validation_error,
    ]
    if output_schema is not None:
        sections.extend(["JSON Schema：", json.dumps(output_schema, ensure_ascii=False, separators=(",", ":"))])
    return "\n\n".join(section for section in sections if section)


def _join_sections(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section and section.strip())
