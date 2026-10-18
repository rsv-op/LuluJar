"""Shared pytest fixtures for the LuluJar test-suite."""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axml_fixture import build_manifest, sample_manifest_elements  # noqa: E402


@pytest.fixture()
def sample_tree(tmp_path: Path) -> Path:
    """A small directory tree used to build archives."""
    root = tmp_path / "tree"
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "readme.txt").write_text("hello lulujar\n", encoding="utf-8")
    (root / "sub" / "data.bin").write_bytes(bytes(range(256)) * 4)
    (root / "sub" / "deep" / "note.md").write_text("# note\n", encoding="utf-8")
    return root


@pytest.fixture()
def sample_zip(sample_tree: Path, tmp_path: Path) -> str:
    path = tmp_path / "sample.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(sample_tree.rglob("*")):
            if item.is_file():
                archive.write(item, str(item.relative_to(sample_tree)))
    return str(path)


@pytest.fixture()
def sample_tar(sample_tree: Path, tmp_path: Path) -> str:
    import tarfile

    path = tmp_path / "sample.tar"
    with tarfile.open(path, "w:gz") as archive:
        for item in sorted(sample_tree.rglob("*")):
            archive.add(item, arcname=str(item.relative_to(sample_tree)))
    return str(path)


@pytest.fixture()
def sample_apk(sample_tree: Path, tmp_path: Path) -> str:
    path = tmp_path / "sample.apk"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", build_manifest(sample_manifest_elements()))
        archive.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 64)
        archive.writestr("lib/arm64-v8a/libdemo.so", b"\x7fELF" + b"\x00" * 32)
        archive.writestr("assets/config.json", b'{"debug": false}')
        archive.writestr("META-INF/CERT.RSA", b"\x30\x82demo")
    return str(path)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the LuluJar home directory into a temporary folder."""
    root = tmp_path / "lulujar-home"
    monkeypatch.setenv("LULUJAR_HOME", str(root))
    root.mkdir(parents=True, exist_ok=True)
    return root
