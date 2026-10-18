"""Archive type detection: extension hints plus magic-byte sniffing.

Detection is intentionally conservative: a file is only labelled with a type we
can actually handle, so the UI never promises an extraction that will fail.
"""

from __future__ import annotations

import os
import zipfile
from typing import Optional

# Canonical type names used across the code base.
ZIP = "zip"
APK = "apk"
RAR = "rar"
SEVENZ = "7z"
TAR = "tar"
GZIP = "gzip"
BZ2 = "bz2"
XZ = "xz"
UNKNOWN = "unknown"

#: Extension -> canonical type.
EXTENSION_MAP = {
    ".zip": ZIP,
    ".jar": ZIP,
    ".war": ZIP,
    ".ear": ZIP,
    ".apk": APK,
    ".aab": ZIP,
    ".apks": ZIP,
    ".xapk": ZIP,
    ".apkm": ZIP,
    ".ipa": ZIP,
    ".rar": RAR,
    ".7z": SEVENZ,
    ".tar": TAR,
    ".tar.gz": TAR,
    ".tgz": TAR,
    ".tar.bz2": TAR,
    ".tbz2": TAR,
    ".tbz": TAR,
    ".tar.xz": TAR,
    ".txz": TAR,
    ".tar.zst": TAR,
    ".gz": GZIP,
    ".bz2": BZ2,
    ".xz": XZ,
}

#: Human readable labels for the UI.
TYPE_LABELS = {
    ZIP: "ZIP archive",
    APK: "Android APK",
    RAR: "RAR archive",
    SEVENZ: "7-Zip archive",
    TAR: "TAR archive",
    GZIP: "GZIP (single file)",
    BZ2: "BZIP2 (single file)",
    XZ: "XZ (single file)",
    UNKNOWN: "Unknown",
}

#: Magic byte signatures, longest first.
_MAGIC = (
    (b"Rar!\x1a\x07\x00", RAR),        # RAR 1.5 - 4.x
    (b"Rar!\x1a\x07\x01\x00", RAR),    # RAR 5.x
    (b"PK\x03\x04", ZIP),              # ZIP / APK / OOXML
    (b"PK\x05\x06", ZIP),              # empty ZIP
    (b"PK\x07\x08", ZIP),              # spanned ZIP
    (b"7z\xbc\xaf\x27\x1c", SEVENZ),   # 7-Zip
    (b"\x1f\x8b", GZIP),
    (b"BZh", BZ2),
    (b"\xfd7zXZ\x00", XZ),
    (b"ustar", TAR),                    # ustar header (offset 257)
)


def _normalise_extension(name: str) -> str:
    """Return a lower-case extension, understanding compound ones (.tar.gz)."""
    lower = name.lower()
    for compound in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if lower.endswith(compound):
            return compound
    return os.path.splitext(lower)[1]


def extension_type(name: str) -> str:
    """Type implied by the file name, or ``UNKNOWN``."""
    return EXTENSION_MAP.get(_normalise_extension(name), UNKNOWN)


def sniff_magic(head: bytes, name: str = "") -> str:
    """Type implied by the leading bytes of a file.

    ``head`` should be the first few hundred bytes of the file.  When the
    signature is ambiguous (a ZIP could really be an APK) the file name is
    used as a tie-breaker.
    """
    if not head:
        return extension_type(name)

    ext_kind = extension_type(name)

    for signature, kind in _MAGIC:
        if head.startswith(signature):
            if kind == ZIP:
                # APK is a ZIP with a specific layout - use the name hint.
                if ext_kind == APK:
                    return APK
                return ZIP
            if kind == TAR:
                return TAR
            if kind in (GZIP, BZ2, XZ):
                # .tar.gz / .tar.bz2 / .tar.xz are tarballs wearing a hat.
                if ext_kind == TAR:
                    return TAR
                return kind
            return kind

    # tar headers live at offset 257, not at the start of the file.
    if len(head) > 262 and head[257:262] == b"ustar":
        return TAR

    return ext_kind


def detect(path: str) -> str:
    """Detect the archive type of ``path`` using magic bytes, then the name."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(512)
    except OSError:
        return extension_type(path)

    kind = sniff_magic(head, os.path.basename(path))

    # A ZIP/APK is only an APK when it really looks like one.
    if kind == APK and not looks_like_apk(path):
        return ZIP

    # A gzip stream may really contain a tarball.
    if kind == GZIP and _contains_tar(path):
        return TAR
    return kind


def _contains_tar(path: str) -> bool:
    """Peek inside a gzip stream and look for a tar header."""
    import gzip

    try:
        with gzip.open(path, "rb") as handle:
            head = handle.read(512)
    except (OSError, EOFError, gzip.BadGzipFile):
        return False
    return len(head) > 262 and head[257:262] == b"ustar"


def looks_like_apk(path: str) -> bool:
    """True when a ZIP archive has the structural markers of an Android APK."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return False

    if "AndroidManifest.xml" in names and "classes.dex" in names:
        return True
    # Split APKs keep the manifest under a resource package.
    return any(
        name.endswith("/AndroidManifest.xml") for name in names
    ) and any(name.endswith(".dex") for name in names)


def is_archive(kind: str) -> bool:
    """Whether the type can be extracted (as opposed to a single compressed file)."""
    return kind in (ZIP, APK, RAR, SEVENZ, TAR, GZIP, BZ2, XZ)


def describe(kind: str) -> str:
    """Human readable label for a canonical type."""
    return TYPE_LABELS.get(kind, TYPE_LABELS[UNKNOWN])


def can_handle(kind: str) -> bool:
    """Whether the running installation has *some* engine for this type."""
    from . import engine  # local import to avoid a cycle

    return bool(engine.engines_for(kind))
