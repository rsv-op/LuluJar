"""The HTTP API."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from lulujar import config, engine, formats
from lulujar.jobs import JobManager
from lulujar.server import LuluJarHandler, LuluJarServer


@pytest.fixture()
def server(tmp_path: Path, sample_zip: str):
    """A running server on a free port with a temporary home directory."""
    config.ensure_dirs()
    settings = config.Settings()
    roots = [tmp_path]
    jobs = JobManager(persist=False)

    httpd = LuluJarServer(("127.0.0.1", 0), LuluJarHandler, settings, roots, jobs, "", False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield f"http://{host}:{port}", tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.loads(response.read().decode())


def post(base: str, path: str, payload: dict | None = None, raw: bytes | None = None) -> dict:
    if raw is not None:
        request = urllib.request.Request(
            base + path, data=raw, method="POST",
            headers={"Content-Type": "application/octet-stream"},
        )
    else:
        request = urllib.request.Request(
            base + path,
            data=json.dumps(payload or {}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def test_health(server) -> None:
    base, _ = server
    health = get(base, "/api/health")
    assert health["service"] == "LuluJar"
    assert health["engines"]


def test_index_is_served(server) -> None:
    base, _ = server
    with urllib.request.urlopen(base + "/", timeout=30) as response:
        body = response.read().decode()
    assert "LuluJar" in body
    assert response.status == 200


def test_upload_and_extract(server, sample_zip: str) -> None:
    base, tmp_path = server
    payload = Path(sample_zip).read_bytes()

    started = post(base, "/api/upload/start?name=sample.zip")
    upload_id = started["id"]

    midpoint = len(payload) // 2
    post(base, f"/api/upload/chunk?id={upload_id}&offset=0", raw=payload[:midpoint])
    post(base, f"/api/upload/chunk?id={upload_id}&offset={midpoint}", raw=payload[midpoint:])

    completed = post(base, f"/api/upload/complete?id={upload_id}")
    assert completed["size"] == len(payload)
    assert completed["kind"] == formats.ZIP

    destination = str(tmp_path / "extracted")
    job = post(
        base,
        "/api/jobs",
        {
            "source_path": completed["path"],
            "source_name": "sample.zip",
            "destination": destination,
        },
    )

    for _ in range(100):
        state = get(base, f"/api/jobs/{job['id']}")
        if state["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.1)

    assert state["status"] == "done", state.get("error")
    assert Path(destination, "readme.txt").exists()

    tree = get(base, f"/api/tree?path={destination}")
    assert any(node["name"] == "readme.txt" for node in tree["tree"])


def test_list_archive_entries(server, sample_zip: str) -> None:
    base, _ = server
    entries = get(base, f"/api/entries?path={sample_zip}")
    assert entries["kind"] == formats.ZIP
    assert entries["count"] >= 3


def test_filesystem_browsing_is_sandboxed(server) -> None:
    base, tmp_path = server
    listing = get(base, f"/api/fs/list?path={tmp_path}")
    assert listing["path"]

    with pytest.raises(urllib.error.HTTPError) as error:
        get(base, "/api/fs/list?path=/etc")
    assert error.value.code == 403


def test_preview_and_download(server, sample_zip: str, tmp_path: Path) -> None:
    base, _ = server
    target = tmp_path / "note.txt"
    target.write_text("hello preview", encoding="utf-8")

    preview = get(base, f"/api/preview?path={target}")
    assert preview["content"] == "hello preview"

    with urllib.request.urlopen(base + f"/api/download?path={target}", timeout=30) as response:
        assert response.read() == b"hello preview"


def test_apk_endpoint(server, sample_apk: str) -> None:
    base, _ = server
    info = get(base, f"/api/apk?path={sample_apk}")
    assert info["package"] == "com.example.lulujar"


def test_delete_endpoint(server, tmp_path: Path) -> None:
    base, _ = server
    target = tmp_path / "doomed"
    target.mkdir()
    (target / "file.txt").write_text("bye", encoding="utf-8")
    result = post(base, "/api/delete", {"path": str(target)})
    assert result["deleted"] is True
    assert not target.exists()


def test_unknown_endpoint(server) -> None:
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as error:
        get(base, "/api/does-not-exist")
    assert error.value.code == 404
