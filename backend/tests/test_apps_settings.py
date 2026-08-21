import asyncio
import io
import zipfile
from datetime import timedelta

from app.db import SessionLocal
from app.models import App, AppVersion, Run
from app.services.apps import GALLERY_OWNER_ID, seed_gallery
from app.services.auth import create_access_token
from app.services.output_contracts import contract_prompt_suffix
from app.services.runtime_paths import claude_home, codex_home
from app.services.uploads import resolve_upload
from app.utils import dumps, now_utc
from tests.auth_helpers import create_regular_user


def _minimal_output_graph() -> dict:
    return {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "asset_kind": "text",
                "content": "input",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "title": "Output",
                "prompt": "render [[respond:<section>ok</section>]]",
                "source_node_id": "n_asset",
            }
        ],
        "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
    }


def test_apps_gallery_versions_and_settings(auth_client):
    gallery = auth_client.get("/api/apps", params={"gallery": "true"})
    assert gallery.status_code == 200
    assert any(app["id"] == "tpl_book_recs" for app in gallery.json())
    assert any(app["id"] == "tpl_parallel_ask_demo" for app in gallery.json())
    template = next(app for app in gallery.json() if app["id"] == "tpl_book_recs")
    assert template["cover"] == "upl_tpl_book_recs_cover"
    assert template["can_edit"] is False
    assert template["can_clone"] is True
    parallel_template = next(app for app in gallery.json() if app["id"] == "tpl_parallel_ask_demo")
    assert parallel_template["cover"] == "upl_tpl_parallel_ask_demo_cover"
    assert parallel_template["can_edit"] is False
    assert parallel_template["can_clone"] is True

    market = auth_client.get("/api/apps", params={"market": "true"})
    assert market.status_code == 200
    assert all(app["id"] != "tpl_book_recs" for app in market.json())
    assert all(app["id"] != "tpl_parallel_ask_demo" for app in market.json())

    created = auth_client.post("/api/apps", json={"name": "Demo"}).json()
    assert created["graph"] == {"nodes": [], "edges": []}
    patched = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n1",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "G",
                        "prompt": "hi",
                        "agent_session_id": "old",
                    },
                    {
                        "id": "n_out",
                        "type": "output",
                        "position": {"x": 200, "y": 0},
                        "title": "Output",
                        "prompt": "render",
                        "source_node_id": "n1",
                    },
                ],
                "edges": [{"id": "e_out", "source": "n1", "target": "n_out"}],
            }
        },
    )
    assert patched.status_code == 200
    clone = auth_client.post(f"/api/apps/{created['id']}/clone").json()
    assert clone["graph"]["nodes"][0]["agent_session_id"] is None

    version = auth_client.post(f"/api/apps/{created['id']}/versions", json={"label": "v1"})
    assert version.status_code == 200
    published = auth_client.post(f"/api/apps/{created['id']}/publish")
    assert published.status_code == 200
    assert published.json()["app"]["status"] == "published"

    settings = auth_client.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert all(agent["enabled"] is False for agent in body["agents"])
    assert all(agent["supported_models"] == [] for agent in body["agents"])
    assert auth_client.put("/api/settings", json=body).status_code == 405
    body["agents"][0]["enabled"] = False
    config = auth_client.get("/api/settings/agents/claude-code/config").json()
    saved = auth_client.put(
        "/api/settings/agents/claude-code/config",
        json={"content": config["content"], "enabled": False, "supported_models": ["test-model"]},
    )
    assert saved.status_code == 200
    assert saved.json()["settings"]["agents"][0]["enabled"] is False


def test_settings_redacts_mcp_header_values_for_regular_users(auth_client):
    admin_auth = auth_client.headers["Authorization"]
    secret = "Bearer settings-secret-token"
    created = auth_client.post(
        "/api/settings/mcp",
        json={
            "id": "mcp_secret_headers",
            "name": "Secret Headers MCP",
            "enabled": True,
            "planning_enabled": True,
            "provider_ids": ["claude-code", "codex"],
            "url": "http://localhost:9999/secret",
            "headers": [{"name": "Authorization", "value": secret}],
            "env_var_names": [],
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["mcp_servers"][0]["headers"][0]["value"] == secret

    admin_settings = auth_client.get("/api/settings")
    assert admin_settings.status_code == 200, admin_settings.text
    assert secret in admin_settings.text

    regular = create_regular_user("settings-redacted-user")
    auth_client.headers.update({"Authorization": f"Bearer {regular['token']}"})
    user_settings = auth_client.get("/api/settings", params={"reveal": "true"})
    assert user_settings.status_code == 200, user_settings.text
    body = user_settings.json()
    server = next(item for item in body["mcp_servers"] if item["id"] == "mcp_secret_headers")
    assert server["headers"] == [{"name": "Authorization", "value": ""}]
    assert any(tool["id"] == "mcp:mcp_secret_headers" for tool in body["tools"])
    assert secret not in user_settings.text

    auth_client.headers.update({"Authorization": admin_auth})


def test_patch_app_strips_runtime_tools_snapshot(auth_client):
    created = auth_client.post("/api/apps", json={"name": "StripRuntimeTools"}).json()
    graph = {
        "agent": "claude",
        "_runtime_tools": {"allowed_tool_ids": ["mcp:injected"]},
        "nodes": [],
        "edges": [],
    }
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert response.status_code == 200, response.text
    assert "_runtime_tools" not in response.json()["graph"]
    fetched = auth_client.get(f"/api/apps/{created['id']}")
    assert fetched.status_code == 200, fetched.text
    assert "_runtime_tools" not in fetched.json()["graph"]

    async def stored_graph_json() -> str:
        async with SessionLocal() as db:
            app = await db.get(App, created["id"])
            assert app is not None
            return app.graph_json

    assert "_runtime_tools" not in asyncio.run(stored_graph_json())


def test_patch_app_rejects_asset_upload_owned_by_other_user(auth_client):
    admin_auth = auth_client.headers["Authorization"]
    other = create_regular_user("asset-upload-owner")
    auth_client.headers.update({"Authorization": f"Bearer {other['token']}"})
    upload = auth_client.post(
        "/api/uploads",
        files={"file": ("other.txt", b"other asset", "text/plain")},
    )
    assert upload.status_code == 200, upload.text
    auth_client.headers.update({"Authorization": admin_auth})

    created = auth_client.post("/api/apps", json={"name": "Reject Other Upload"}).json()
    graph = {
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Files",
                "asset_kind": "file",
                "uploads": [upload.json()],
            }
        ],
        "edges": [],
    }
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_patch_app_rejects_missing_drawing_asset_upload(auth_client):
    created = auth_client.post("/api/apps", json={"name": "Reject Missing Drawing"}).json()
    graph = {
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Drawing",
                "asset_kind": "drawing",
                "upload": {"id": "upl_missing", "name": "missing.png"},
            }
        ],
        "edges": [],
    }
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_publish_rejects_empty_graph(auth_client):
    created = auth_client.post("/api/apps", json={"name": "EmptyPublish"}).json()
    response = auth_client.post(f"/api/apps/{created['id']}/publish")
    assert response.status_code == 400
    assert response.json()["detail"] == "工作流没有节点"


