"""Build a binary AndroidManifest.xml (AXML) for tests.

The layout follows the AOSP ``ResourceTypes.h`` specification:

* file header          - ``RES_XML_TYPE``
* string pool chunk    - ``RES_STRING_POOL_TYPE``
* resource map chunk   - ``RES_XML_RESOURCE_MAP_TYPE``
* namespace / element chunks

The generator is deliberately independent from :mod:`lulujar.apk` so the parser
can be checked against a third party implementation (apkutils2).
"""

from __future__ import annotations

import struct
from typing import List, Sequence, Tuple

ANDROID_NS = "http://schemas.android.com/apk/res/android"

RES_XML_TYPE = 0x0003
RES_STRING_POOL_TYPE = 0x0001
RES_XML_RESOURCE_MAP_TYPE = 0x0180
RES_XML_START_NAMESPACE_TYPE = 0x0100
RES_XML_END_NAMESPACE_TYPE = 0x0101
RES_XML_START_ELEMENT_TYPE = 0x0102
RES_XML_END_ELEMENT_TYPE = 0x0103

TYPE_REFERENCE = 0x01
TYPE_STRING = 0x03
TYPE_INT_DEC = 0x10
TYPE_INT_BOOLEAN = 0x12

NO_ENTRY = 0xFFFFFFFF


class _Pool:
    def __init__(self, utf8: bool = True):
        self.utf8 = utf8
        self.strings: List[str] = []
        self.index: dict = {}

    def add(self, value: str) -> int:
        if value in self.index:
            return self.index[value]
        position = len(self.strings)
        self.strings.append(value)
        self.index[value] = position
        return position

    def encode(self) -> bytes:
        flags = 0x100 if self.utf8 else 0
        offsets = []
        blob = bytearray()
        for value in self.strings:
            offsets.append(len(blob))
            if self.utf8:
                encoded = value.encode("utf-8")
                blob += _encode_length8(len(value))
                blob += _encode_length8(len(encoded))
                blob += encoded
                blob += b"\x00"
            else:
                blob += _encode_length16(len(value))
                blob += value.encode("utf-16-le")
                blob += b"\x00\x00"

        header_size = 28
        strings_start = header_size + 4 * len(offsets)
        size = strings_start + len(blob)

        header = struct.pack(
            "<HHIIIIII",
            RES_STRING_POOL_TYPE,
            header_size,
            size,
            len(self.strings),
            0,          # styleCount
            flags,      # flags
            strings_start,
            0,          # stylesStart
        )
        return header + b"".join(struct.pack("<I", offset) for offset in offsets) + bytes(blob)


def _encode_length8(value: int) -> bytes:
    if value > 0x7F:
        return bytes([0x80 | (value >> 8), value & 0xFF])
    return bytes([value])


def _encode_length16(value: int) -> bytes:
    if value > 0x7FFF:
        return struct.pack("<HH", 0x8000 | (value >> 16), value & 0xFFFF)
    return struct.pack("<H", value)


def _attribute(ns_index: int, name_index: int, raw_index: int, value_type: int, data: int) -> bytes:
    """Build one 20 byte ``ResXMLTree_attr`` structure."""
    return struct.pack(
        "<IIIHBBI", ns_index, name_index, raw_index, 8, 0, value_type, data
    )


