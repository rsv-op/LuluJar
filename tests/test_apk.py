"""The Android manifest reader."""

from __future__ import annotations

import struct

import pytest

from axml_fixture import build_manifest, sample_manifest_elements
from lulujar import apk


def test_inspect_apk_reports_identity(sample_apk: str) -> None:
    info = apk.inspect_apk(sample_apk)
    assert info["package"] == "com.example.lulujar"
    assert info["version_name"] == "1.4.2"
    assert info["version_code"] == 42
    assert info["min_sdk"] == 21
    assert info["target_sdk"] == 34


def test_inspect_apk_reports_permissions(sample_apk: str) -> None:
    info = apk.inspect_apk(sample_apk)
    assert "android.permission.INTERNET" in info["permissions"]
    assert "android.permission.CAMERA" in info["permissions"]


def test_inspect_apk_reports_components(sample_apk: str) -> None:
    info = apk.inspect_apk(sample_apk)
    assert info["activities"] == ["com.example.lulujar.MainActivity"]
    assert info["services"] == ["com.example.lulujar.SyncService"]


def test_inspect_apk_reports_layout(sample_apk: str) -> None:
    info = apk.inspect_apk(sample_apk)
    assert info["dex_files"] == ["classes.dex"]
    assert info["abis"] == ["arm64-v8a"]
    assert info["signed"] is True
    assert info["assets"] == ["assets/config.json"]


def test_axml_parses_utf8_pool() -> None:
    data = build_manifest(sample_manifest_elements(), utf8=True)
    document = apk.AxmlDocument(data)
    assert [element["name"] for element in document.elements][:3] == [
        "manifest",
        "uses-sdk",
        "uses-permission",
    ]


def test_axml_parses_utf16_pool() -> None:
    data = build_manifest(sample_manifest_elements(), utf8=False)
    document = apk.AxmlDocument(data)
    manifest = document.elements[0]
    values = {a["name"]: a for a in manifest["attributes"]}
    assert values["package"]["raw"] == "com.example.lulujar"
    assert values["versionCode"]["value"] == 42


def test_axml_rejects_garbage() -> None:
    with pytest.raises(apk.AxmlError):
        apk.AxmlDocument(b"not xml at all")


def test_inspect_apk_handles_broken_file(tmp_path) -> None:
    broken = tmp_path / "broken.apk"
    broken.write_bytes(b"PK\x03\x04 garbage")
    info = apk.inspect_apk(str(broken))
    assert info["warnings"]
    assert info["package"] is None