def test_publish_rejects_non_empty_graph_without_output(auth_client):
    created = auth_client.post("/api/apps", json={"name": "MissingOutputPublish"}).json()
    graph = {
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Input",
                "input_schema": {"label": "input", "kind": "text"},
            }
        ],
        "edges": [],
    }
    patched = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert patched.status_code == 200, patched.text
    response = auth_client.post(f"/api/apps/{created['id']}/publish")
    assert response.status_code == 400
    assert response.json()["detail"] == "工作流必须包含 output 节点"


def test_publish_revalidates_stored_asset_upload_owner(auth_client):
    created = auth_client.post("/api/apps", json={"name": "Stored Missing Asset"}).json()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Files",
                "asset_kind": "file",
                "uploads": [{"id": "upl_missing", "name": "missing.txt"}],
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 220, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_asset",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
    }

    async def store_graph() -> None:
        async with SessionLocal() as db:
            app = await db.get(App, created["id"])
            assert app is not None
            app.graph_json = dumps(graph)
            await db.commit()

    asyncio.run(store_graph())
    response = auth_client.post(f"/api/apps/{created['id']}/publish")
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_gallery_seed_source_is_readonly_and_resyncs_owner(auth_client):
    admin_auth = auth_client.headers["Authorization"]

    async def dirty_owner_then_seed_again() -> str:
        async with SessionLocal() as db:
            app = await db.get(App, "tpl_book_recs")
            assert app is not None
            app.owner_id = "user_admin"
            await db.commit()
        async with SessionLocal() as db:
            await seed_gallery(db)
        async with SessionLocal() as db:
            app = await db.get(App, "tpl_book_recs")
            assert app is not None
            return app.owner_id

    assert asyncio.run(dirty_owner_then_seed_again()) == GALLERY_OWNER_ID

    gallery = auth_client.get("/api/apps", params={"gallery": "true"})
    assert gallery.status_code == 200
    template = next(app for app in gallery.json() if app["id"] == "tpl_book_recs")
    assert template["can_edit"] is False
    assert template["can_clone"] is True

    auth_client.headers.update({"Authorization": f"Bearer {create_access_token(GALLERY_OWNER_ID)}"})
    assert auth_client.patch("/api/apps/tpl_book_recs", json={"name": "Nope"}).status_code == 404
    assert auth_client.delete("/api/apps/tpl_book_recs").status_code == 404
    assert auth_client.post("/api/apps/tpl_book_recs/publish", json={"visibility": "public"}).status_code == 404
    assert auth_client.post("/api/apps/tpl_book_recs/versions", json={"label": "Nope"}).status_code == 404

    auth_client.headers.update({"Authorization": admin_auth})
    cloned = auth_client.post("/api/apps/clone/tpl_book_recs")
    assert cloned.status_code == 200, cloned.text
    assert cloned.json()["status"] == "draft"
    assert cloned.json()["can_edit"] is True


def test_recent_market_endpoint_is_removed(auth_client):
    response = auth_client.get("/api/apps/recent-market")
    assert response.status_code == 404


def test_recent_runs_include_owned_apps(auth_client):
    first_app = auth_client.post("/api/apps", json={"name": "First Recent App"}).json()
    second_app = auth_client.post("/api/apps", json={"name": "Second Recent App"}).json()

    async def add_runs() -> None:
        started = now_utc()
        async with SessionLocal() as db:
            db.add(
                Run(
                    id="run_recent_first_old",
                    app_id=first_app["id"],
                    owner_id="user_admin",
                    status="success",
                    inputs_json=dumps({}),
                    graph_json=dumps(first_app["graph"]),
                    started_at=started,
                    finished_at=started,
                )
            )
            db.add(
                Run(
                    id="run_recent_second",
                    app_id=second_app["id"],
                    owner_id="user_admin",
                    status="success",
                    inputs_json=dumps({}),
                    graph_json=dumps(second_app["graph"]),
                    started_at=started + timedelta(seconds=1),
                    finished_at=started + timedelta(seconds=1),
                )
            )
            db.add(
                Run(
                    id="run_recent_first_new",
                    app_id=first_app["id"],
                    owner_id="user_admin",
                    status="success",
                    inputs_json=dumps({}),
                    graph_json=dumps(first_app["graph"]),
                    started_at=started + timedelta(seconds=2),
                    finished_at=started + timedelta(seconds=2),
                )
            )
            await db.commit()

    asyncio.run(add_runs())

    recent = auth_client.get("/api/apps/recent-runs")
    assert recent.status_code == 200
    assert [app["id"] for app in recent.json()[:2]] == [first_app["id"], second_app["id"]]
    assert recent.json()[0]["can_edit"] is True


