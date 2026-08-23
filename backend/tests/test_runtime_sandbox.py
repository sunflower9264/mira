from __future__ import annotations

from app.runtime.sandbox import RuntimePathMap, _MultiplexedLineDecoder
from app.services.runtime_paths import uploads_dir
from app.services.runtime_uploads import RuntimeUploadRef, runtime_upload_context


def test_runtime_path_map_rewrites_workspace_home_and_uploads(tmp_path):
    path_map = RuntimePathMap(
        workspace_host=tmp_path / "workspace",
        home_host=tmp_path / "home",
        uploads_host=tmp_path / "uploads",
    )
    for path in (path_map.workspace_host, path_map.home_host, path_map.uploads_host):
        path.mkdir(parents=True)

    text = f"{path_map.workspace_host}/a.txt {path_map.home_host}/.mira/prompt.txt {path_map.uploads_host}/upl_1/blob"
    container_text = path_map.host_to_container_text(text)

    assert "/workspace/a.txt" in container_text
    assert "/home/mira/.mira/prompt.txt" in container_text
    assert "/mnt/inputs/upl_1/blob" in container_text
    assert str(path_map.workspace_host / "a.txt") in path_map.container_to_host_text(container_text)


def test_runtime_path_map_for_call_does_not_use_user_uploads_root(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    user_uploads = uploads_dir("sandbox_user")
    user_uploads.mkdir(parents=True, exist_ok=True)

    path_map = RuntimePathMap.for_call(workspace=workspace, home=home)

    assert path_map.uploads_host is None
    rewritten = path_map.host_to_container_text(str(user_uploads / "upl_secret" / "blob"))
    assert rewritten == str(user_uploads / "upl_secret" / "blob")
    assert "/mnt/inputs" not in rewritten


def test_runtime_path_map_for_call_uses_staged_uploads_only(tmp_path):
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    source = tmp_path / "uploads" / "upl_allowed" / "blob"
    workspace.mkdir()
    home.mkdir()
    source.parent.mkdir(parents=True)
    source.write_bytes(b"allowed")

    with runtime_upload_context(workspace, [RuntimeUploadRef(id="upl_allowed", path=source)]) as upload_context:
        path_map = RuntimePathMap.for_call(workspace=workspace, home=home)
        staged_text = upload_context.rewrite_text(str(source))
        container_text = path_map.host_to_container_text(staged_text)

    assert path_map.uploads_host is not None
    assert path_map.uploads_host != source.parent.parent
    assert "/mnt/inputs/upl_allowed/blob" in container_text
    assert str(source) not in container_text


def test_multiplexed_line_decoder_keeps_cjk_character_split_across_chunks() -> None:
    missing = "缺"
    prefix = '{"html":"封面图，'.encode("utf-8")
    suffix = '少详情"}\n'.encode("utf-8")
    encoded = missing.encode("utf-8")
    assert len(encoded) == 3
    decoder = _MultiplexedLineDecoder()
    lines = decoder.feed_stdout(prefix + encoded[:1])
    lines.extend(decoder.feed_stdout(encoded[1:] + suffix))
    assert lines == ['{"html":"封面图，缺少详情"}']
    assert "\ufffd" not in lines[0]
