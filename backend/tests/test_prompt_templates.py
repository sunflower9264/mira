import asyncio
from pathlib import Path

from app.db import SessionLocal
from app.models import PromptTemplate
from app.runtime.base import AgentChunk, AgentExecutionResult, AgentRuntimeStatus
from app.runtime.factory import set_runtime_override
from app.services import prompts as prompts_service
from app.services.prompts import load_seed_prompts, seed_prompt_templates
from app.utils import now_utc
from tests.runtime_mock import MockRuntime


class PromptCaptureRuntime:
    def __init__(self, total_text: str = '{"patches":[]}'):
        self.total_text = total_text
        self.prompts: list[str] = []

    async def detect_status(self) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(
            installed=True,
            runnable=True,
            identity="prompt-capture",
            method="test",
            checked_at=now_utc(),
        )

    async def execute(
        self,
        *,
        prompt: str,
        session_id: str | None,
        allowed_tools,
        model,
        reasoning_effort,
        cwd: Path,
        on_chunk,
        cancel_event: asyncio.Event,
        runtime_policy="execute",
        output_schema=None,
    ) -> AgentExecutionResult:
        self.prompts.append(prompt)
        if self.total_text:
            await on_chunk(AgentChunk(type="text", text=self.total_text))
        return AgentExecutionResult(
            session_id=session_id or "prompt_session",
            total_text=self.total_text,
            finished_with="done",
        )


def test_prompt_templates_are_seeded_and_saved_to_seed(auth_client):
    response = auth_client.get("/api/settings/prompts")
    assert response.status_code == 200, response.text
    items = {item["key"]: item for item in response.json()}
    assert {
        "nlcompile_graph_patch",
        "nlcompile_plan",
        "condition_choice",
        "status_smoke",
        "prompt_assistant",
        "output_html_rendering",
        "output_contract_repair",
        "graph_layout_beautify",
    } <= set(items)
    assert "prompt_helper" not in items
    assert "$instruction" in items["nlcompile_plan"]["content"]
    assert "goal_summary" in items["nlcompile_plan"]["content"]
    assert "新建 workflow、节点较多或结构调整较大本身都不是提问理由" in items["nlcompile_plan"]["content"]
    assert "implementation_steps" in items["nlcompile_plan"]["content"]
    assert "expected_inputs" in items["nlcompile_plan"]["content"]
    assert "expected_outputs" in items["nlcompile_plan"]["content"]
    assert "中文用户可见节点名称" in items["nlcompile_plan"]["content"]
    assert "一个 workflow 最多一个 user_input 和一个 output" in items["nlcompile_plan"]["content"]
    assert "不设计“运行代码/部署项目”的平台能力" in items["nlcompile_plan"]["content"]
    assert "$instruction" in items["nlcompile_graph_patch"]["content"]
    assert "$confirmed_plan" in items["nlcompile_graph_patch"]["content"]
    assert "output 是最终 HTML 展示节点" in items["nlcompile_graph_patch"]["content"]
    assert "不能作为 source" in items["nlcompile_graph_patch"]["content"]
    assert "不得 add_node 创建第二个" in items["nlcompile_graph_patch"]["content"]
    assert "graph patch 阶段禁止再次向用户提问" in items["nlcompile_graph_patch"]["content"]
    assert "不得重新解释、扩展或替换确认方案" in items["nlcompile_graph_patch"]["content"]
    assert "禁止冗余传递连线" in items["nlcompile_graph_patch"]["content"]
    assert "a 已经通过 b 影响 c" in items["nlcompile_graph_patch"]["content"]
    assert "不判断画布视觉交叉、节点坐标或连线路径" in items["nlcompile_graph_patch"]["content"]
    assert "中文 title 和 description" in items["nlcompile_graph_patch"]["content"]
    assert "禁止制造交叉连接" not in items["nlcompile_graph_patch"]["content"]
    assert items["prompt_assistant"]["name"] == "提示词助手"
    assert "执行祖先/后继关系" in items["prompt_assistant"]["description"]
    assert "先选模式；有歧义时最小改动" in items["prompt_assistant"]["content"]
    assert "其余逐字保留" in items["prompt_assistant"]["content"]
    assert "所有影响行为的目标、变量、字段、示例、边界、输出要求和验收标准必须保留" in items["prompt_assistant"]["content"]
    assert "不写修改说明" in items["prompt_assistant"]["content"]
    assert "最短充分 prompt" in items["prompt_assistant"]["content"]
    assert "中文 `title` 与 `description`" in items["prompt_assistant"]["content"]
    assert "否则采用保守默认直接生成" in items["prompt_assistant"]["content"]
    assert items["graph_layout_beautify"]["name"] == "美化样式节点布局"
    assert "$graph_json" in items["graph_layout_beautify"]["content"]
    assert "$node_sizes_json" in items["graph_layout_beautify"]["content"]
    assert "并行节点" in items["graph_layout_beautify"]["content"]
    assert "$user_prompt" in items["output_html_rendering"]["content"]
    assert "完整呈现上游中与最终结果有关" in items["output_html_rendering"]["content"]
    assert "box-sizing: border-box" in items["output_html_rendering"]["content"]
    assert "表格和代码在窄屏不得撑破页面" in items["output_html_rendering"]["content"]
    assert "download_url" in items["output_html_rendering"]["content"]
    assert "`/workspace` 中的内部文件路径绝对不要在 HTML 中展示或链接" in items["output_html_rendering"]["content"]
    assert "可下载文件由 Mira 的「文件」视图统一展示" in items["output_html_rendering"]["content"]
    assert '<img src="该地址" alt="图片说明">' in items["output_html_rendering"]["content"]
    assert "工具输出不是最终结果" in items["output_html_rendering"]["content"]
    assert "完整 HTML 放入 `html` 字段" in items["output_html_rendering"]["content"]
    assert len(items["nlcompile_graph_patch"]["content"]) < 2600
    assert len(items["prompt_assistant"]["content"]) < 2600
    assert "$contract" in items["output_contract_repair"]["content"]
    assert "$validation_error" in items["output_contract_repair"]["content"]
    assert "$original_output" in items["output_contract_repair"]["content"]
    assert "$task_context" in items["output_contract_repair"]["content"]
    assert "最小结构或格式修正" in items["output_contract_repair"]["content"]
    assert items["condition_choice"]["variables"] == ["user_prompt", "branch_options_json"]
    assert "根据每项 `label` 的业务含义判断" in items["condition_choice"]["content"]
    assert items["output_contract_repair"]["variables"] == [
        "contract",
        "validation_error",
        "original_output",
        "task_context",
    ]

    dirty_keys = {
        "status_smoke": "DB_ONLY_STATUS",
        "nlcompile_plan": "DB_ONLY_NLCOMPILE_PLAN",
        "nlcompile_graph_patch": "DB_ONLY_NLCOMPILE",
        "prompt_assistant": "DB_ONLY_PROMPT_ASSISTANT",
        "output_html_rendering": "DB_ONLY_OUTPUT_HTML",
        "output_contract_repair": "DB_ONLY_OUTPUT_CONTRACT_REPAIR",
        "graph_layout_beautify": "DB_ONLY_GRAPH_LAYOUT",
    }
    for key, content in dirty_keys.items():
        changed = auth_client.put(f"/api/settings/prompts/{key}", json={"content": content})
        assert changed.status_code == 200, changed.text
        assert changed.json()["content"] == content

    status_seed = (prompts_service.PROMPT_SEED_DIR / "status_smoke.md").read_text(encoding="utf-8")
    assert "key: status_smoke" in status_seed
    assert "variables: []" in status_seed
    assert status_seed.endswith("DB_ONLY_STATUS\n")

    async def seed_again() -> dict[str, str]:
        async with SessionLocal() as db:
            await seed_prompt_templates(db)
            rows: dict[str, str] = {}
            for key in dirty_keys:
                row = await db.get(PromptTemplate, key)
                assert row is not None
                rows[key] = row.content
            assert await db.get(PromptTemplate, "prompt_helper") is None
            return rows

    seeded_content = asyncio.run(seed_again())
    seeds = {seed.key: seed for seed in load_seed_prompts()}
    for key, saved_content in dirty_keys.items():
        assert seeded_content[key] == seeds[key].content
        assert seeded_content[key] == saved_content