def test_patch_app_rejects_invalid_status(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidStatus"}).json()
    response = auth_client.patch(f"/api/apps/{created['id']}", json={"status": "archived"})
    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数无效"


def test_patch_app_rejects_publish_lifecycle_fields(auth_client):
    created = auth_client.post("/api/apps", json={"name": "PatchPublishBypass"}).json()

    bypass = auth_client.patch(f"/api/apps/{created['id']}", json={"status": "published"})
    assert bypass.status_code == 400
    assert bypass.json()["detail"] == "发布状态请使用 /publish 或 /unpublish 修改"

    app = auth_client.get(f"/api/apps/{created['id']}").json()
    assert app["status"] == "draft"
    assert app["published_at"] is None
    versions = auth_client.get(f"/api/apps/{created['id']}/versions").json()
    assert not any(version["is_published"] for version in versions)

    visibility = auth_client.patch(f"/api/apps/{created['id']}", json={"visibility": "private"})
    assert visibility.status_code == 400
    assert visibility.json()["detail"] == "发布设置请使用 /publish 修改"

    market_access = auth_client.patch(f"/api/apps/{created['id']}", json={"market_access": "run_only"})
    assert market_access.status_code == 400
    assert market_access.json()["detail"] == "发布设置请使用 /publish 修改"

    graph_patch = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _minimal_output_graph()})
    assert graph_patch.status_code == 200, graph_patch.text
    published = auth_client.post(f"/api/apps/{created['id']}/publish", json={"visibility": "public"})
    assert published.status_code == 200, published.text
    assert published.json()["app"]["status"] == "published"
    assert published.json()["app"]["published_at"] is not None
    versions = auth_client.get(f"/api/apps/{created['id']}/versions").json()
    assert any(version["is_published"] for version in versions)

    unpublish_bypass = auth_client.patch(f"/api/apps/{created['id']}", json={"status": "draft"})
    assert unpublish_bypass.status_code == 400
    assert unpublish_bypass.json()["detail"] == "发布状态请使用 /publish 或 /unpublish 修改"
    assert auth_client.get(f"/api/apps/{created['id']}").json()["status"] == "published"


