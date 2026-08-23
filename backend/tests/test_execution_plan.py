from __future__ import annotations

import pytest

from app.services.execution_plan import ExecutionPlanError, compile_execution_plan


def test_execution_plan_exposes_all_transitive_ancestors_in_topological_order() -> None:
    graph = {
        "nodes": [{"id": node_id} for node_id in ("a", "b", "c", "d", "unrelated")],
        "execution_edges": [
            {"id": "e_ab", "source": "a", "target": "b"},
            {"id": "e_ac", "source": "a", "target": "c"},
            {"id": "e_bd", "source": "b", "target": "d"},
            {"id": "e_cd", "source": "c", "target": "d"},
        ],
    }

    plan = compile_execution_plan(graph)

    assert plan.predecessors["d"] == frozenset({"b", "c"})
    assert plan.ancestor_ids("d") == ("a", "b", "c")
    assert "unrelated" not in plan.ancestor_ids("d")


def test_execution_plan_rejects_cycles() -> None:
    graph = {
        "nodes": [{"id": "a"}, {"id": "b"}],
        "execution_edges": [
            {"id": "e_ab", "source": "a", "target": "b"},
            {"id": "e_ba", "source": "b", "target": "a"},
        ],
    }

    with pytest.raises(ExecutionPlanError, match="环路"):
        compile_execution_plan(graph)
