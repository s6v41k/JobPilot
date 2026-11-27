"""Static checks of the web UI files (no browser). The clicking tests are in uitest/smoke.py."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

WEB = Path(__file__).parent.parent / "web"
ROOT = WEB.parent


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_every_local_asset_in_index_html_is_served(client):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    paths = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    assert {"/style.css", "/util.js", "/app.js", "/vendor/vue.global.prod.js"} <= set(paths)
    for path in paths:
        assert client.get(path).status_code == 200, path


def test_the_page_needs_no_internet(client):
    """The demo may run without Wi-Fi: no script, style or font may come from another site."""
    for name in ("index.html", "style.css", "app.js", "util.js"):
        text = (WEB / name).read_text(encoding="utf-8")
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"|url\((https?://[^)]+)\)|@import\s+["\']https?://', text)
        # links the user clicks (job ads, sources) are fine; loaded resources are not
        loaded = [e for e in external if e and ("." in "".join(e))]
        assert not [e for e in loaded if re.search(r"\.(js|css|woff2?|ttf)\b", "".join(e))], f"{name}: {loaded}"


def test_vue_is_vendored_with_its_licence_header():
    head = (WEB / "vendor" / "vue.global.prod.js").read_text(encoding="utf-8")[:200]
    assert "vue v3" in head and "MIT" in head


def test_every_api_path_used_by_the_javascript_exists_on_the_server():
    routes = {r.path for r in main.app.routes}
    templated = [re.sub(r"\{[^}]+\}", "{}", p) for p in routes]
    used = set(re.findall(r'["\'`](/api/[a-zA-Z0-9/_-]*)', (WEB / "app.js").read_text(encoding="utf-8")))
    assert used, "app.js should call the API"
    for path in used:
        ok = path in routes or path.rstrip("/") in routes or any(t.startswith(path.rstrip("/")) for t in templated)
        assert ok, f"app.js calls {path} but the server has no such route"


def test_html_has_no_unbalanced_vue_blocks():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for tag in ("section", "header", "article", "details", "form", "template", "nav"):
        assert len(re.findall(rf"<{tag}[\s>]", html)) == len(re.findall(rf"</{tag}>", html)), tag


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_javascript_helpers_pass_their_node_tests():
    result = subprocess.run(["node", "--test", "tests/js/util.test.js"], cwd=ROOT, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-500:]