def test_patch_app_cover_requires_current_user_upload(auth_client):
    created = auth_client.post("/api/apps", json={"name": "CoverApp"}).json()
    upload = auth_client.post(
        "/api/uploads",
        files={"file": ("cover.jpg", b"cover image", "image/jpeg")},
    )
    assert upload.status_code == 200, upload.text
    upload_id = upload.json()["id"]

    patched = auth_client.patch(f"/api/apps/{created['id']}", json={"cover": upload_id})
    assert patched.status_code == 200, patched.text
    assert patched.json()["cover"] == upload_id

    cleared = auth_client.patch(f"/api/apps/{created['id']}", json={"cover": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cover"] is None

    invalid = auth_client.patch(f"/api/apps/{created['id']}", json={"cover": "https://example.com/cover.jpg"})
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "封面图片不存在"

    admin_auth = auth_client.headers["Authorization"]
    other = create_regular_user("cover-user")
    auth_client.headers.update({"Authorization": f"Bearer {other['token']}"})
    other_upload = auth_client.post(
        "/api/uploads",
        files={"file": ("other.jpg", b"other image", "image/jpeg")},
    )
    assert other_upload.status_code == 200, other_upload.text
    auth_client.headers.update({"Authorization": admin_auth})
    denied = auth_client.patch(f"/api/apps/{created['id']}", json={"cover": other_upload.json()["id"]})
    assert denied.status_code == 400
    assert denied.json()["detail"] == "封面图片不存在"


def _file_asset_graph(upload_id: str) -> dict:
    return {
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Files",
                "asset_kind": "file",
                "uploads": [{"id": upload_id, "name": "asset.txt"}],
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 220, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_asset",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
    }


def test_patch_app_rejects_missing_asset_upload(auth_client):
    created = auth_client.post("/api/apps", json={"name": "MissingAssetUpload"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={"graph": _file_asset_graph("upl_missing_asset")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_patch_app_rejects_drawing_upload_owned_by_other_user(auth_client):
    created = auth_client.post("/api/apps", json={"name": "ForeignDrawingUpload"}).json()
    admin_auth = auth_client.headers["Authorization"]
    other = create_regular_user("drawing-owner")
    auth_client.headers.update({"Authorization": f"Bearer {other['token']}"})
    uploaded = auth_client.post(
        "/api/uploads",
        files={"file": ("drawing.png", b"drawing asset", "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text

    auth_client.headers.update({"Authorization": admin_auth})
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_drawing",
                        "type": "asset",
                        "position": {"x": 0, "y": 0},
                        "title": "Drawing",
                        "asset_kind": "drawing",
                        "upload": uploaded.json(),
                    }
                ],
                "edges": [],
            }
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_publish_rejects_saved_graph_with_missing_asset_upload(auth_client):
    created = auth_client.post("/api/apps", json={"name": "PublishBadAsset"}).json()

    async def save_bad_graph() -> None:
        async with SessionLocal() as db:
            app = await db.get(App, created["id"])
            assert app is not None
            app.graph_json = dumps(_file_asset_graph("upl_missing_publish_asset"))
            await db.commit()

    asyncio.run(save_bad_graph())

    response = auth_client.post(f"/api/apps/{created['id']}/publish", json={"visibility": "public"})
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_app_cover_endpoint_and_gallery_clone_cover(auth_client):
    gallery_cover = auth_client.get("/api/apps/tpl_book_recs/cover")
    assert gallery_cover.status_code == 200
    assert gallery_cover.headers["content-type"].startswith("image/jpeg")
    parallel_cover = auth_client.get("/api/apps/tpl_parallel_ask_demo/cover")
    assert parallel_cover.status_code == 200
    assert parallel_cover.headers["content-type"].startswith("image/jpeg")

    cloned = auth_client.post("/api/apps/clone/tpl_book_recs")
    assert cloned.status_code == 200, cloned.text
    clone_cover = cloned.json()["cover"]
    assert clone_cover and clone_cover.startswith("upl_")
    assert clone_cover != "upl_tpl_book_recs_cover"

    response = auth_client.get(f"/api/apps/{cloned.json()['id']}/cover")
    assert response.status_code == 200
    assert response.content == gallery_cover.content


def test_patch_app_rejects_invalid_graph_edges(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidGraph"}).json()
    base_nodes = [
        {
            "id": "n_input",
            "type": "user_input",
            "position": {"x": 0, "y": 0},
            "title": "Input",
            "input_schema": {"label": "input", "kind": "textarea"},
        },
        {
            "id": "n_gen",
            "type": "generate",
            "position": {"x": 0, "y": 0},
            "title": "Generate",
            "prompt": "ok",
        },
        {
            "id": "n_out",
            "type": "output",
            "position": {"x": 0, "y": 0},
            "title": "Output",
            "prompt": "ok",
            "source_node_id": "n_gen",
        },
    ]
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": base_nodes,
                "edges": [
                    {"id": "e1", "source": "n_input", "target": "n_gen"},
                    {"id": "e2", "source": "n_gen", "target": "n_out"},
                    {"id": "e3", "source": "n_out", "target": "n_gen"},
                ],
            }
        },
    )
    assert response.status_code == 400
    assert "output 节点不能作为连线起点" in response.json()["detail"]


def test_patch_app_rejects_invalid_output_source(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidOutputSource"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                    },
                    {
                        "id": "n_other",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Other",
                        "prompt": "ok",
                    },
                    {
                        "id": "n_out",
                        "type": "output",
                        "position": {"x": 0, "y": 0},
                        "title": "Output",
                        "prompt": "ok",
                        "source_node_id": "n_other",
                    },
                ],
                "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
            }
        },
    )
    assert response.status_code == 400
    assert "source_node_id" in response.json()["detail"]


def test_patch_app_rejects_multiple_output_nodes(auth_client):
    created = auth_client.post("/api/apps", json={"name": "MultipleOutputs"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                    },
                    {
                        "id": "n_out_a",
                        "type": "output",
                        "position": {"x": 0, "y": 0},
                        "title": "Output A",
                        "prompt": "ok",
                        "source_node_id": "n_gen",
                    },
                    {
                        "id": "n_out_b",
                        "type": "output",
                        "position": {"x": 0, "y": 0},
                        "title": "Output B",
                        "prompt": "ok",
                        "source_node_id": "n_gen",
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "n_gen", "target": "n_out_a"},
                    {"id": "e2", "source": "n_gen", "target": "n_out_b"},
                ],
            }
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "工作流只能有一个 output 节点"


def test_patch_app_rejects_multiple_user_input_nodes(auth_client):
    created = auth_client.post("/api/apps", json={"name": "MultipleInputs"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_input_a",
                        "type": "user_input",
                        "position": {"x": 0, "y": 0},
                        "title": "Input A",
                        "input_schema": {"label": "input a", "kind": "text"},
                    },
                    {
                        "id": "n_input_b",
                        "type": "user_input",
                        "position": {"x": 0, "y": 0},
                        "title": "Input B",
                        "input_schema": {"label": "input b", "kind": "text"},
                    },
                ],
                "edges": [],
            }
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "工作流只能有一个 user_input 节点"


def test_patch_app_rejects_output_contract_on_output_node(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidOutputContract"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                    },
                    {
                        "id": "n_out",
                        "type": "output",
                        "position": {"x": 0, "y": 0},
                        "title": "Output",
                        "prompt": "ok",
                        "source_node_id": "n_gen",
                        "output_contract": {"type": "json"},
                    },
                ],
                "edges": [{"id": "e1", "source": "n_gen", "target": "n_out"}],
            }
        },
    )
    assert response.status_code == 400
    assert "只有 generate 支持 output_contract" in response.json()["detail"]


def test_patch_app_rejects_invalid_artifact_kind(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidArtifactKind"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {"type": "artifact", "artifact_kind": "exe"},
                    },
                ],
                "edges": [],
            }
        },
    )
    assert response.status_code == 400
    assert "artifact 输出契约必须包含有效 artifact_kind" in response.json()["detail"]


