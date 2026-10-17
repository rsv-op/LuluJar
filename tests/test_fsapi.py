"""Filesystem sandboxing rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from lulujar import fsapi


def test_resolve_allows_paths_inside_a_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    target = root / "sub" / "file.txt"
    target.write_text("x", encoding="utf-8")
    resolved = fsapi.resolve(str(target), [root])
    assert resolved == target.resolve()


def test_resolve_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(fsapi.PathNotAllowed):
        fsapi.resolve(str(outside), [root])


def test_resolve_blocks_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(fsapi.PathNotAllowed):
        fsapi.resolve(str(root / ".." / "etc"), [root])


def test_list_directory_sorts_folders_first(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "folder").mkdir(parents=True)
    (root / "file.txt").write_text("x", encoding="utf-8")
    entries = fsapi.list_directory(str(root), [root])
    assert entries[0].is_dir is True
    assert entries[1].name == "file.txt"


def test_list_directory_hides_dotfiles_by_default(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".hidden").write_text("x", encoding="utf-8")
    (root / "shown.txt").write_text("x", encoding="utf-8")
    names = [e.name for e in fsapi.list_directory(str(root), [root])]
    assert ".hidden" not in names
    names = [e.name for e in fsapi.list_directory(str(root), [root], show_hidden=True)]
    assert ".hidden" in names


def test_read_preview_of_text_file(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("hello", encoding="utf-8")
    preview = fsapi.read_preview(str(target), [root])
    assert preview["binary"] is False
    assert preview["content"] == "hello"


def test_read_preview_detects_binary(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "blob.bin"
    target.write_bytes(b"\x00\x01\x02")
    preview = fsapi.read_preview(str(target), [root])
    assert preview["binary"] is True


def test_remove_tree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "file.txt").write_text("12345", encoding="utf-8")
    freed = fsapi.remove_tree(str(root), [root])
    assert freed == 5
    assert not root.exists()


def test_breadcrumbs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a" / "b").mkdir(parents=True)
    crumbs = fsapi.breadcrumbs(str(root / "a" / "b"), [root])
    assert [c["name"] for c in crumbs] == ["root", "a", "b"]


def test_sha256(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.bin"
    target.write_bytes(b"abc")
    digest = fsapi.sha256(str(target), [root])
    assert digest == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
