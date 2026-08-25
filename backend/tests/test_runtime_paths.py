from pathlib import Path

from app.services.runtime_paths import (
    clone_run_scoped_homes,
    compact_run_scoped_home,
    run_scoped_home_clone_size,
    run_scoped_home_path,
    scoped_codex_home,
)


def test_run_home_clone_excludes_disposable_cache_and_preserves_session_state(auth_client):
    source = scoped_codex_home(Path("/unused"), session_scope="run:source")
    (source / "sessions").mkdir()
    (source / "sessions" / "thread.jsonl").write_text("thread", encoding="utf-8")
    (source / "state_5.sqlite").write_bytes(b"state")
    for directory in (".npm", ".cache", ".tmp", "tmp", "thread-writer-locks"):
        (source / directory).mkdir()
        (source / directory / "cache.bin").write_bytes(b"cache")
    (source / "logs_2.sqlite").write_bytes(b"logs")
    (source / ".agents" / "skills").mkdir(parents=True)
    (source / ".agents" / "skills" / "generated.txt").write_text("skill", encoding="utf-8")

    assert run_scoped_home_clone_size("source") == len(b"thread") + len(b"state")
    clone_run_scoped_homes("source", "target")
    target = run_scoped_home_path("target")

    assert (target / "sessions" / "thread.jsonl").read_text(encoding="utf-8") == "thread"
    assert (target / "state_5.sqlite").read_bytes() == b"state"
    assert not (target / ".npm").exists()
    assert not (target / "logs_2.sqlite").exists()
    assert not (target / ".agents" / "skills").exists()

    (target / ".cache").mkdir()
    compact_run_scoped_home("target")
    assert not (target / ".cache").exists()
    assert (target / "sessions" / "thread.jsonl").is_file()