def test_patch_app_accepts_zip_artifact_kind(auth_client):
    created = auth_client.post("/api/apps", json={"name": "ZipArtifact"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {"type": "artifact", "artifact_kind": "zip"},
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["graph"]["nodes"][0]["output_contract"] == {
        "type": "artifact",
        "artifact_kind": "zip",
    }


def test_patch_app_accepts_office_document_validation(auth_client):
    created = auth_client.post("/api/apps", json={"name": "OfficeValidation"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {
                            "type": "artifact",
                            "artifact_kind": "zip",
                            "validate_office_documents": True,
                        },
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["graph"]["nodes"][0]["output_contract"] == {
        "type": "artifact",
        "artifact_kind": "zip",
        "validate_office_documents": True,
    }


def test_patch_app_rejects_non_bool_office_document_validation(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidOfficeValidation"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {
                            "type": "artifact",
                            "artifact_kind": "zip",
                            "validate_office_documents": "true",
                        },
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 400
    assert "validate_office_documents 必须是 bool" in response.json()["detail"]


def test_patch_app_rejects_office_validation_for_non_office_artifact_kind(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidOfficeArtifactKind"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {
                            "type": "artifact",
                            "artifact_kind": "image",
                            "validate_office_documents": True,
                        },
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 400
    assert "validate_office_documents 仅支持 artifact_kind" in response.json()["detail"]


def test_patch_app_normalizes_stale_output_contract_fields(auth_client):
    created = auth_client.post("/api/apps", json={"name": "StaleOutputContract"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {
                            "type": "artifact",
                            "artifact_kind": "archive",
                            "json_schema": {"type": "object", "additionalProperties": False},
                        },
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 200, response.text
    node = response.json()["graph"]["nodes"][0]
    assert node["output_contract"] == {"type": "artifact", "artifact_kind": "archive"}


def test_patch_app_rejects_json_field_level_output_contract(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidJsonContract"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "output_contract": {"type": "json", "required_fields": ["title"]},
                    },
                ],
                "edges": [],
            }
        },
    )
    assert response.status_code == 400
    assert "不支持的字段" in response.json()["detail"]


def test_patch_app_rejects_non_boolean_ask_user_enabled(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidAskUserEnabled"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                        "ask_user_enabled": "false",
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 400
    assert "ask_user_enabled 必须是 bool" in response.json()["detail"]


def test_patch_app_rejects_ask_user_enabled_on_non_generate_node(auth_client):
    created = auth_client.post("/api/apps", json={"name": "UnsupportedAskUserEnabled"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "n_input",
                        "type": "user_input",
                        "position": {"x": 0, "y": 0},
                        "title": "Input",
                        "input_schema": {"label": "input", "kind": "text"},
                        "ask_user_enabled": False,
                    },
                ],
                "edges": [],
            }
        },
    )

    assert response.status_code == 400
    assert "只有 generate 支持 ask_user_enabled" in response.json()["detail"]


def test_artifact_kind_prompt_suffix_specializes_file_hint():
    suffix = contract_prompt_suffix(
        {
            "id": "n_gen",
            "type": "generate",
            "title": "Generate",
            "prompt": "ok",
            "output_contract": {"type": "artifact", "artifact_kind": "ppt"},
        }
    )

    assert "PPT 产物" in suffix
    assert ".pptx" in suffix
    assert "path" in suffix
    assert "path" in suffix


def test_zip_artifact_prompt_suffix_requires_zip_file():
    suffix = contract_prompt_suffix(
        {
            "id": "n_gen",
            "type": "generate",
            "title": "Generate",
            "prompt": "ok",
            "output_contract": {"type": "artifact", "artifact_kind": "zip"},
        }
    )

    assert "ZIP 压缩包产物" in suffix
    assert "应生成 .zip 文件" in suffix


def test_patch_app_rejects_invalid_condition_handle(auth_client):
    created = auth_client.post("/api/apps", json={"name": "InvalidCondition"}).json()
    response = auth_client.patch(
        f"/api/apps/{created['id']}",
        json={
            "graph": {
                "agent": "claude",
                "nodes": [
                    {
                        "id": "n_cond",
                        "type": "condition",
                        "position": {"x": 0, "y": 0},
                        "title": "Condition",
                        "prompt": "ok",
                        "mode": "binary",
                        "branches": [{"key": "true"}, {"key": "false"}],
                    },
                    {
                        "id": "n_gen",
                        "type": "generate",
                        "position": {"x": 0, "y": 0},
                        "title": "Generate",
                        "prompt": "ok",
                    },
                ],
                "edges": [{"id": "e1", "source": "n_cond", "target": "n_gen", "source_handle": "maybe"}],
            }
        },
    )
    assert response.status_code == 400
    assert "source_handle 无效" in response.json()["detail"]


def test_gallery_and_version_clone_clear_agent_session_id(auth_client):
    gallery_clone = auth_client.post("/api/apps/clone/tpl_book_recs")
    assert gallery_clone.status_code == 200
    assert all(
        node.get("agent_session_id") is None
        for node in gallery_clone.json()["graph"]["nodes"]
        if node["type"] == "generate"
    )

    app = auth_client.post("/api/apps", json={"name": "VersionClone"}).json()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "gen",
                "type": "generate",
                "position": {"x": 0, "y": 0},
                "title": "Gen",
                "prompt": "hi",
                "agent_session_id": "session_old",
            }
        ],
        "edges": [],
    }
    auth_client.patch(f"/api/apps/{app['id']}", json={"graph": graph})
    version = auth_client.post(f"/api/apps/{app['id']}/versions", json={"label": "has session"}).json()
    cloned = auth_client.post(f"/api/versions/{version['id']}/clone")
    assert cloned.status_code == 200
    assert cloned.json()["graph"]["nodes"][0]["agent_session_id"] is None


