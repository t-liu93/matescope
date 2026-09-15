from pathlib import Path

from fastapi.testclient import TestClient
from matescope.main import app, create_app


def test_health_contract() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_errors_are_not_hidden_by_spa() -> None:
    response = TestClient(app).get("/api/v1/does-not-exist")
    assert response.status_code == 404


def test_pwa_static_files_keep_explicit_mime_and_cache_policy(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "assets").mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>MateScope</title>")
    (static_dir / "manifest.webmanifest").write_text('{"name":"MateScope"}')
    (static_dir / "sw.js").write_text("self.addEventListener('fetch', () => {});")
    (static_dir / "offline.html").write_text("offline")
    client = TestClient(create_app(static_dir=static_dir))
    manifest = client.get("/manifest.webmanifest")
    worker = client.get("/sw.js")
    offline = client.get("/offline.html")
    assert manifest.status_code == 200
    assert "manifest" in manifest.headers["content-type"]
    assert manifest.headers["cache-control"] == "no-cache"
    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert offline.status_code == 200
    assert offline.headers["cache-control"] == "no-cache"


def test_missing_worker_and_manifest_are_not_spa_fallbacks(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "assets").mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>MateScope</title>")
    client = TestClient(create_app(static_dir=static_dir))
    assert client.get("/sw.js").status_code == 404
    assert client.get("/manifest.webmanifest").status_code == 404
