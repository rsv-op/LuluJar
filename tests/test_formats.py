"""Archive type detection."""

from __future__ import annotations

import tarfile
import zipfile

from lulujar import formats


def test_detects_zip(sample_zip: str) -> None:
    assert formats.detect(sample_zip) == formats.ZIP


def test_detects_apk(sample_apk: str) -> None:
    assert formats.detect(sample_apk) == formats.APK


def test_detects_tar_gz(sample_tar: str) -> None:
    # A .tar.gz must not be mistaken for a plain gzip file.
    assert formats.detect(sample_tar) == formats.TAR


def test_detects_plain_gzip(tmp_path) -> None:
    import gzip

    path = tmp_path / "note.txt.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(b"data")
    assert formats.detect(str(path)) == formats.GZIP


def test_detects_rar_magic(tmp_path) -> None:
    path = tmp_path / "fake.rar"
    path.write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 32)
    assert formats.detect(str(path)) == formats.RAR


def test_detects_rar5_magic(tmp_path) -> None:
    path = tmp_path / "fake5.rar"
    path.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32)
    assert formats.detect(str(path)) == formats.RAR


def test_detects_7z_magic(tmp_path) -> None:
    path = tmp_path / "fake.7z"
    path.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 32)
    assert formats.detect(str(path)) == formats.SEVENZ


def test_unknown_extension(tmp_path) -> None:
    path = tmp_path / "mystery.bin"
    path.write_bytes(b"random bytes")
    assert formats.detect(str(path)) == formats.UNKNOWN


def test_zip_without_apk_markers_is_not_apk(sample_zip: str) -> None:
    assert formats.detect(sample_zip) != formats.APK


def test_looks_like_apk(sample_apk: str) -> None:
    assert formats.looks_like_apk(sample_apk) is True


def test_extension_map_covers_common_archives() -> None:
    assert formats.extension_type("a.rar") == formats.RAR
    assert formats.extension_type("a.apk") == formats.APK
    assert formats.extension_type("a.zip") == formats.ZIP
    assert formats.extension_type("a.tar.gz") == formats.TAR
    assert formats.extension_type("a.7z") == formats.SEVENZ


def test_describe_returns_label() -> None:
    assert formats.describe(formats.APK) == "Android APK"
    assert formats.describe(formats.RAR) == "RAR archive"


def test_is_archive_rejects_plain_files() -> None:
    assert formats.is_archive(formats.UNKNOWN) is False
    assert formats.is_archive(formats.RAR) is True