def test_gallery_clone_is_idempotent_per_user(auth_client):
    first = auth_client.post("/api/apps/clone/tpl_book_recs")
    assert first.status_code == 200
    second = auth_client.post("/api/apps/clone/tpl_book_recs")
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]

    my_apps = auth_client.get("/api/apps", params={"mine": "true"}).json()
    assert [app["id"] for app in my_apps].count(first.json()["id"]) == 1

    source = auth_client.post("/api/apps", json={"name": "Copyable"}).json()
    clone_a = auth_client.post(f"/api/apps/{source['id']}/clone")
    clone_b = auth_client.post(f"/api/apps/{source['id']}/clone")
    assert clone_a.status_code == 200
    assert clone_b.status_code == 200
    assert clone_a.json()["id"] != clone_b.json()["id"]


def test_clone_app_rejects_saved_graph_with_missing_asset_upload(auth_client):
    source = auth_client.post("/api/apps", json={"name": "Bad Asset Clone Source"}).json()

    async def save_bad_graph() -> None:
        async with SessionLocal() as db:
            app = await db.get(App, source["id"])
            assert app is not None
            app.graph_json = dumps(_file_asset_graph("upl_missing_clone_asset"))
            await db.commit()

    asyncio.run(save_bad_graph())

    before_ids = {app["id"] for app in auth_client.get("/api/apps", params={"mine": "true"}).json()}
    response = auth_client.post(f"/api/apps/{source['id']}/clone")
    after_ids = {app["id"] for app in auth_client.get("/api/apps", params={"mine": "true"}).json()}
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"
    assert after_ids == before_ids


def test_clone_app_via_template_route_rejects_saved_graph_with_missing_asset_upload(auth_client):
    source = auth_client.post("/api/apps", json={"name": "Bad Asset Template Clone Source"}).json()

    async def save_bad_graph() -> None:
        async with SessionLocal() as db:
            app = await db.get(App, source["id"])
            assert app is not None
            app.status = "published"
            app.visibility = "public"
            app.graph_json = dumps(_file_asset_graph("upl_missing_template_clone_asset"))
            await db.commit()

    asyncio.run(save_bad_graph())

    response = auth_client.post(f"/api/apps/clone/{source['id']}")
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_clone_version_rejects_saved_graph_with_missing_asset_upload(auth_client):
    source = auth_client.post("/api/apps", json={"name": "Bad Asset Version Source"}).json()

    async def create_bad_version() -> str:
        async with SessionLocal() as db:
            version = AppVersion(
                id="ver_bad_asset_upload",
                app_id=source["id"],
                label="bad asset",
                name="Bad Asset Version",
                description="",
                graph_json=dumps(_file_asset_graph("upl_missing_version_asset")),
                is_published=False,
                created_at=now_utc(),
            )
            db.add(version)
            await db.commit()
            return version.id

    version_id = asyncio.run(create_bad_version())

    response = auth_client.post(f"/api/versions/{version_id}/clone")
    assert response.status_code == 400
    assert response.json()["detail"] == "素材上传文件不存在"


def test_clone_app_copies_file_asset_uploads_for_new_owner(auth_client):
    uploaded = auth_client.post(
        "/api/uploads",
        files={"file": ("source.txt", b"source asset", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    source_upload = uploaded.json()
    source = auth_client.post("/api/apps", json={"name": "Asset Copy Source"}).json()
    graph = {
        "nodes": [
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 0, "y": 0},
                "title": "Files",
                "asset_kind": "file",
                "uploads": [source_upload],
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 220, "y": 0},
                "title": "Output",
                "prompt": "render",
                "source_node_id": "n_asset",
            },
        ],
        "edges": [{"id": "e_out", "source": "n_asset", "target": "n_out"}],
    }
    patched = auth_client.patch(f"/api/apps/{source['id']}", json={"graph": graph})
    assert patched.status_code == 200, patched.text
    published = auth_client.post(f"/api/apps/{source['id']}/publish", json={"visibility": "public"})
    assert published.status_code == 200, published.text

    other = create_regular_user("asset-cloner")
    auth_client.headers.update({"Authorization": f"Bearer {other['token']}"})
    clone = auth_client.post(f"/api/apps/{source['id']}/clone")
    assert clone.status_code == 200, clone.text
    clone_upload = clone.json()["graph"]["nodes"][0]["uploads"][0]
    assert clone_upload["id"] != source_upload["id"]
    assert clone_upload["name"] == "source.txt"
    assert resolve_upload(other["id"], clone_upload["id"]) is not None


