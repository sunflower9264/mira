from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from app.schemas import RuntimeSkillConfig
from app.services import skill_dependencies, skills_install


def _write_skill(path: Path, *, root: str = "", requirements: bool = False) -> str:
    prefix = f"{root}/" if root else ""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{prefix}SKILL.md", "---\nname: test\n---\n")
        archive.writestr(f"{prefix}scripts/run.py", "print('ok')\n")
        if requirements:
            archive.writestr(f"{prefix}requirements.txt", "example==1\n")
    return hashlib.md5(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(("root", "expected"), [("", ""), ("nested/tool", "nested/tool")])
def test_inspect_skill_archive_returns_canonical_root(tmp_path, root, expected) -> None:
    archive = tmp_path / "skill.zip"
    _write_skill(archive, root=root)

    assert skills_install.inspect_skill_archive(archive) == expected


def test_inspect_skill_archive_requires_one_skill_markdown(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "root")
        bundle.writestr("nested/SKILL.md", "nested")

    with pytest.raises(skills_install.SkillArchiveError, match="只能包含一个"):
        skills_install.inspect_skill_archive(archive)


@pytest.mark.parametrize("member", ["../SKILL.md", "/SKILL.md"])
def test_inspect_skill_archive_rejects_unsafe_paths(tmp_path, member) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, "unsafe")

    with pytest.raises(skills_install.SkillArchiveError, match="路径非法"):
        skills_install.inspect_skill_archive(archive)


def test_inspect_skill_archive_rejects_normalized_duplicates(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("skill/SKILL.md", "first")
        bundle.writestr("skill/./SKILL.md", "second")

    with pytest.raises(skills_install.SkillArchiveError, match="规范化重名"):
        skills_install.inspect_skill_archive(archive)


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO])
def test_inspect_skill_archive_rejects_links_and_special_files(tmp_path, kind) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "skill")
        info = zipfile.ZipInfo("unsafe")
        info.create_system = 3
        info.external_attr = (kind | 0o777) << 16
        bundle.writestr(info, "target")

    with pytest.raises(skills_install.SkillArchiveError, match="成员类型非法"):
        skills_install.inspect_skill_archive(archive)


