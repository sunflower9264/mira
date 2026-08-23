from pathlib import Path

from app.services.workspace_tree import WorkspaceTree, tree_hash


def test_workspace_tree_fork_isolated_and_diffed(tmp_path: Path):
    tree = WorkspaceTree(tmp_path / "run")
    root = tree.create_empty_branch("branch_root")
    (root / "notes").mkdir()
    (root / "notes" / "shared.txt").write_text("base", encoding="utf-8")

    checkpoint_path, checkpoint_hash = tree.create_checkpoint("branch_root", "checkpoint_base")
    assert checkpoint_hash == tree_hash(checkpoint_path)

    left = tree.fork_branch("checkpoint_base", "branch_left")
    right = tree.fork_branch("checkpoint_base", "branch_right")
    (left / "notes" / "shared.txt").write_text("left", encoding="utf-8")
    (right / "right.txt").write_text("right", encoding="utf-8")

    assert (checkpoint_path / "notes" / "shared.txt").read_text(encoding="utf-8") == "base"
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