def test_published_apps_market_visibility_and_readonly_access(auth_client, enable_claude_agent):
    enable_claude_agent()
    admin_auth = auth_client.headers["Authorization"]
    public_app = auth_client.post("/api/apps", json={"name": "Public Market App"}).json()
    private_app = auth_client.post("/api/apps", json={"name": "Private Market App"}).json()
    public_patch = auth_client.patch(f"/api/apps/{public_app['id']}", json={"graph": _minimal_output_graph()})
    assert public_patch.status_code == 200, public_patch.text
    private_patch = auth_client.patch(f"/api/apps/{private_app['id']}", json={"graph": _minimal_output_graph()})
    assert private_patch.status_code == 200, private_patch.text

    public_publish = auth_client.post(f"/api/apps/{public_app['id']}/publish", json={"visibility": "public"})
    assert public_publish.status_code == 200, public_publish.text
    assert public_publish.json()["app"]["visibility"] == "public"
    private_publish = auth_client.post(f"/api/apps/{private_app['id']}/publish", json={"visibility": "private"})
    assert private_publish.status_code == 200, private_publish.text
    assert private_publish.json()["app"]["visibility"] == "private"

    own_market = auth_client.get("/api/apps", params={"market": "true"}).json()
    own_by_id = {app["id"]: app for app in own_market}
    assert own_by_id[public_app["id"]]["can_edit"] is True
    assert own_by_id[private_app["id"]]["can_edit"] is True

    other = create_regular_user("market-user")
    auth_client.headers.update({"Authorization": f"Bearer {other['token']}"})

    other_market = auth_client.get("/api/apps", params={"market": "true"}).json()
    other_by_id = {app["id"]: app for app in other_market}
    assert public_app["id"] in other_by_id
    assert other_by_id[public_app["id"]]["can_edit"] is False
    assert private_app["id"] not in other_by_id

    visible = auth_client.get(f"/api/apps/{public_app['id']}")
    assert visible.status_code == 200
    assert visible.json()["can_edit"] is False
    assert auth_client.get(f"/api/apps/{private_app['id']}").status_code == 404
    assert auth_client.patch(f"/api/apps/{public_app['id']}", json={"name": "Nope"}).status_code == 404
    assert auth_client.delete(f"/api/apps/{public_app['id']}").status_code == 404
    assert auth_client.post(f"/api/apps/{public_app['id']}/versions", json={"label": "Nope"}).status_code == 404

    lint = auth_client.post(f"/api/apps/{public_app['id']}/lint", json={})
    assert lint.status_code == 200
    run = auth_client.post("/api/runs", json={"app_id": public_app["id"], "inputs": {}})
    assert run.status_code == 200, run.text
    runs = auth_client.get(f"/api/apps/{public_app['id']}/runs")
    assert runs.status_code == 200
    assert any(item["id"] == run.json()["run_id"] for item in runs.json())

    clone = auth_client.post(f"/api/apps/{public_app['id']}/clone")
    assert clone.status_code == 200, clone.text
    assert clone.json()["status"] == "draft"
    assert clone.json()["can_edit"] is True
    assert clone.json()["market_access"] == "cloneable"

    auth_client.headers.update({"Authorization": admin_auth})


