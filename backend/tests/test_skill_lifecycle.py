from __future__ import annotations

import asyncio
import io
import zipfile
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import Skill
from app.services import skills as skills_service
from app.services.admin import ADMIN_USER_ID
from app.services.runtime_paths import skills_data_dir
from app.services.skill_dependencies import SkillDependencyError


def _skill_archive(*, requirements: str | None = None, include_managed_deps: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("nested/SKILL.md", "---\nname: dependency-demo\n---\n")
        if requirements is not None:
            archive.writestr("nested/requirements.txt", requirements)
        if include_managed_deps:
            archive.writestr("nested/.deps/demo.py", "VERSION = 'bundled'\n")
    return buffer.getvalue()


def test_skill_upload_persists_ready_dependency_layer(auth_client, monkeypatch) -> None:
    monkeypatch.setattr(
        skills_service,
        "build_dependency_layer",
        lambda *_args, **_kwargs: SimpleNamespace(cache_key="a" * 64),
    )

    response = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("dependency-demo.zip", _skill_archive(requirements="demo==1\n"), "application/zip")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["dependency_status"] == "ready"
    assert response.json()["dependency_error"] == ""

    async def stored_fields() -> tuple[str, str, str]:
        async with SessionLocal() as db:
            skill = await db.get(Skill, response.json()["id"])
            assert skill is not None
            return skill.skill_root, skill.dependency_status, skill.dependency_key

    assert asyncio.run(stored_fields()) == ("nested", "ready", "a" * 64)


def test_skill_dependency_failure_leaves_no_skill_or_raw_archive(auth_client, monkeypatch) -> None:
    def fail_build(*_args, **_kwargs):
        raise SkillDependencyError("没有兼容当前 runtime 的二进制 wheel")

    monkeypatch.setattr(skills_service, "build_dependency_layer", fail_build)
    before_ids = {item["id"] for item in auth_client.get("/api/settings").json()["skills"]}

    response = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("dependency-failure.zip", _skill_archive(requirements="demo==1\n"), "application/zip")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "没有兼容当前 runtime 的二进制 wheel"
    after_ids = {item["id"] for item in auth_client.get("/api/settings").json()["skills"]}
    assert after_ids == before_ids
    assert list(skills_data_dir(ADMIN_USER_ID).iterdir()) == []


def test_skill_upload_rejects_managed_deps(auth_client) -> None:
    response = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("bundled-deps.zip", _skill_archive(include_managed_deps=True), "application/zip")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Skill 压缩包不能包含托管的 .deps 目录"


def test_failed_skill_cannot_be_enabled_or_exposed_to_planning(auth_client) -> None:
    uploaded = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("failed-skill.zip", _skill_archive(), "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    skill_id = uploaded.json()["id"]

    async def mark_failed() -> None:
        async with SessionLocal() as db:
            skill = await db.get(Skill, skill_id)
            assert skill is not None
            skill.enabled = False
            skill.dependency_status = "failed"
            skill.dependency_error = "构建失败"
            await db.commit()

    asyncio.run(mark_failed())

    enabled = auth_client.patch(f"/api/settings/skills/{skill_id}", json={"enabled": True})
    planning = auth_client.patch(
        f"/api/settings/skills/{skill_id}", json={"planning_enabled": True}
    )
    assert enabled.status_code == 409
    assert enabled.json()["detail"] == "构建失败"
    assert planning.status_code == 409
    assert planning.json()["detail"] == "构建失败"


def test_reconcile_updates_migrated_skill_metadata(auth_client) -> None:
    uploaded = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("migrated-skill.zip", _skill_archive(), "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    skill_id = uploaded.json()["id"]

    async def reconcile() -> tuple[str, str, str]:
        async with SessionLocal() as db:
            skill = await db.get(Skill, skill_id)
            assert skill is not None
            skill.skill_root = ""
            skill.dependency_status = "pending"
            await db.commit()
            await skills_service.reconcile_skill_dependencies(db)
            await db.refresh(skill)
            return skill.skill_root, skill.dependency_status, skill.dependency_key

    assert asyncio.run(reconcile()) == ("nested", "not_required", "")


def test_reconcile_disables_skill_when_dependency_build_fails(auth_client, monkeypatch) -> None:
    uploaded = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("reconcile-failure.zip", _skill_archive(), "application/zip")},
    )
    assert uploaded.status_code == 200, uploaded.text
    skill_id = uploaded.json()["id"]

    def fail_build(*_args, **_kwargs):
        raise SkillDependencyError("runtime 镜像不再兼容")

    monkeypatch.setattr(skills_service, "build_dependency_layer", fail_build)

    async def reconcile() -> tuple[str, str, bool, bool]:
        async with SessionLocal() as db:
            skill = await db.get(Skill, skill_id)
            assert skill is not None
            skill.planning_enabled = True
            await db.commit()
            await skills_service.reconcile_skill_dependencies(db)
            await db.refresh(skill)
            return (
                skill.dependency_status,
                skill.dependency_error,
                skill.enabled,
                skill.planning_enabled,
            )

    assert asyncio.run(reconcile()) == (
        "failed",
        "runtime 镜像不再兼容",
        False,
        False,
    )
