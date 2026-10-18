"""Android APK inspection.

An APK is a ZIP file, so extraction is handled by the generic engine.  On top
of that this module reads the binary ``AndroidManifest.xml`` and reports the
package identity, SDK levels, permissions and components - useful when you
want to know what you just unpacked.
"""

from __future__ import annotations

import struct
import zipfile
from typing import Dict, List, Optional, Tuple, Tuple

# ResChunk_header types
_RES_STRING_POOL = 0x0001
_RES_XML = 0x0003
_RES_XML_START_NAMESPACE = 0x0100
_RES_XML_END_NAMESPACE = 0x0101
_RES_XML_START_ELEMENT = 0x0102
_RES_XML_END_ELEMENT = 0x0103
_RES_XML_CDATA = 0x0104
_RES_XML_RESOURCE_MAP = 0x0180

# Res_value data types
_TYPE_REFERENCE = 0x01
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11
_TYPE_INT_BOOLEAN = 0x12

ANDROID_NS = "http://schemas.android.com/apk/res/android"

#: Framework resource ids we resolve to a friendly name.
_ANDROID_NAMES = {
    0x0101021B: "versionCode",
    0x0101021C: "versionName",
    0x01010572: "minSdkVersion",
    0x01010270: "targetSdkVersion",
    0x01010573: "maxSdkVersion",
    0x0101057B: "compileSdkVersion",
    0x01010576: "compileSdkVersionCodename",
    0x0101021B + 0: "versionCode",
}


class AxmlError(RuntimeError):
    pass


class StringPool:
    """The string pool of a binary XML document."""

    def __init__(self, data: bytes, offset: int = 0):
        self._strings: List[str] = []
        self._parse(data, offset)

    def _parse(self, data: bytes, offset: int) -> None:
        if len(data) < offset + 28:
            raise AxmlError("string pool header truncated")

        (
            _type,
            _header_size,
            _size,
            string_count,
            style_count,
            flags,
            strings_start,
            _styles_start,
        ) = struct.unpack_from("<HHIIIIII", data, offset)

        utf8 = bool(flags & (1 << 8))
        offsets_start = offset + 28
        strings_base = offset + strings_start

        for index in range(string_count):
            (string_offset,) = struct.unpack_from("<I", data, offsets_start + index * 4)
            position = strings_base + string_offset
            if position >= len(data):
                self._strings.append("")
                continue
            self._strings.append(
                _read_utf8_string(data, position)
                if utf8
                else _read_utf16_string(data, position)
            )

    def __len__(self) -> int:
        return len(self._strings)

    def get(self, index: int) -> str:
        if index < 0 or index >= len(self._strings):
            return ""
        return self._strings[index]


