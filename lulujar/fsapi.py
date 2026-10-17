"""Safe filesystem access for the browser UI.

The web app can browse the machine it runs on.  To keep that from turning
into an open door, every path is resolved and checked against a list of
allowed roots before it is read.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

#: Roots that are always browsable.
DEFAULT_ROOTS = ("home", "cwd")


class PathNotAllowed(PermissionError):
    """Raised when a path falls outside the allowed roots."""


@dataclass
class FileEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    hidden: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "mtime": self.mtime,
            "hidden": self.hidden,
        }


def default_roots(extra: Optional[Sequence[str]] = None) -> List[Path]:
    """The list of directories the browser may look into."""
    roots: List[Path] = []

    def add(path: Path) -> None:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        if resolved.is_dir() and resolved not in roots:
            roots.append(resolved)

    for entry in extra or ():
        add(Path(entry))

    add(Path.home())
    add(Path.cwd())
    from . import config

    add(config.output_dir())
    add(config.uploads_dir())
    return roots


def resolve(path: str, roots: Sequence[Path]) -> Path:
    """Resolve ``path`` and make sure it stays inside one of ``roots``."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve()
    except OSError:
        raise PathNotAllowed(f"cannot resolve path: {path}")

    # A path may point at something that does not exist yet (a destination the
    # user is about to create) - check the parent in that case.
    check_path = resolved if resolved.exists() else resolved.parent
    if not check_path.exists():
        raise PathNotAllowed(f"path does not exist: {path}")

    for root in roots:
        try:
            check_path.relative_to(root)
            return resolved
        except ValueError:
            continue

    raise PathNotAllowed(
        f"path is outside the allowed locations: {path}"
    )


def is_within(path: str, roots: Sequence[Path]) -> bool:
    try:
        resolve(path, roots)
        return True
    except (PathNotAllowed, OSError):
        return False


def list_directory(path: str, roots: Sequence[Path], show_hidden: bool = False) -> List[FileEntry]:
    """List a directory, sorted with folders first."""
    target = resolve(path, roots)
    entries: List[FileEntry] = []
    try:
        children = list(target.iterdir())
    except OSError as exc:
        raise PathNotAllowed(str(exc))

    for child in children:
        name = child.name
        if not show_hidden and name.startswith("."):
            continue
        try:
            stat_result = child.stat()
        except OSError:
            continue
        entries.append(
            FileEntry(
                name=name,
                path=str(child),
                is_dir=child.is_dir(),
                size=stat_result.st_size,
                mtime=stat_result.st_mtime,
                hidden=name.startswith("."),
            )
        )

    entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))
    return entries


def breadcrumbs(path: str, roots: Sequence[Path]) -> List[dict]:
    """Ancestors of ``path`` for the breadcrumb bar."""
    target = resolve(path, roots)
    crumbs: List[dict] = []
    for root in roots:
        try:
            target.relative_to(root)
        except ValueError:
            continue
        current = root
        crumbs.append({"name": current.name or str(current), "path": str(current)})
        for part in target.relative_to(root).parts:
            current = current / part
            crumbs.append({"name": part, "path": str(current)})
        break
    else:
        crumbs.append({"name": target.name, "path": str(target)})
    return crumbs


def read_preview(path: str, roots: Sequence[Path], max_bytes: int = 200_000) -> dict:
    """Return a text preview of a file (or a note that it is binary)."""
    target = resolve(path, roots)
    if target.is_dir():
        raise PathNotAllowed("cannot preview a directory")

    with open(target, "rb") as handle:
        chunk = handle.read(max_bytes)

    binary = b"\x00" in chunk
    text = ""
    if not binary:
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            binary = True

    return {
        "path": str(target),
        "name": target.name,
        "size": target.stat().st_size,
        "binary": binary,
        "truncated": target.stat().st_size > len(chunk),
        "content": text,
    }


def sha256(path: str, roots: Sequence[Path], chunk: int = 1024 * 1024) -> str:
    target = resolve(path, roots)
    digest = hashlib.sha256()
    with open(target, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def directory_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def remove_tree(path: str, roots: Sequence[Path]) -> int:
    """Delete a directory tree, returning the number of bytes reclaimed."""
    import shutil

    target = resolve(path, roots)
    if not target.is_dir():
        raise PathNotAllowed("refusing to delete a file")
    size = directory_size(str(target))
    shutil.rmtree(target)
    return size