def test_prompt_template_save_rejects_invalid_seed_without_db_update(auth_client):
    before = auth_client.get("/api/settings/prompts")
    assert before.status_code == 200, before.text
    before_items = {item["key"]: item for item in before.json()}
    original_content = before_items["status_smoke"]["content"]

    seed_path = prompts_service.PROMPT_SEED_DIR / "status_smoke.md"
    seed_path.write_text("invalid seed", encoding="utf-8")

    response = auth_client.put("/api/settings/prompts/status_smoke", json={"content": "NEW_STATUS"})
    assert response.status_code == 500, response.text

    after = auth_client.get("/api/settings/prompts")
    assert after.status_code == 200, after.text
    after_items = {item["key"]: item for item in after.json()}
    assert after_items["status_smoke"]["content"] == original_content


def test_prompt_template_admin_only(user_client):
    assert user_client.get("/api/settings/prompts").status_code == 403
    assert user_client.put("/api/settings/prompts/status_smoke", json={"content": "x"}).status_code == 403


def test_status_smoke_uses_database_prompt(auth_client):
    saved = auth_client.put("/api/settings/prompts/status_smoke", json={"content": "DB_ONLY_STATUS"})
    assert saved.status_code == 200, saved.text
    runtime = PromptCaptureRuntime(total_text="OK")
    set_runtime_override(runtime)
    try:
        response = auth_client.post("/api/settings/codex/status")
    finally:
        set_runtime_override(MockRuntime())

    assert response.status_code == 200, response.text
    assert runtime.prompts == ["DB_ONLY_STATUS"]
