"""Extraction engines: listing, extraction, safety and progress."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from lulujar import engine, formats


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_list_zip_entries(sample_zip: str) -> None:
    entries = engine.list_entries(sample_zip, formats.ZIP)
    names = {entry.path for entry in entries}
    assert "readme.txt" in names
    assert "sub/deep/note.md" in names


def test_extract_zip(sample_zip: str, tmp_path: Path) -> None:
    destination = tmp_path / "out"
    result = engine.extract(sample_zip, str(destination))
    assert result.files == 3
    assert (destination / "readme.txt").exists()
    assert _read(destination / "readme.txt") == "hello lulujar\n"
    assert (destination / "sub" / "deep" / "note.md").exists()


def test_extract_tar(sample_tar: str, tmp_path: Path) -> None:
    destination = tmp_path / "out"
    engine.extract(sample_tar, str(destination))
    assert (destination / "readme.txt").exists()
    assert (destination / "sub" / "data.bin").exists()


def test_extract_apk(sample_apk: str, tmp_path: Path) -> None:
    destination = tmp_path / "out"
    engine.extract(sample_apk, str(destination))
    assert (destination / "AndroidManifest.xml").exists()
    assert (destination / "classes.dex").exists()
    assert (destination / "lib" / "arm64-v8a" / "libdemo.so").exists()


def test_progress_callback_is_called(sample_zip: str, tmp_path: Path) -> None:
    events: list = []
    engine.extract(
        sample_zip,
        str(tmp_path / "out"),
        progress=lambda payload: events.append(payload),
    )
    assert events
    assert any(event.get("phase") == "done" for event in events)


def test_zip_slip_is_blocked(tmp_path: Path) -> None:
    """A member that tries to escape the destination must not be written."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escaped.txt", "pwned")
        handle.writestr("normal.txt", "fine")

    destination = tmp_path / "out"
    result = engine.extract(str(archive), str(destination))

    assert not (tmp_path / "escaped.txt").exists()
    assert (destination / "normal.txt").exists()
    assert any("unsafe" in warning for warning in result.warnings)


def test_absolute_paths_are_blocked(tmp_path: Path) -> None:
    archive = tmp_path / "absolute.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("/etc/evil.txt", "pwned")

    destination = tmp_path / "out"
    engine.extract(str(archive), str(destination))
    assert not Path("/etc/evil.txt").exists()


def test_sanitize_member_path() -> None:
    assert engine.sanitize_member_path("a/b/c.txt") == "a/b/c.txt"
    assert engine.sanitize_member_path("./a.txt") == "a.txt"
    # Traversal and absolute paths are refused outright.
    assert engine.sanitize_member_path("../a.txt") is None
    assert engine.sanitize_member_path("a/../../b.txt") is None
    assert engine.sanitize_member_path("/etc/passwd") == "etc/passwd"
    assert engine.sanitize_member_path("C:\\windows\\evil") is None
    assert engine.sanitize_member_path("") is None


def test_build_tree(sample_zip: str, tmp_path: Path) -> None:
    destination = tmp_path / "out"
    engine.extract(sample_zip, str(destination))
    tree = engine.build_tree(str(destination))
    names = {node["name"] for node in tree}
    assert "readme.txt" in names
    assert "sub" in names


def test_engines_are_discovered() -> None:
    engines = engine.discover_engines(refresh=True)
    names = {item.name for item in engines}
    assert "zipfile" in names
    assert "tarfile" in names


def test_zip_is_always_supported() -> None:
    assert engine.best_engine(formats.ZIP) is not None
    assert engine.best_engine(formats.APK) is not None
    assert engine.best_engine(formats.TAR) is not None


def test_extract_rejects_unknown_format(tmp_path: Path) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not an archive at all")
    with pytest.raises(engine.ExtractionError):
        engine.extract(str(junk), str(tmp_path / "out"), kind=formats.UNKNOWN)


def test_cancellation_stops_extraction(sample_zip: str, tmp_path: Path) -> None:
    with pytest.raises(engine.ExtractionCancelled):
        engine.extract(
            sample_zip,
            str(tmp_path / "out"),
            cancel=lambda: True,
        )


def test_rar_engine_available_when_libarchive_present() -> None:
    """The bundled WASM engine should give us RAR support out of the box."""
    import shutil

    if not shutil.which("node"):
        pytest.skip("node is not installed")
    assert engine.best_engine(formats.RAR) is not None
