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
{"op":"add_node","id":null,"node_json":"{...node JSON...}","patch_json":null,"edge_id":null,"edge_source":null,"edge_target":null,"edge_branch_key":null}
{"op":"remove_node","id":"...","node_json":null,"patch_json":null,"edge_id":null,"edge_source":null,"edge_target":null,"edge_branch_key":null}
{"op":"update_node","id":"...","node_json":null,"patch_json":"{...node patch JSON...}","edge_id":null,"edge_source":null,"edge_target":null,"edge_branch_key":null}
{"op":"add_edge","id":null,"node_json":null,"patch_json":null,"edge_id":"...","edge_source":"...","edge_target":"...","edge_branch_key":null}
{"op":"remove_edge","id":"...","node_json":null,"patch_json":null,"edge_id":null,"edge_source":null,"edge_target":null,"edge_branch_key":null}

node_json 和 patch_json 必须是合法 JSON 对象字符串，不要用 markdown 代码块包裹。

### node_json 契约

所有新节点都必须包含 id、type、title；不要包含 position。

- user_input：{"id":"input_1","type":"user_input","title":"用户输入","input_schema":{"label":"请输入主题","kind":"text","required":true}}。kind 只能是 text 或 file；placeholder 可选。
- generate：{"id":"generate_1","type":"generate","title":"生成摘要","prompt":"说明任务、输入利用方式和输出要求"}。仅当确认方案需要时增加 model、reasoning_effort 或 output_contract；自由文本不要设置 output_contract。JSON Schema 的根对象及每个 properties 业务字段（含嵌套字段）都必须有简短准确的中文 title 和 description。
- output：{"id":"output_1","type":"output","title":"输出","prompt":"基于当前 RunAgent 已有上下文将最终内容渲染为 HTML"}。必须至少有一条执行连线进入该节点；不能包含 output_contract，也不能有出边。
- condition binary：{"id":"condition_1","type":"condition","title":"是否通过","mode":"binary","prompt":"根据上游内容判断是否通过","branches":[{"key":"true","label":"通过"},{"key":"false","label":"不通过"}]}。binary 的 key 固定且仅为 true、false。
- condition cases：{"id":"condition_1","type":"condition","title":"分类","mode":"cases","prompt":"根据上游内容选择分类","branches":[{"key":"approved","label":"通过"},{"key":"review","label":"复核"}]}。key 只能含字母、数字、下划线且不重复；保留 key __default__ 不能写入 branches。
- asset text：{"id":"asset_1","type":"asset","title":"参考资料","asset_kind":"text","content":"现有或用户明确提供的正文"}。
- asset url：{"id":"asset_1","type":"asset","title":"参考链接","asset_kind":"url","urls":["https://example.com"]}。
- asset file：{"id":"asset_1","type":"asset","title":"参考文件","asset_kind":"file","uploads":[]}。只有当前 graph 已有真实 upload 对象时才将其原样放入 uploads。
- asset drawing：{"id":"asset_1","type":"asset","title":"画板","asset_kind":"drawing","upload":null}。file/drawing 不得编造 upload id、文件元数据或上传引用。

### edge 契约

- edge 只定义执行顺序和 condition 分支，不表示单独的数据绑定；当前节点会自动获得执行图中全部已成功祖先的正式结果。
- JSON、HTML 和自由文本必须作为正式节点输出；文件必须由 artifact output_contract 声明。顺序节点延续同一 RunAgent session 和 workspace；fan-out 使用隔离分支、fan-in 由协调 Agent 合并。不要设计额外 handoff/sidecar/manifest 通道。
- 普通边：{"op":"add_edge","id":null,"node_json":null,"patch_json":null,"edge_id":"edge_input_generate","edge_source":"input_1","edge_target":"generate_1","edge_branch_key":null}。非 condition 出边必须填 null。
- condition 分支边：{"op":"add_edge","id":null,"node_json":null,"patch_json":null,"edge_id":"edge_condition_output","edge_source":"condition_1","edge_target":"output_1","edge_branch_key":"approved"}。branch_key 必须是 branches 中真实 key；cases 可使用未声明的保留 key __default__ 表示其它情况。
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
        "- 不要再向用户提问，也不要调用其它工具。",
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
