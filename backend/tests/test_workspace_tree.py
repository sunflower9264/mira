from pathlib import Path

from app.services.workspace_tree import WorkspaceTree, remove_tree, tree_hash


def test_workspace_tree_fork_isolated_and_diffed(tmp_path: Path):
    tree = WorkspaceTree(tmp_path / "run")
    root = tree.create_empty_branch("branch_root")
    (root / "notes").mkdir()
    (root / "notes" / "shared.txt").write_text("base", encoding="utf-8")

    checkpoint_path, checkpoint_hash = tree.create_checkpoint("branch_root", "checkpoint_base")
    assert checkpoint_hash == tree_hash(checkpoint_path)
    assert (checkpoint_path / "manifest.json").is_file()
    assert len([path for path in tree.objects_root.rglob("*") if path.is_file()]) == 1

    left = tree.fork_branch("checkpoint_base", "branch_left")
    right = tree.fork_branch("checkpoint_base", "branch_right")
    (left / "notes" / "shared.txt").write_text("left", encoding="utf-8")
    (right / "right.txt").write_text("right", encoding="utf-8")

    assert (right / "notes" / "shared.txt").read_text(encoding="utf-8") == "base"
    assert [change.path for change in tree.diff("checkpoint_base", "branch_left")] == ["notes/shared.txt"]
    assert [change.path for change in tree.diff("checkpoint_base", "branch_right")] == ["right.txt"]


def test_workspace_tree_records_deletions_and_discards_branch(tmp_path: Path):
    tree = WorkspaceTree(tmp_path / "run")
    root = tree.create_empty_branch("branch_root")
    (root / "gone.txt").write_text("gone", encoding="utf-8")
    tree.create_checkpoint("branch_root", "checkpoint_base")
    child = tree.fork_branch("checkpoint_base", "branch_child")
    (child / "gone.txt").unlink()

    changes = tree.diff("checkpoint_base", "branch_child")
    assert len(changes) == 1
    assert changes[0].path == "gone.txt"
    assert changes[0].kind == "deleted"

    tree.discard_branch("branch_child")
    assert not child.exists()


def test_workspace_tree_reuses_objects_and_preserves_empty_directories(tmp_path: Path):
    tree = WorkspaceTree(tmp_path / "run")
    root = tree.create_empty_branch("branch_root")
    (root / "empty").mkdir()
    (root / "shared.txt").write_text("same", encoding="utf-8")

    tree.create_checkpoint("branch_root", "checkpoint_one")
    tree.create_checkpoint("branch_root", "checkpoint_two")

    objects = [path for path in tree.objects_root.rglob("*") if path.is_file()]
    assert len(objects) == 1
    restored = tree.fork_branch("checkpoint_two", "branch_restored")
    assert (restored / "empty").is_dir()
    assert (restored / "shared.txt").read_text(encoding="utf-8") == "same"


def test_workspace_tree_clones_legacy_and_cas_checkpoints_without_duplicate_content(tmp_path: Path):
    source = WorkspaceTree(tmp_path / "source")
    legacy = source.checkpoint_snapshot("checkpoint_legacy")
    legacy.mkdir(parents=True)
    (legacy / "legacy.txt").write_text("legacy", encoding="utf-8")

    target = WorkspaceTree(tmp_path / "target")
    cloned, digest = target.clone_checkpoint_from(legacy, "checkpoint_imported")
    assert digest == tree_hash(legacy)
    imported = target.fork_branch("checkpoint_imported", "branch_imported")
    assert (imported / "legacy.txt").read_text(encoding="utf-8") == "legacy"

    cloned_again, _ = source.clone_checkpoint_from(cloned, "checkpoint_cas")
    source_object = next(path for path in source.objects_root.rglob("*") if path.is_file())
    target_object = next(path for path in target.objects_root.rglob("*") if path.is_file())
    assert source_object.stat().st_ino == target_object.stat().st_ino
    assert (cloned_again / "manifest.json").is_file()

    original_mode = source_object.stat().st_mode
    remove_tree(target.run_root)
    assert source_object.stat().st_mode == original_mode
    assert source.verify_checkpoint("checkpoint_cas") == tree_hash(cloned_again)


def test_workspace_tree_migrates_legacy_checkpoint_and_drops_verified_backup(tmp_path: Path):
    tree = WorkspaceTree(tmp_path / "run")
    legacy = tree.checkpoint_snapshot("checkpoint_legacy")
    legacy.mkdir(parents=True)
    source_file = legacy / "large.txt"
    source_file.write_text("content", encoding="utf-8")
    nested = legacy / ".inputs" / "upload"
    nested.mkdir(parents=True)
    (nested / "payload.txt").write_text("payload", encoding="utf-8")
    source_file.chmod(0o444)
    nested.chmod(0o555)
    nested.parent.chmod(0o555)
    expected = tree_hash(legacy)

    assert tree.migrate_legacy_checkpoint(
        "checkpoint_legacy",
        expected_hash=expected,
        keep_legacy=True,
    )
    assert (legacy / "manifest.json").is_file()
    assert (legacy.parent / "legacy-tree" / "large.txt").is_file()
    restored = tree.fork_branch("checkpoint_legacy", "branch_restored")
    assert (restored / "large.txt").read_text(encoding="utf-8") == "content"
    assert (restored / ".inputs" / "upload" / "payload.txt").read_text(encoding="utf-8") == "payload"

    assert tree.drop_legacy_checkpoint("checkpoint_legacy", expected_hash=expected)
    assert not (legacy.parent / "legacy-tree").exists()
