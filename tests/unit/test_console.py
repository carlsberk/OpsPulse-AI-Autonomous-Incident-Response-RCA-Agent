"""The review console is served with a strict CSP and renders untrusted data only via textContent."""

import pathlib
import re

from src.console import STATIC_DIR


def test_console_page_has_strict_csp_and_security_headers(client):
    resp = client.get("/console")
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cache-control"] == "no-store"


def test_static_assets_are_served(client):
    for name, kind in (("app.js", "javascript"), ("styles.css", "css")):
        resp = client.get(f"/console/static/{name}")
        assert resp.status_code == 200 and kind in resp.headers["content-type"]


def test_api_responses_carry_baseline_security_headers(client):
    resp = client.get("/healthz")
    assert resp.headers["x-frame-options"] == "DENY" and resp.headers["referrer-policy"] == "no-referrer"


def test_console_has_no_inline_script_or_unsafe_dom_sinks():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> would violate the CSP"
    assert not re.search(r"\sstyle\s*=", html), "inline styles would violate the CSP"
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handlers would violate the CSP"
    source = pathlib.Path(STATIC_DIR / "app.js").read_text(encoding="utf-8")
    js = "\n".join(line for line in source.splitlines() if not line.strip().startswith("//"))  # ignore comments
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js, f"{sink} must not be used with untrusted data"


def test_console_can_be_disabled(monkeypatch):
    import importlib

    import src.main as main_module

    monkeypatch.setenv("CONSOLE_ENABLED", "false")
    try:
        from src import config

        monkeypatch.setattr(config, "settings", config.Settings())
        reloaded = importlib.reload(main_module)
        paths = {getattr(route, "path", None) for route in reloaded.app.routes}
        assert "/console" not in paths
    finally:
        monkeypatch.undo()
        importlib.reload(main_module)


def test_console_loads_nothing_from_other_origins_and_never_sets_a_style_attribute():
    sources = {name: (STATIC_DIR / name).read_text(encoding="utf-8") for name in ("index.html", "app.js", "styles.css")}
    for name, text in sources.items():
        assert not re.search(r"https?://(?!localhost)", re.sub(r"//.*", "", text)), f"{name} references another origin"
        assert "@import" not in text and "url(" not in sources["styles.css"]
    js = sources["app.js"]
    assert 'setAttribute("style"' not in js and "cssText" not in js  # CSSOM property writes only (CSP-safe)
    assert 'href="/console/static/styles.css"' in sources["index.html"]


def test_console_has_the_confirmation_dialog_and_live_regions():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<dialog" in html and 'aria-live="polite"' in html and 'class="skip-link"' in html
    assert 'id="approve"' in html and 'id="confirm-ok"' in html


def test_console_script_is_syntactically_valid():
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    args = [node, "--check", str(STATIC_DIR / "app.js")]  # fixed arguments, no untrusted input
    result = subprocess.run(args, capture_output=True, text=True, check=False)  # noqa: S603
    assert result.returncode == 0, result.stderr


def test_console_ask_panel_is_wired_and_uses_only_text_nodes():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="ask-form"' in html and 'id="ask-log"' in html
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "/ask`" in js and "result.answer" in js
    assert "node.textContent = result.answer" in js  # model output is rendered as text, never as markup
    assert "/guidance`" in js and "plainMarkup" in js


def test_demo_key_is_embedded_only_when_explicitly_configured(client, set_settings):
    assert "opspulse-demo-key" not in client.get("/console").text  # off by default
    set_settings(console_demo_key='demo"key')
    html = client.get("/console").text
    assert '<meta name="opspulse-demo-key" content="demo&quot;key">' in html  # escaped, never raw
    assert "unsafe-inline" not in client.get("/console").headers["content-security-policy"]


def test_console_supports_theme_and_status_query_options():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'params.get("theme")' in js and 'params.get("status")' in js
    assert 'meta[name="opspulse-demo-key"]' in js
