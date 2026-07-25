from pathlib import Path

from fastapi.testclient import TestClient

from sentinel_ops.camera_upload import UPLOAD_ROOT
from sentinel_ops.main import app


def test_demo_seed_and_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("SENTINEL_DATABASE_PATH", str(tmp_path / "ops.db"))
    client = TestClient(app)
    seeded = client.post("/api/demo/seed")
    assert seeded.status_code == 200
    assert seeded.json()["seeded_events"] >= 1
    reset = client.delete("/api/demo/reset")
    assert reset.status_code == 200
    assert reset.json()["storage"]["events"] == 0


def test_media_path_traversal_is_rejected():
    client = TestClient(app)
    response = client.get("/api/cameras/media/not-a-batch/../../README.md")
    assert response.status_code == 404
