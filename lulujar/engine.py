"""Extraction engines.

LuluJar talks to whatever is available on the machine and degrades gracefully:

* ``7zz`` / ``7z`` / ``7za`` / ``7zr`` - best all-round engine (RAR5, multi
  volume, 7z, zip, tar ...).
* ``unrar`` - RARLAB reference decoder.
* ``unar`` - The Unarchiver, understands RAR5.
* ``bsdtar`` - libarchive.
* ``rarfile`` (pip) - Python RAR parser, delegates the heavy lifting to one of
  the binaries above.
* ``unrar-cffi`` (pip) - in-process RAR decoder, no external binary needed.
* ``py7zr`` (pip) - pure Python 7z reader.
* Python standard library - ``zipfile`` for ZIP/APK and ``tarfile`` for TAR.

Nothing here is required: ZIP and APK work out of the box on a bare Python
install, and the rest is enabled automatically as soon as a helper is present.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from . import formats

ProgressCallback = Callable[[Dict[str, object]], None]
CancelCheck = Callable[[], bool]


class ExtractionError(RuntimeError):
    """Raised when an archive cannot be extracted."""


class ExtractionCancelled(RuntimeError):
    """Raised when a user cancels a running extraction."""


@dataclass
class Entry:
    """A single member of an archive."""

    path: str
    size: int = 0
    packed_size: int = 0
    is_dir: bool = False
    crc: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "packed_size": self.packed_size,
            "is_dir": self.is_dir,
            "crc": self.crc,
        }


@dataclass
class ExtractResult:
    """Summary of a finished extraction."""

    destination: str
    engine: str
    entries: int = 0
    files: int = 0
    dirs: int = 0
    bytes_written: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "destination": self.destination,
            "engine": self.engine,
            "entries": self.entries,
            "files": self.files,
            "dirs": self.dirs,
            "bytes_written": self.bytes_written,
            "warnings": list(self.warnings),
        }


@dataclass
class Engine:
    """A discovered extraction backend."""

    name: str
    kind: str          # "binary" or "library"
    kinds: tuple       # archive types it can handle
    location: str = ""  # executable path or "module"
    version: str = ""
    script: str = ""    # helper script (used by the node engine)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "kinds": list(self.kinds),
            "location": self.location,
            "version": self.version,
        }


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

_BINARY_CANDIDATES = {
    "7zz": ("7zip", (formats.ZIP, formats.APK, formats.RAR, formats.SEVENZ, formats.TAR)),
    "7z": ("7zip", (formats.ZIP, formats.APK, formats.RAR, formats.SEVENZ, formats.TAR)),
    "7za": ("7zip", (formats.ZIP, formats.APK, formats.RAR, formats.SEVENZ, formats.TAR)),
    "7zr": ("7zip", (formats.ZIP, formats.RAR, formats.SEVENZ)),
    "unrar": ("unrar", (formats.RAR,)),
    "unar": ("unar", (formats.RAR, formats.ZIP, formats.APK, formats.SEVENZ, formats.TAR)),
    "bsdtar": ("bsdtar", (formats.RAR, formats.ZIP, formats.APK, formats.SEVENZ, formats.TAR)),
    "lsar": ("lsar", (formats.RAR,)),
}


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _binary_version(path: str) -> str:
    try:
        proc = subprocess.run(
            [path, "-h"], capture_output=True, text=True, timeout=10, errors="replace"
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"\b(\d+\.\d+)\b", text)
    return match.group(1) if match else ""


def discover_engines(refresh: bool = False) -> List[Engine]:
    """Return every extraction backend that is currently usable."""
    if refresh:
        _engine_cache.clear()

    cached = _engine_cache.get("engines")
    if cached is not None:
        return cached

    engines: List[Engine] = []

    for binary, (name, kinds) in _BINARY_CANDIDATES.items():
        path = _which(binary)
        if path:
            engines.append(
                Engine(
                    name=name,
                    kind="binary",
                    kinds=kinds,
                    location=path,
                    version=_binary_version(path) if name != "lsar" else "",
                )
            )

    try:  # pure Python RAR parser (needs a helper binary to decompress)
        import rarfile  # type: ignore

        engines.append(
            Engine(
                name="rarfile",
                kind="library",
                kinds=(formats.RAR,),
                location=f"python module {getattr(rarfile, '__version__', '?')}",
                version=str(getattr(rarfile, "__version__", "")),
            )
        )
    except Exception:
        pass

    try:  # in-process RAR decoder, no external binary required
        import unrar.cffi  # type: ignore  # noqa: F401

        engines.append(
            Engine(
                name="unrar-cffi",
                kind="library",
                kinds=(formats.RAR,),
                location="python module unrar.cffi",
            )
        )
    except Exception:
        pass

    try:  # pure Python 7z reader
        import py7zr  # type: ignore

        engines.append(
            Engine(
                name="py7zr",
                kind="library",
                kinds=(formats.SEVENZ,),
                location=f"python module py7zr {getattr(py7zr, '__version__', '')}",
            )
        )
    except Exception:
        pass

    # WebAssembly libarchive driven by Node - handles RAR v4/v5 with no
    # native binary at all (npm install libarchive.js).
    node_engine = _node_libarchive_engine()
    if node_engine is not None:
        engines.append(node_engine)

    # The standard library is always available.
    engines.append(
        Engine(
            name="zipfile",
            kind="library",
            kinds=(formats.ZIP, formats.APK),
            location="python stdlib",
            version="builtin",
        )
    )
    engines.append(
        Engine(
            name="tarfile",
            kind="library",
            kinds=(formats.TAR,),
            location="python stdlib",
            version="builtin",
        )
    )
    engines.append(
        Engine(
            name="compress",
            kind="library",
            kinds=(formats.GZIP, formats.BZ2, formats.XZ),
            location="python stdlib",
            version="builtin",
        )
    )

    _engine_cache["engines"] = engines
    return engines


_engine_cache: Dict[str, object] = {}


def _node_libarchive_engine() -> Optional[Engine]:
    """Return the WASM libarchive engine when Node and the module are present."""
    node = _which("node")
    if not node:
        return None

    script = Path(__file__).parent / "node" / "libarchive_worker.mjs"
    if not script.is_file():
        return None

    probe = subprocess.run(
        [node, str(script), "--check"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # The worker exits with code 2 for a missing module and prints a hint.
    if "libarchive.js not found" in (probe.stderr or ""):
        return None

    return Engine(
        name="libarchive",
        kind="node",
        kinds=(formats.RAR, formats.SEVENZ, formats.ZIP, formats.APK, formats.TAR),
        location=f"node {node}",
        script=str(script),
    )


#: Preferred engine order per archive type (first match wins).
_PRIORITY = {
    formats.RAR: ("7zip", "unrar", "unar", "bsdtar", "libarchive", "unrar-cffi", "rarfile"),
    formats.SEVENZ: ("7zip", "py7zr", "libarchive", "bsdtar", "unar"),
    formats.ZIP: ("zipfile", "7zip", "libarchive", "bsdtar", "unar"),
    formats.APK: ("zipfile", "7zip", "libarchive", "bsdtar", "unar"),
    formats.TAR: ("tarfile", "7zip", "libarchive", "bsdtar", "unar"),
    formats.GZIP: ("compress", "7zip", "bsdtar", "unar", "libarchive"),
    formats.BZ2: ("compress", "7zip", "bsdtar", "unar", "libarchive"),
    formats.XZ: ("compress", "7zip", "bsdtar", "unar", "libarchive"),
}

_DEFAULT_ORDER = (
    "7zip", "unrar", "unar", "bsdtar", "libarchive",
    "unrar-cffi", "rarfile", "py7zr", "zipfile", "tarfile", "compress",
)


def engines_for(kind: str) -> List[Engine]:
    """Engines able to handle ``kind``, best first."""
    order = _PRIORITY.get(kind, _DEFAULT_ORDER)
    found = [e for e in discover_engines() if kind in e.kinds]
    return sorted(
        found,
        key=lambda e: order.index(e.name) if e.name in order else len(order),
    )


def best_engine(kind: str) -> Optional[Engine]:
    engines = engines_for(kind)
    return engines[0] if engines else None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _notify(
    progress: Optional[ProgressCallback],
    **payload: object,
) -> None:
    if progress is None:
        return
    try:
        progress(payload)
    except Exception:  # never let a broken callback kill an extraction
        pass


def _check_cancel(cancel: Optional[CancelCheck]) -> None:
    if cancel is not None and cancel():
        raise ExtractionCancelled("cancelled by user")


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _safe_link_target(dest: Path, target: Path, link_target: str) -> Optional[str]:
    """Validate a symlink target so it cannot point outside the destination."""
    if not link_target:
        return None
    if os.path.isabs(link_target) or link_target.startswith("~"):
        return None
    resolved = (target.parent / link_target).resolve()
    if not _is_within(dest, resolved):
        return None
    return link_target


def sanitize_member_path(name: str) -> Optional[str]:
    """Return a safe relative path for an archive member.

    ``None`` means the member must be skipped (absolute path, drive letter or
    a ``..`` escape attempt).
    """
    if not name:
        return None
    normalised = name.replace("\\", "/")
    # Strip a Windows drive letter such as ``C:``.
    if re.match(r"^[A-Za-z]:", normalised):
        return None
    parts: List[str] = []
    for part in normalised.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            # A member that tries to climb out of the archive is refused
            # outright rather than silently rewritten.
            return None
        parts.append(part)
    if not parts:
        return None
    return "/".join(parts)


# --------------------------------------------------------------------------
# listing
# --------------------------------------------------------------------------


def list_entries(path: str, kind: Optional[str] = None) -> List[Entry]:
    """List the members of an archive without extracting it."""
    kind = kind or formats.detect(path)
    engine = best_engine(kind)
    if engine is None:
        raise ExtractionError(f"no engine available for {formats.describe(kind)}")

    if engine.name == "7zip":
        return _list_7zip(engine.location, path)
    if engine.name == "zipfile":
        return _list_zipfile(path)
    if engine.name == "tarfile":
        return _list_tarfile(path)
    if engine.name == "rarfile":
        return _list_rarfile(path)
    if engine.name == "unrar-cffi":
        return _list_unrar_cffi(path)
    if engine.name == "unar":
        return _list_unar(engine.location, path)
    if engine.name == "unrar":
        return _list_unrar(engine.location, path)
    if engine.name == "bsdtar":
        return _list_bsdtar(engine.location, path)
    if engine.name == "py7zr":
        return _list_py7zr(path)
    if engine.name == "compress":
        return _list_compress(path)
    if engine.name == "libarchive":
        return _list_libarchive(engine, path)
    raise ExtractionError(f"listing not supported by {engine.name}")


def _list_7zip(binary: str, path: str) -> List[Entry]:
    proc = subprocess.run(
        [binary, "l", "-slt", "-y", path],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        raise ExtractionError(proc.stderr.strip() or "7-Zip could not read the archive")

    entries: List[Entry] = []
    current: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            if "Path" in current:
                entries.append(_entry_from_fields(current))
            current = {}
            continue
        if " = " in line:
            key, _, value = line.partition(" = ")
            current[key.strip()] = value.strip()
    if "Path" in current:
        entries.append(_entry_from_fields(current))
    return entries


def _entry_from_fields(fields: Dict[str, str]) -> Entry:
    attributes = fields.get("Attributes", "")
    is_dir = "Folder" in fields and fields.get("Folder") == "+"
    if attributes.startswith("D"):
        is_dir = True
    size = _to_int(fields.get("Size"))
    packed = _to_int(fields.get("Packed Size"))
    if is_dir:
        size = 0
    return Entry(
        path=fields.get("Path", ""),
        size=size,
        packed_size=packed,
        is_dir=is_dir,
        crc=fields.get("CRC", ""),
    )


def _to_int(value: Optional[str]) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _list_zipfile(path: str) -> List[Entry]:
    entries: List[Entry] = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            entries.append(
                Entry(
                    path=info.filename,
                    size=info.file_size,
                    packed_size=info.compress_size,
                    is_dir=info.is_dir(),
                    crc=f"{info.CRC:08X}",
                )
            )
    return entries


def _list_tarfile(path: str) -> List[Entry]:
    entries: List[Entry] = []
    with tarfile.open(path) as archive:
        for info in archive.getmembers():
            entries.append(
                Entry(
                    path=info.name,
                    size=info.size if info.isfile() else 0,
                    is_dir=info.isdir(),
                )
            )
    return entries


def _list_rarfile(path: str) -> List[Entry]:
    import rarfile  # type: ignore

    with rarfile.RarFile(path) as archive:
        return [
            Entry(
                path=info.filename,
                size=info.file_size,
                packed_size=info.compress_size,
                is_dir=info.isdir(),
                crc=f"{info.CRC:08X}" if info.CRC else "",
            )
            for info in archive.infolist()
        ]


def _list_unrar_cffi(path: str) -> List[Entry]:
    from unrar.cffi import rarfile as cffi_rarfile  # type: ignore

    archive = cffi_rarfile.RarFile(path)
    entries: List[Entry] = []
    for info in archive.infolist():
        is_dir = info.is_dir() if callable(info.is_dir) else bool(info.is_dir)
        entries.append(
            Entry(
                path=info.filename,
                size=info.file_size,
                packed_size=info.compress_size,
                is_dir=is_dir,
            )
        )
    return entries


def _list_unar(binary: str, path: str) -> List[Entry]:
    proc = subprocess.run(
        [binary, "-l", "-q", path], capture_output=True, text=True, errors="replace", timeout=600
    )
    lines = (proc.stdout or "").splitlines()
    entries: List[Entry] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            entries.append(Entry(path=parts[1].strip()))
    return entries


def _list_unrar(binary: str, path: str) -> List[Entry]:
    proc = subprocess.run(
        [binary, "lb", path], capture_output=True, text=True, errors="replace", timeout=600
    )
    if proc.returncode != 0:
        raise ExtractionError(proc.stderr.strip() or "unrar could not read the archive")
    return [Entry(path=line.strip()) for line in proc.stdout.splitlines() if line.strip()]


def _list_bsdtar(binary: str, path: str) -> List[Entry]:
    proc = subprocess.run(
        [binary, "-tf", path], capture_output=True, text=True, errors="replace", timeout=600
    )
    if proc.returncode != 0:
        raise ExtractionError(proc.stderr.strip() or "bsdtar could not read the archive")
    return [Entry(path=line.strip()) for line in proc.stdout.splitlines() if line.strip()]


def _list_py7zr(path: str) -> List[Entry]:
    import py7zr  # type: ignore

    entries: List[Entry] = []
    with py7zr.SevenZipFile(path, "r") as archive:
        for info in archive.list():
            entries.append(
                Entry(
                    path=info.filename,
                    size=info.uncompressed if info.uncompressed is not None else 0,
                    is_dir=bool(info.is_directory),
                )
            )
    return entries


def _list_compress(path: str) -> List[Entry]:
    """A gzip / bzip2 / xz file holds exactly one member."""
    name = os.path.basename(path)
    for suffix in (".gz", ".bz2", ".xz"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return [Entry(path=name, size=os.path.getsize(path), is_dir=False)]


def _list_libarchive(engine: Engine, path: str) -> List[Entry]:
    """List an archive through the WebAssembly libarchive build."""
    import json

    proc = subprocess.run(
        ["node", engine.script, "list", path],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=3600,
    )
    if proc.returncode != 0:
        raise ExtractionError((proc.stderr or "").strip() or "libarchive could not read the archive")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise ExtractionError(f"unexpected libarchive output: {exc}") from exc
    return [
        Entry(
            path=item.get("path", ""),
            size=int(item.get("size", 0) or 0),
            is_dir=bool(item.get("is_dir")),
        )
        for item in payload.get("entries", [])
    ]


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------


def extract(
    path: str,
    destination: str,
    kind: Optional[str] = None,
    progress: Optional[ProgressCallback] = None,
    cancel: Optional[CancelCheck] = None,
) -> ExtractResult:
    """Extract ``path`` into ``destination`` using the best available engine."""
    path = os.path.abspath(path)
    kind = kind or formats.detect(path)
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)

    engine = best_engine(kind)
    if engine is None:
        raise ExtractionError(
            f"No extraction engine available for {formats.describe(kind)}.\n"
            "Install 7-Zip (7z), unrar or unar, or run: pip install unrar-cffi"
        )

    _notify(progress, phase="starting", engine=engine.name, kind=kind)

    handler = {
        "7zip": _extract_7zip,
        "unar": _extract_unar,
        "unrar": _extract_unrar,
        "bsdtar": _extract_bsdtar,
        "rarfile": _extract_rarfile,
        "unrar-cffi": _extract_unrar_cffi,
        "py7zr": _extract_py7zr,
        "zipfile": _extract_zipfile,
        "tarfile": _extract_tarfile,
        "compress": _extract_compress,
        "libarchive": _extract_libarchive,
    }.get(engine.name)

    if handler is None:
        raise ExtractionError(f"extraction not supported by engine {engine.name}")

    result = handler(engine, path, dest, progress, cancel)
    result.engine = engine.name
    result.destination = str(dest)
    _annotate_result(result, dest)
    _notify(progress, phase="done", **result.to_dict())
    return result


def _annotate_result(result: ExtractResult, dest: Path) -> None:
    """Fill in file/dir counts and byte totals by walking the output tree."""
    files = 0
    dirs = 0
    total = 0
    for root, dirnames, filenames in os.walk(dest):
        dirs += len(dirnames)
        for name in filenames:
            files += 1
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    result.files = files
    result.dirs = dirs
    result.bytes_written = total
    if not result.entries:
        result.entries = files + dirs


_PROGRESS_RE = re.compile(r"^\s*(\d{1,3})%(?:\s+(\d+))?\s*[-–]?\s*(.*)$")


def _run_streaming(
    command: Sequence[str],
    progress: Optional[ProgressCallback],
    cancel: Optional[CancelCheck],
    total_bytes: int = 0,
    total_files: int = 0,
) -> int:
    """Run a command, parsing 7-Zip style progress lines from the output."""
    _notify(progress, phase="extracting", percent=0, bytes_done=0, bytes_total=total_bytes)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )

    percent = 0
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if cancel is not None and cancel():
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise ExtractionCancelled("cancelled by user")
            match = _PROGRESS_RE.match(line)
            if match:
                percent = min(100, int(match.group(1)))
                _notify(
                    progress,
                    phase="extracting",
                    percent=percent,
                    current=match.group(3).strip(),
                    bytes_done=int(total_bytes * percent / 100) if total_bytes else 0,
                    bytes_total=total_bytes,
                    files_total=total_files,
                )
    finally:
        if process.stdout is not None:
            process.stdout.close()

    code = process.wait()
    if code != 0:
        raise ExtractionError(f"extraction failed (exit code {code})")
    return code


def _extract_7zip(
    engine: Engine, path: str, dest: Path, progress, cancel
) -> ExtractResult:
    out_dir = str(dest)
    command = [engine.location, "x", "-y", f"-o{out_dir}", path]
    _run_streaming(command, progress, cancel)
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_unar(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    command = [engine.location, "-quiet", "-no-directory", "-o", str(dest), path]
    _run_streaming(command, progress, cancel)
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_unrar(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    command = [engine.location, "x", "-y", "-o+", path, str(dest) + os.sep]
    _run_streaming(command, progress, cancel)
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_bsdtar(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    command = [engine.location, "-x", "-f", path, "-C", str(dest)]
    _run_streaming(command, progress, cancel)
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_rarfile(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    import rarfile  # type: ignore

    with rarfile.RarFile(path) as archive:
        members = archive.infolist()
        total = sum(m.file_size for m in members)
        done = 0
        for index, member in enumerate(members, start=1):
            _check_cancel(cancel)
            archive.extract(member, path=str(dest))
            done += member.file_size
            _notify(
                progress,
                phase="extracting",
                current=member.filename,
                bytes_done=done,
                bytes_total=total,
                files_done=index,
                files_total=len(members),
            )
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_unrar_cffi(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    """Extract with the in-process unrar decoder (no external binary).

    The library exposes one member at a time through ``RarFile.open()``.
    """
    from unrar.cffi import rarfile as cffi_rarfile  # type: ignore

    archive = cffi_rarfile.RarFile(path)
    infos = archive.infolist()
    total = sum(info.file_size for info in infos)
    done = 0

    for index, info in enumerate(infos, start=1):
        _check_cancel(cancel)
        is_dir = info.is_dir() if callable(info.is_dir) else bool(info.is_dir)
        safe = sanitize_member_path(info.filename)
        if safe is None:
            continue
        target = dest / safe
        if is_dir:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        stream = archive.open(info.filename)
        data = stream.read()
        with open(target, "wb") as sink:
            sink.write(data)
        done += info.file_size
        _notify(
            progress,
            phase="extracting",
            current=info.filename,
            bytes_done=done,
            bytes_total=total,
            files_done=index,
            files_total=len(infos),
        )
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_libarchive(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    """Extract through the WebAssembly libarchive build running under Node."""
    import json

    command = ["node", engine.script, "extract", path, str(dest)]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    )
    assert process.stderr is not None
    for line in process.stderr:
        if cancel is not None and cancel():
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise ExtractionCancelled("cancelled by user")
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        update = payload.get("progress")
        if isinstance(update, dict):
            _notify(progress, phase="extracting", **update)

    stdout = process.stdout.read() if process.stdout else ""
    process.stdout.close() if process.stdout else None
    code = process.wait()
    if code != 0:
        raise ExtractionError(f"libarchive extraction failed (exit code {code})")
    try:
        payload = json.loads(stdout or "{}")
    except ValueError:
        payload = {}
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_py7zr(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    import py7zr  # type: ignore

    _notify(progress, phase="extracting", percent=0)
    with py7zr.SevenZipFile(path, "r") as archive:
        archive.extractall(path=str(dest))
    _notify(progress, phase="extracting", percent=100)
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_zipfile(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    """Extract a ZIP/APK with the standard library, safely and incrementally."""
    warnings: List[str] = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        total = sum(i.file_size for i in infos)
        done = 0
        for index, info in enumerate(infos, start=1):
            _check_cancel(cancel)
            safe = sanitize_member_path(info.filename)
            if safe is None:
                warnings.append(f"skipped unsafe path: {info.filename}")
                continue
            target = dest / safe
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if _is_symlink(info):
                link_target = archive.read(info).decode("utf-8", "replace")
                safe_link = _safe_link_target(dest, target, link_target)
                if safe_link is None:
                    warnings.append(f"skipped symlink pointing outside the archive: {safe}")
                    continue
                if target.exists() or target.is_symlink():
                    target.unlink()
                try:
                    os.symlink(safe_link, target)
                except (OSError, NotImplementedError) as exc:
                    warnings.append(f"could not create symlink {safe}: {exc}")
                continue
            if not _is_within(dest, target.parent):
                warnings.append(f"skipped {safe}: path escapes the destination")
                continue
            with archive.open(info) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            done += info.file_size
            _notify(
                progress,
                phase="extracting",
                current=info.filename,
                bytes_done=done,
                bytes_total=total,
                files_done=index,
                files_total=len(infos),
            )
    return ExtractResult(destination=str(dest), engine=engine.name, warnings=warnings)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    if info.create_system != 3:  # not unix
        return False
    mode = info.external_attr >> 16
    return bool(mode) and stat.S_ISLNK(mode)


def _extract_compress(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    """Decompress a single-file gzip / bzip2 / xz stream."""
    import bz2
    import gzip
    import lzma

    lower = path.lower()
    if lower.endswith(".gz"):
        opener, suffix = gzip.open, ".gz"
    elif lower.endswith(".bz2"):
        opener, suffix = bz2.open, ".bz2"
    elif lower.endswith(".xz"):
        opener, suffix = lzma.open, ".xz"
    else:
        raise ExtractionError("unsupported compression format")

    name = os.path.basename(path)
    if name.lower().endswith(suffix):
        name = name[: -len(suffix)]
    target = dest / (name or "output")

    _notify(progress, phase="extracting", percent=0, current=target.name)
    with opener(path, "rb") as source, open(target, "wb") as sink:
        while True:
            _check_cancel(cancel)
            block = source.read(1024 * 1024)
            if not block:
                break
            sink.write(block)
    _notify(progress, phase="extracting", percent=100, current=target.name)
    return ExtractResult(destination=str(dest), engine=engine.name)


def _extract_tarfile(engine: Engine, path: str, dest: Path, progress, cancel) -> ExtractResult:
    """Extract a TAR with the standard library, safely and incrementally."""
    warnings: List[str] = []
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        total = sum(m.size for m in members)
        done = 0
        for index, member in enumerate(members, start=1):
            _check_cancel(cancel)
            safe = sanitize_member_path(member.name)
            if safe is None:
                warnings.append(f"skipped unsafe path: {member.name}")
                continue
            target = dest / safe
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym() or member.islnk():
                link_target = member.linkname
                safe_link = _safe_link_target(dest, target, link_target)
                if safe_link is None:
                    warnings.append(f"skipped link pointing outside the archive: {safe}")
                    continue
                if target.exists() or target.is_symlink():
                    target.unlink()
                try:
                    os.symlink(safe_link, target)
                except (OSError, NotImplementedError) as exc:
                    warnings.append(f"could not create symlink {safe}: {exc}")
                continue
            if not member.isfile():
                warnings.append(f"skipped special file: {member.name}")
                continue
            if not _is_within(dest, target.parent):
                warnings.append(f"skipped {safe}: path escapes the destination")
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            done += member.size
            _notify(
                progress,
                phase="extracting",
                current=member.name,
                bytes_done=done,
                bytes_total=total,
                files_done=index,
                files_total=len(members),
            )
    return ExtractResult(destination=str(dest), engine=engine.name, warnings=warnings)


# --------------------------------------------------------------------------
# output tree
# --------------------------------------------------------------------------


def build_tree(root: str, relative_to: str = "") -> List[Dict[str, object]]:
    """Build a nested tree of the extracted output for the browser UI."""
    base = Path(root)
    if not base.is_dir():
        return []

    def walk(directory: Path) -> List[Dict[str, object]]:
        items: List[Dict[str, object]] = []
        try:
            children = sorted(
                directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except OSError:
            return items
        for child in children:
            try:
                stat_result = child.stat()
            except OSError:
                continue
            is_dir = child.is_dir()
            node: Dict[str, object] = {
                "name": child.name,
                "path": str(child),
                "rel": str(child.relative_to(base)),
                "is_dir": is_dir,
                "size": 0 if is_dir else stat_result.st_size,
                "mtime": stat_result.st_mtime,
            }
            if is_dir:
                node["children"] = walk(child)
            items.append(node)
        return items

    return walk(base)