def build_manifest(
    elements: Sequence[Tuple[str, Sequence[Tuple[str, int, object]]]],
    utf8: bool = True,
) -> bytes:
    """Build a binary manifest.

    ``elements`` is a sequence of ``(element_name, attributes)`` where each
    attribute is ``(name, value_type, value)``.  A ``<manifest>`` element is
    added automatically with the android namespace.
    """
    pool = _Pool(utf8=utf8)

    # Framework attribute names come first so a resource map makes sense.
    attribute_names: List[str] = []
    for _, attributes in elements:
        for name, _, _ in attributes:
            if name not in attribute_names:
                attribute_names.append(name)
    for name in attribute_names:
        pool.add(name)

    android_index = pool.add(ANDROID_NS)
    resource_ids = [0x0101021B + index for index in range(len(attribute_names))]

    # Element names and values.
    element_indices: List[int] = []
    for name, _ in elements:
        element_indices.append(pool.add(name))

    value_indices: List[List[int]] = []
    for _, attributes in elements:
        row = []
        for _, value_type, value in attributes:
            row.append(pool.add(str(value)) if value_type == TYPE_STRING else NO_ENTRY)
        value_indices.append(row)

    # ---- chunks -------------------------------------------------------
    resource_map = struct.pack("<HHI", RES_XML_RESOURCE_MAP_TYPE, 8, 8 + 4 * len(resource_ids))
    resource_map += b"".join(struct.pack("<I", rid) for rid in resource_ids)

    # Namespace chunk: node header (16) + prefix (4) + uri (4) = 24 bytes
    start_namespace = struct.pack(
        "<HHIII", RES_XML_START_NAMESPACE_TYPE, 16, 24, 1, NO_ENTRY
    ) + struct.pack("<II", android_index, android_index)
    end_namespace = struct.pack(
        "<HHIII", RES_XML_END_NAMESPACE_TYPE, 16, 24, 1, NO_ENTRY
    ) + struct.pack("<II", android_index, android_index)

    body = bytearray()
    for index, (name, attributes) in enumerate(elements):
        attribute_count = len(attributes)
        attribute_start = 20
        attribute_size = 20
        chunk_size = 16 + attribute_start + attribute_size * attribute_count

        chunk = struct.pack(
            "<HHIII",
            RES_XML_START_ELEMENT_TYPE,
            16,
            chunk_size,
            index + 1,
            NO_ENTRY,
        )
        chunk += struct.pack("<II", NO_ENTRY, element_indices[index])
        chunk += struct.pack(
            "<HHHHHH",
            attribute_start,
            attribute_size,
            attribute_count,
            0,   # idIndex
            0,   # classIndex
            0,   # styleIndex
        )
        for position, (attr_name, value_type, value) in enumerate(attributes):
            name_index = pool.index[attr_name]
            raw_index = value_indices[index][position]
            if value_type == TYPE_STRING:
                data = raw_index
            elif value_type == TYPE_INT_BOOLEAN:
                data = 1 if value else 0
            else:
                data = int(value)
            chunk += _attribute(android_index, name_index, raw_index, value_type, data)
        body += chunk

        body += struct.pack(
            "<HHIII", RES_XML_END_ELEMENT_TYPE, 16, 24, index + 1, NO_ENTRY
        )
        body += struct.pack("<II", NO_ENTRY, element_indices[index])

    payload = (
        pool.encode()
        + resource_map
        + start_namespace
        + bytes(body)
        + end_namespace
    )
    header = struct.pack("<HHI", RES_XML_TYPE, 8, 8 + len(payload))
    return header + payload


def sample_manifest_elements() -> List[Tuple[str, List[Tuple[str, int, object]]]]:
    """A small but realistic manifest."""
    return [
        (
            "manifest",
            [
                ("package", TYPE_STRING, "com.example.lulujar"),
                ("versionCode", TYPE_INT_DEC, 42),
                ("versionName", TYPE_STRING, "1.4.2"),
                ("compileSdkVersion", TYPE_INT_DEC, 34),
            ],
        ),
        (
            "uses-sdk",
            [
                ("minSdkVersion", TYPE_INT_DEC, 21),
                ("targetSdkVersion", TYPE_INT_DEC, 34),
            ],
        ),
        (
            "uses-permission",
            [("name", TYPE_STRING, "android.permission.INTERNET")],
        ),
        (
            "uses-permission",
            [("name", TYPE_STRING, "android.permission.CAMERA")],
        ),
        (
            "uses-feature",
            [("name", TYPE_STRING, "android.hardware.camera")],
        ),
        (
            "application",
            [("label", TYPE_STRING, "LuluJar"), ("debuggable", TYPE_INT_BOOLEAN, True)],
        ),
        ("activity", [("name", TYPE_STRING, "com.example.lulujar.MainActivity")]),
        ("service", [("name", TYPE_STRING, "com.example.lulujar.SyncService")]),
    ]