def test_inspect_skill_archive_rejects_managed_deps(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("nested/SKILL.md", "skill")
        bundle.writestr("nested/.deps/package.py", "unsafe")

    with pytest.raises(skills_install.SkillArchiveError, match="不能包含托管的 .deps"):
        skills_install.inspect_skill_archive(archive)


def test_inspect_skill_archive_rejects_corrupt_zip(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    _write_skill(archive)
    archive.write_bytes(archive.read_bytes()[:-12])

    with pytest.raises(skills_install.SkillArchiveError, match="无效的 Skill zip"):
        skills_install.inspect_skill_archive(archive)


def test_inspect_skill_archive_enforces_member_and_expanded_limits(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "skill.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", "skill")
        bundle.writestr("large.txt", "12345")

    monkeypatch.setattr(skills_install, "MAX_SKILL_ARCHIVE_MEMBERS", 1)
    with pytest.raises(skills_install.SkillArchiveError, match="成员数超过限制"):
        skills_install.inspect_skill_archive(archive)

    monkeypatch.setattr(skills_install, "MAX_SKILL_ARCHIVE_MEMBERS", 10)
    monkeypatch.setattr(skills_install, "MAX_SKILL_ARCHIVE_EXPANDED_BYTES", 5)
    with pytest.raises(skills_install.SkillArchiveError, match="展开大小超过限制"):
        skills_install.inspect_skill_archive(archive)


def test_sync_runtime_skills_extracts_nested_skill_and_returns_readonly_mount(
    tmp_path, monkeypatch
) -> None:
    archive = tmp_path / "skill.zip"
    archive_md5 = _write_skill(archive, root="nested")
    dependency_layer = tmp_path / "cache" / ".deps"
    dependency_layer.mkdir(parents=True)
    (dependency_layer / "package.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(skills_install, "_validated_dependency_path", lambda _key: dependency_layer)
    skill = RuntimeSkillConfig(
        id="skill_test",
        archive_path=str(archive),
        archive_md5=archive_md5,
        skill_root="nested",
        dependency_key="a" * 64,
    )
    skills_dir = tmp_path / "home" / ".agents" / "skills"

    mounts = skills_install.sync_runtime_skills([skill], skills_dir)

    assert (skills_dir / "skill_test" / "nested" / "SKILL.md").is_file()
    assert (skills_dir / "skill_test" / "nested" / ".deps").is_dir()
    assert len(mounts) == 1
    assert mounts[0].source == dependency_layer
    assert mounts[0].target.as_posix() == "/home/mira/.agents/skills/skill_test/nested/.deps"
    assert mounts[0].read_only is True


def test_sync_runtime_skills_rejects_stored_root_mismatch(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    archive_md5 = _write_skill(archive, root="actual")
    skill = RuntimeSkillConfig(
        id="skill_test",
        archive_path=str(archive),
        archive_md5=archive_md5,
        skill_root="recorded",
    )

    with pytest.raises(skills_install.SkillArchiveError, match="根目录与记录不一致"):
        skills_install.sync_runtime_skills([skill], tmp_path / "skills")


def test_sync_runtime_skills_rejects_archive_hash_change(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    _write_skill(archive)
    skill = RuntimeSkillConfig(
        id="skill_test",
        archive_path=str(archive),
        archive_md5="0" * 32,
        skill_root="",
    )

    with pytest.raises(skills_install.SkillArchiveError, match="完整性校验失败"):
        skills_install.sync_runtime_skills([skill], tmp_path / "skills")


def test_sync_runtime_skills_restores_replaced_nested_root(tmp_path) -> None:
    archive = tmp_path / "skill.zip"
    archive_md5 = _write_skill(archive, root="nested")
    skill = RuntimeSkillConfig(
        id="skill_test",
        archive_path=str(archive),
        archive_md5=archive_md5,
        skill_root="nested",
    )
    skills_dir = tmp_path / "skills"
    skills_install.sync_runtime_skills([skill], skills_dir)
    nested = skills_dir / "skill_test" / "nested"
    outside = tmp_path / "outside"
    outside.mkdir()
    skills_install._remove_path(nested)
    nested.symlink_to(outside, target_is_directory=True)

    # A subsequent sync re-extracts the immutable archive instead of following
    # the writable HOME symlink.
    skills_install.sync_runtime_skills([skill], skills_dir)
    assert nested.is_dir()
    assert not nested.is_symlink()
    assert (nested / "SKILL.md").is_file()


def test_sync_runtime_skills_validates_dependency_manifest(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "skill.zip"
    archive_md5 = _write_skill(archive)
    dependency_key = "b" * 64
    cache_root = tmp_path / "dependency-cache"
    entry = cache_root / dependency_key
    deps = entry / ".deps"
    deps.mkdir(parents=True)
    (deps / "package.py").write_text("VALUE = 1\n", encoding="utf-8")
    metadata = deps / "example-1.0.dist-info" / "METADATA"
    metadata.parent.mkdir()
    metadata.write_text("Name: example\nVersion: 1.0\n", encoding="utf-8")
    scan = skill_dependencies._scan_dependency_tree(deps)
    (entry / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": skill_dependencies.MANIFEST_VERSION,
                "policy_version": skill_dependencies.POLICY_VERSION,
                "cache_key": dependency_key,
                "packages": skill_dependencies._installed_packages(deps),
                **scan,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_install, "skill_dependency_cache_dir", lambda: cache_root)
    skill = RuntimeSkillConfig(
        id="skill_test",
        archive_path=str(archive),
        archive_md5=archive_md5,
        dependency_key=dependency_key,
    )

    mounts = skills_install.sync_runtime_skills([skill], tmp_path / "skills")
    assert mounts[0].source == deps

    (deps / "package.py").write_text("CHANGED = 1\n", encoding="utf-8")
    with pytest.raises(skill_dependencies.SkillDependencyError, match="已损坏或被修改"):
        skills_install.sync_runtime_skills([skill], tmp_path / "other-skills")