def test_run_only_market_app_blocks_clone_hides_source_and_tracks_recent(auth_client, enable_claude_agent):
    enable_claude_agent()
    admin_auth = auth_client.headers["Authorization"]
    created = auth_client.post("/api/apps", json={"name": "Run Only Market App"}).json()
    graph = {
        "agent": "claude",
        "nodes": [
            {
                "id": "n_input",
                "type": "user_input",
                "position": {"x": 0, "y": 0},
                "title": "Secret Input Node",
                "input_schema": {"label": "Your request", "kind": "textarea"},
            },
            {
                "id": "n_asset",
                "type": "asset",
                "position": {"x": 220, "y": 0},
                "title": "Hidden Asset Node",
                "asset_kind": "text",
                "content": "internal source text",
            },
            {
                "id": "n_out",
                "type": "output",
                "position": {"x": 440, "y": 0},
                "title": "Secret Output Node",
                "prompt": "render [[respond:<section>ok</section>]]",
                "source_node_id": "n_input",
            },
        ],
        "edges": [
            {"id": "e_input_out", "source": "n_input", "target": "n_out"},
            {"id": "e_asset_out", "source": "n_asset", "target": "n_out"},
        ],
    }
    patched = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": graph})
    assert patched.status_code == 200, patched.text
    published = auth_client.post(
        f"/api/apps/{created['id']}/publish",
        json={"visibility": "public", "market_access": "run_only"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["app"]["market_access"] == "run_only"
    assert published.json()["app"]["can_clone"] is True
    assert published.json()["app"]["can_view_source"] is True

    other = create_regular_user("run-only-user")
    auth_client.headers.update({"Authorization": f"Bearer {other['token']}"})

    market = auth_client.get("/api/apps", params={"market": "true"})
    assert market.status_code == 200
    market_app = next(app for app in market.json() if app["id"] == created["id"])
    assert market_app["can_clone"] is False
    assert market_app["can_view_source"] is False
    assert [node["type"] for node in market_app["graph"]["nodes"]] == ["user_input", "output"]
    assert market_app["graph"]["nodes"][0]["title"] == "Your request"
    assert market_app["graph"]["edges"] == [{"id": "public_n_input_n_out", "source": "n_input", "target": "n_out"}]

    visible = auth_client.get(f"/api/apps/{created['id']}")
    assert visible.status_code == 200
    assert visible.json()["graph"] == market_app["graph"]
    lint = auth_client.post(f"/api/apps/{created['id']}/lint", json={})
    assert lint.status_code == 200, lint.text
    lint_body = lint.json()
    assert lint_body["ok"] is True
    assert lint_body["issues"] == []
    assert "Secret Input Node" not in lint.text
    assert "Hidden Asset Node" not in lint.text
    assert "internal source text" not in lint.text
    clone = auth_client.post(f"/api/apps/{created['id']}/clone")
    assert clone.status_code == 403

    run = auth_client.post("/api/runs", json={"app_id": created["id"], "inputs": {"n_input": "hello"}})
    assert run.status_code == 200, run.text
    assert [node["type"] for node in run.json()["graph"]["nodes"]] == ["user_input", "output"]

    runs = auth_client.get(f"/api/apps/{created['id']}/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["graph"] == run.json()["graph"]
    assert [step["node_id"] for step in runs.json()[0]["steps"]] == ["n_input", "n_out"]

    recent_runs = auth_client.get("/api/apps/recent-runs")
    assert recent_runs.status_code == 200
    assert [app["id"] for app in recent_runs.json()] == [created["id"]]
    assert recent_runs.json()[0]["can_view_source"] is False
    assert [node["type"] for node in recent_runs.json()[0]["graph"]["nodes"]] == ["user_input", "output"]

    auth_client.headers.update({"Authorization": admin_auth})


def test_version_limit_keeps_published_versions(auth_client):
    created = auth_client.post("/api/apps", json={"name": "Versions"}).json()
    patched = auth_client.patch(f"/api/apps/{created['id']}", json={"graph": _minimal_output_graph()})
    assert patched.status_code == 200, patched.text
    published = auth_client.post(f"/api/apps/{created['id']}/publish")
    assert published.status_code == 200
    published_version_id = published.json()["version"]["id"]
    for index in range(55):
        response = auth_client.post(f"/api/apps/{created['id']}/versions", json={"label": f"snap {index}"})
        assert response.status_code == 200
    versions = auth_client.get(f"/api/apps/{created['id']}/versions").json()
    assert any(version["id"] == published_version_id for version in versions)
    unpublished = [version for version in versions if not version.get("is_published")]
    assert len(unpublished) <= 50


def test_skill_zip_upload(auth_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: pdf-summarizer\ndescription: 总结 PDF\n---\n")
    buffer.seek(0)
    response = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("skill.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    skill = response.json()
    assert skill["name"] == "pdf-summarizer"
    settings = auth_client.get("/api/settings").json()
    assert any(tool["id"] == f"skill:{skill['id']}" and tool["enabled"] for tool in settings["tools"])
    assert not (claude_home() / ".claude" / "skills" / skill["id"]).exists()
    assert not (codex_home() / ".agents" / "skills" / skill["id"]).exists()


def test_skill_markdown_preview_reads_root_and_nested_skill_md(auth_client):
    root_buffer = io.BytesIO()
    with zipfile.ZipFile(root_buffer, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: root-preview\n---\n# Root Skill\n")
    root_buffer.seek(0)
    root_skill = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("root-preview.zip", root_buffer.getvalue(), "application/zip")},
    ).json()
    root_preview = auth_client.get(f"/api/settings/skills/{root_skill['id']}/skill-md")
    assert root_preview.status_code == 200, root_preview.text
    assert root_preview.json()["path"] == "SKILL.md"
    assert "# Root Skill" in root_preview.json()["content"]

    nested_buffer = io.BytesIO()
    with zipfile.ZipFile(nested_buffer, "w") as zf:
        zf.writestr("nested/SKILL.md", "---\nname: nested-preview\n---\n# Nested Skill\n")
    nested_buffer.seek(0)
    nested_skill = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("nested-preview.zip", nested_buffer.getvalue(), "application/zip")},
    ).json()
    nested_preview = auth_client.get(f"/api/settings/skills/{nested_skill['id']}/skill-md")
    assert nested_preview.status_code == 200, nested_preview.text
    assert nested_preview.json()["path"] == "nested/SKILL.md"
    assert "# Nested Skill" in nested_preview.json()["content"]


def test_skill_markdown_preview_returns_404_without_skill_md(auth_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("README.md", "# Missing skill file\n")
    buffer.seek(0)
    skill = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("missing-skill-md.zip", buffer.getvalue(), "application/zip")},
    ).json()
    response = auth_client.get(f"/api/settings/skills/{skill['id']}/skill-md")
    assert response.status_code == 404
    assert response.json()["detail"] == "未找到 SKILL.md"


def test_skill_runtime_sync_on_disable_enable_and_delete(auth_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: runtime-sync\ndescription: sync me\n---\n")
    buffer.seek(0)
    skill = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("sync.zip", buffer.getvalue(), "application/zip")},
    ).json()
    claude_skill = claude_home() / ".claude" / "skills" / skill["id"]
    codex_skill = codex_home() / ".agents" / "skills" / skill["id"]
    assert not claude_skill.exists()
    assert not codex_skill.exists()

    disabled = auth_client.patch(f"/api/settings/skills/{skill['id']}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert not claude_skill.exists()
    assert not codex_skill.exists()
    assert any(tool["id"] == f"skill:{skill['id']}" and not tool["enabled"] for tool in disabled.json()["tools"])

    enabled = auth_client.patch(f"/api/settings/skills/{skill['id']}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert not claude_skill.exists()
    assert not codex_skill.exists()
    assert any(tool["id"] == f"skill:{skill['id']}" and tool["enabled"] for tool in enabled.json()["tools"])

    deleted = auth_client.delete(f"/api/settings/skills/{skill['id']}")
    assert deleted.status_code == 204
    assert not claude_skill.exists()
    assert not codex_skill.exists()


def test_invalid_skill_zip_upload_is_rejected(auth_client):
    response = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("broken.zip", b"not a zip", "application/zip")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "无效的 Skill zip"


def test_skill_removed_from_settings_is_deleted(auth_client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: removable\ndescription: delete me\n---\n")
    buffer.seek(0)
    skill = auth_client.post(
        "/api/skills/parse",
        files={"archive": ("remove.zip", buffer.getvalue(), "application/zip")},
    ).json()
    response = auth_client.delete(f"/api/settings/skills/{skill['id']}")
    assert response.status_code == 204
    assert all(item["id"] != skill["id"] for item in auth_client.get("/api/settings").json()["skills"])