def _read_len8(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a length prefix that may use the high-bit extension scheme."""
    value = data[pos]
    pos += 1
    if value & 0x80:
        value = (value & 0x7F) << 8
        value |= data[pos]
        pos += 1
    return value, pos


def _read_utf8_string(data: bytes, pos: int) -> str:
    # character count (may be 2 bytes)
    _chars, pos = _read_len8(data, pos)
    # byte length (may be 2 bytes)
    byte_len, pos = _read_len8(data, pos)
    raw = data[pos : pos + byte_len]
    return raw.decode("utf-8", "replace")


def _read_utf16_string(data: bytes, pos: int) -> str:
    length = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    if length & 0x8000:
        low = length & 0x7FFF
        high = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        length = (high << 16) | low
    raw = data[pos : pos + length * 2]
    return raw.decode("utf-16-le", "replace")


class AxmlDocument:
    """A very small binary-XML reader - just enough for AndroidManifest.xml."""

    def __init__(self, data: bytes):
        pool_offset, pool_end = self._locate_pool(data)
        self.strings = StringPool(data, pool_offset)
        self.elements: List[Dict[str, object]] = []
        self._parse(data, pool_end)

    def _parse(self, data: bytes, offset: int) -> None:
        while offset + 8 <= len(data):
            chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
            if chunk_size <= 0:
                break
            if chunk_type == _RES_XML_START_ELEMENT:
                self._parse_start_element(data, offset, header_size)
            offset += chunk_size

    @staticmethod
    def _locate_pool(data: bytes) -> Tuple[int, int]:
        """Return ``(offset, end)`` of the string pool chunk."""
        if len(data) < 8:
            raise AxmlError("file too small to be an AXML document")
        _type, header_size, size = struct.unpack_from("<HHI", data, 0)
        if _type != _RES_XML:
            raise AxmlError("not a binary XML document")
        # The string pool is the first chunk after the file header.
        pool_type, _pool_header, pool_size = struct.unpack_from("<HHI", data, header_size)
        if pool_type != _RES_STRING_POOL:
            raise AxmlError("string pool missing")
        return header_size, header_size + pool_size

    def _parse_start_element(self, data: bytes, offset: int, header_size: int) -> None:
        # ResXMLTree_node: header(8) + lineNumber(4) + comment(4) = 16
        base = offset + header_size
        ns, name_index = struct.unpack_from("<II", data, base)
        attribute_start, attribute_size, attribute_count = struct.unpack_from(
            "<HHH", data, base + 8
        )

        attributes: List[Dict[str, object]] = []
        attr_offset = base + attribute_start
        for index in range(attribute_count):
            position = attr_offset + index * attribute_size
            if position + 20 > len(data):
                break
            attr_ns, attr_name, raw_value = struct.unpack_from("<III", data, position)
            _size, _res0, data_type, value = struct.unpack_from("<HBBI", data, position + 12)
            attributes.append(
                {
                    "ns": self.strings.get(attr_ns),
                    "name": self.strings.get(attr_name),
                    "raw": self.strings.get(raw_value) if raw_value != 0xFFFFFFFF else "",
                    "type": data_type,
                    "value": value,
                }
            )

        self.elements.append(
            {
                "name": self.strings.get(name_index),
                "ns": self.strings.get(ns),
                "attributes": attributes,
            }
        )


def _attr_value(attr: Dict[str, object]) -> Optional[object]:
    data_type = attr.get("type")
    value = attr.get("value")
    if data_type == _TYPE_STRING:
        raw = attr.get("raw")
        return raw if raw else None
    if data_type in (_TYPE_INT_DEC, _TYPE_INT_HEX):
        return int(value)
    if data_type == _TYPE_INT_BOOLEAN:
        return bool(value)
    if data_type == _TYPE_REFERENCE:
        return None
    return value


def inspect_apk(path: str) -> Dict[str, object]:
    """Return a summary of an Android APK."""
    result: Dict[str, object] = {
        "path": path,
        "name": path.rsplit("/", 1)[-1],
        "package": None,
        "version_name": None,
        "version_code": None,
        "min_sdk": None,
        "target_sdk": None,
        "compile_sdk": None,
        "permissions": [],
        "features": [],
        "activities": [],
        "services": [],
        "receivers": [],
        "providers": [],
        "native_libraries": [],
        "dex_files": [],
        "assets": [],
        "signature_files": [],
        "signed": False,
        "abis": [],
        "entry_count": 0,
        "uncompressed_size": 0,
        "is_split": False,
        "warnings": [],
    }

    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        result["warnings"].append(f"cannot open APK: {exc}")
        return result

    with archive:
        names = archive.namelist()
        result["entry_count"] = len(names)
        result["uncompressed_size"] = sum(i.file_size for i in archive.infolist())
        result["dex_files"] = [n for n in names if n.endswith(".dex")]
        result["assets"] = [n for n in names if n.startswith("assets/")][:200]
        result["signature_files"] = [
            n
            for n in names
            if n.upper().startswith("META-INF/")
            and n.upper().endswith((".RSA", ".DSA", ".EC", ".SF", ".MF"))
        ]
        result["signed"] = any(
            n.upper().endswith((".RSA", ".DSA", ".EC")) for n in names
        )
        libs = [n for n in names if n.startswith("lib/") and n.endswith(".so")]
        abis = sorted({n.split("/")[1] for n in libs if n.count("/") >= 2})
        result["abis"] = abis
        result["native_libraries"] = libs[:200]

        if "AndroidManifest.xml" not in names:
            result["warnings"].append("AndroidManifest.xml is missing")
            return result

        try:
            manifest_bytes = archive.read("AndroidManifest.xml")
        except (KeyError, zipfile.BadZipFile, OSError) as exc:
            result["warnings"].append(f"cannot read manifest: {exc}")
            return result

    try:
        document = AxmlDocument(manifest_bytes)
    except (AxmlError, struct.error, IndexError) as exc:
        result["warnings"].append(f"manifest could not be parsed: {exc}")
        return result

    for element in document.elements:
        name = str(element.get("name") or "")
        attributes = element.get("attributes") or []
        flat = {}
        for attr in attributes:
            attr_name = str(attr.get("name") or "")
            if not attr_name:
                continue
            flat[attr_name] = _attr_value(attr)

        if name == "manifest":
            result["package"] = flat.get("package")
            result["version_name"] = flat.get("versionName")
            result["version_code"] = flat.get("versionCode")
            result["compile_sdk"] = flat.get("compileSdkVersion")
            if flat.get("split"):
                result["is_split"] = True
        elif name == "uses-sdk":
            result["min_sdk"] = flat.get("minSdkVersion")
            result["target_sdk"] = flat.get("targetSdkVersion")
        elif name == "uses-permission":
            permission = flat.get("name")
            if permission:
                result["permissions"].append(str(permission))
        elif name == "uses-feature":
            feature = flat.get("name")
            if feature:
                result["features"].append(str(feature))
        elif name == "activity":
            result["activities"].append(flat.get("name"))
        elif name == "service":
            result["services"].append(flat.get("name"))
        elif name == "receiver":
            result["receivers"].append(flat.get("name"))
        elif name == "provider":
            result["providers"].append(flat.get("name"))

    for key in ("activities", "services", "receivers", "providers"):
        result[key] = [v for v in result[key] if v]
    result["permissions"] = sorted(set(result["permissions"]))
    result["features"] = sorted(set(result["features"]))
    return result
