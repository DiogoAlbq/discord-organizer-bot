from __future__ import annotations

import fnmatch
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import VaultNode


def _should_ignore(name: str, ignore_patterns: list[str]) -> bool:
    for pattern in ignore_patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
    return False


def _read_gitignore(gitignore_path: Path) -> list[str]:
    patterns = []
    try:
        with open(gitignore_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)
    except (OSError, UnicodeDecodeError):
        pass
    return patterns


def _get_file_info(entry: Path, follow_symlinks: bool = False) -> dict[str, Any]:
    try:
        stat_result = entry.stat(follow_symlinks=follow_symlinks)
        is_symlink = entry.is_symlink()
        return {
            "size": stat_result.st_size if not is_symlink else 0,
            "modified": stat_result.st_mtime,
            "mode": stat_result.st_mode,
            "is_symlink": is_symlink,
            "symlink_target": os.readlink(entry) if is_symlink else None,
        }
    except (OSError, ValueError):
        return {
            "size": 0,
            "modified": None,
            "mode": 0,
            "is_symlink": False,
            "symlink_target": None,
        }


def _has_relevant_content(
    dirpath: Path,
    ignore_patterns: list[str],
    follow_symlinks: bool = False,
) -> bool:
    try:
        for entry in dirpath.iterdir():
            if entry.name.startswith("."):
                continue
            if _should_ignore(entry.name, ignore_patterns):
                continue
            is_dir = entry.is_dir()
            if follow_symlinks and entry.is_symlink():
                try:
                    is_dir = entry.resolve().is_dir()
                except (OSError, RuntimeError):
                    is_dir = False
            if is_dir:
                if _has_relevant_content(entry, ignore_patterns, follow_symlinks):
                    return True
            else:
                return True
    except (PermissionError, OSError):
        pass
    return False


def read_vault(
    path: Path,
    ignore_patterns: list[str] | None = None,
    follow_symlinks: bool = False,
    include_files: bool = False,
    max_depth: int | None = None,
    gitignore_path: Path | None = None,
) -> VaultNode:
    """Read directory tree recursively into VaultNode structure.

    Args:
        path: Root path to scan
        ignore_patterns: Glob patterns to ignore (e.g., ["venv", "*.tmp", "__pycache__"])
        follow_symlinks: Whether to follow symbolic links
        include_files: Whether to include files in the tree (not just directories)
        max_depth: Maximum recursion depth (None = unlimited)
        gitignore_path: Path to .gitignore file for additional patterns

    Returns:
        VaultNode root with complete directory tree
    """
    if ignore_patterns is None:
        ignore_patterns = []

    if gitignore_path and gitignore_path.exists():
        ignore_patterns = ignore_patterns + _read_gitignore(gitignore_path)

    root_path = path.resolve()

    def build_node(
        current_path: Path,
        current_depth: int = 0,
    ) -> VaultNode:
        if max_depth is not None and current_depth > max_depth:
            return VaultNode(name=current_path.name, path=str(current_path))

        node_name = current_path.name
        file_info = _get_file_info(current_path, follow_symlinks)

        node = VaultNode(
            name=node_name,
            path=str(current_path),
            is_file=False,
            size=file_info["size"],
            modified=(
                datetime.fromtimestamp(file_info["modified"])
                if file_info["modified"]
                else None
            ),
            is_symlink=file_info["is_symlink"],
            symlink_target=file_info["symlink_target"],
        )

        try:
            entries = sorted(current_path.iterdir(), key=lambda e: e.name.lower())
        except (PermissionError, OSError):
            return node

        for entry in entries:
            if entry.name.startswith("."):
                continue
            if _should_ignore(entry.name, ignore_patterns):
                continue

            entry_info = _get_file_info(entry, follow_symlinks)

            is_dir = entry.is_dir()
            if follow_symlinks and entry.is_symlink():
                try:
                    is_dir = entry.resolve().is_dir()
                except (OSError, RuntimeError):
                    is_dir = False

            if is_dir:
                if _has_relevant_content(entry, ignore_patterns, follow_symlinks):
                    child = build_node(entry, current_depth + 1)
                    if child.children or _has_relevant_content(entry, ignore_patterns, follow_symlinks):
                        node.children.append(child)
                else:
                    node.children.append(VaultNode(
                        name=entry.name,
                        path=str(entry),
                        is_file=False,
                    ))
            elif include_files:
                node.children.append(VaultNode(
                    name=entry.name,
                    path=str(entry),
                    is_file=True,
                    size=entry_info["size"],
                    modified=(
                        datetime.fromtimestamp(entry_info["modified"])
                        if entry_info["modified"]
                        else None
                    ),
                    is_symlink=entry_info["is_symlink"],
                    symlink_target=entry_info["symlink_target"],
                ))

        return node

    return build_node(root_path)


def calculate_stats(node: VaultNode) -> tuple[int, int, int]:
    """Calculate total dirs, files, and size for a vault tree."""
    total_dirs = 0
    total_files = 0
    total_size = 0

    def walk(n: VaultNode) -> None:
        nonlocal total_dirs, total_files, total_size
        if n.is_file:
            total_files += 1
            total_size += n.size
        else:
            total_dirs += 1
        for child in n.children:
            walk(child)

    walk(node)
    return total_dirs, total_files, total_size


def find_node(root: VaultNode, target_path: str) -> VaultNode | None:
    """Find a node by its absolute path."""
    if root.path == target_path:
        return root
    for child in root.children:
        result = find_node(child, target_path)
        if result:
            return result
    return None


def filter_vault(root: VaultNode, predicate) -> VaultNode:
    """Create a filtered copy of the vault tree."""
    if predicate(root):
        new_root = VaultNode(
            name=root.name,
            path=root.path,
            is_file=root.is_file,
            size=root.size,
            modified=root.modified,
            is_symlink=root.is_symlink,
            symlink_target=root.symlink_target,
        )
        for child in root.children:
            filtered = filter_vault(child, predicate)
            if filtered:
                new_root.children.append(filtered)
        return new_root
    return None