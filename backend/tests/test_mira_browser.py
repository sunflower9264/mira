from __future__ import annotations

import subprocess
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIRA_BROWSER = BACKEND_ROOT / "runtime" / "scripts" / "mira-browser"


def test_wrapper_rejects_runtime_binding_overrides() -> None:
    for option in (
        "--browser=firefox",
        "--config=custom.json",
        "--profile=shared",
        "--persistent",
        "--cdp=http://127.0.0.1:9222",
        "--endpoint=ws://127.0.0.1:9222",
        "--extension=chrome",
        "--headed",
        "install-browser",
    ):
        result = subprocess.run(
            ["bash", str(MIRA_BROWSER), "open", option],
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 64
        assert "unsupported" in result.stderr


def test_wrapper_pins_browser_policy_and_disables_downloads() -> None:
    script = MIRA_BROWSER.read_text(encoding="utf-8")

    assert "readonly PLAYWRIGHT_VERSION=0.1.18" in script
    assert "readonly CHROMIUM_BINARY=/usr/bin/chromium" in script
    assert "export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1" in script
    assert "export PLAYWRIGHT_MCP_CONFIG=\"$MIRA_CONFIG\"" in script
    assert "export PLAYWRIGHT_MCP_EXECUTABLE_PATH=\"$CHROMIUM_BINARY\"" in script
    assert "export PLAYWRIGHT_MCP_HEADLESS=true" in script
    assert "export PLAYWRIGHT_MCP_ISOLATED=true" in script
    assert "export PLAYWRIGHT_MCP_SANDBOX=false" in script
    assert 'if [[ "${1:-}" == "open" ]]; then' in script
    assert 'exec "$PLAYWRIGHT_CLI" --config "$MIRA_CONFIG" "$@"' in script
    assert "npx" not in script
