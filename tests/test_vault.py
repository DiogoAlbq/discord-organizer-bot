from __future__ import annotations

from pathlib import Path

from organizer.vault import calculate_stats, filter_vault, find_node, read_vault


def test_read_vault_basic_structure(sample_vault_path: Path, mock_config) -> None:
    vault = read_vault(sample_vault_path, mock_config.vault.ignore_patterns)

    assert vault.name == "sample_vault"
    assert len(vault.children) == 4

    child_names = {c.name for c in vault.children}
    assert child_names == {"concepts", "daily", "projects", "reports"}

    daily = next(c for c in vault.children if c.name == "daily")
    assert len(daily.children) == 2
    daily_child_names = {c.name for c in daily.children}
    assert daily_child_names == {"2026-06-16", "2026-06-17"}

    concepts = next(c for c in vault.children if c.name == "concepts")
    assert concepts.is_leaf


def test_read_vault_ignores_dotfiles(sample_vault_path: Path, mock_config) -> None:
    (sample_vault_path / ".hidden").mkdir(exist_ok=True)
    (sample_vault_path / ".hidden" / "file.txt").write_text("secret")

    vault = read_vault(sample_vault_path, mock_config.vault.ignore_patterns)
    child_names = {c.name for c in vault.children}
    assert ".hidden" not in child_names


def test_read_vault_ignores_patterns(sample_vault_path: Path, mock_config) -> None:
    (sample_vault_path / "venv").mkdir(exist_ok=True)
    (sample_vault_path / "venv" / "lib").mkdir(exist_ok=True)

    vault = read_vault(sample_vault_path, mock_config.vault.ignore_patterns)
    child_names = {c.name for c in vault.children}
    assert "venv" not in child_names


def test_read_vault_ignores_wildcard_patterns(temp_vault: Path, mock_config) -> None:
    (temp_vault / "node_modules").mkdir(exist_ok=True)
    (temp_vault / "node_modules" / "pkg").mkdir(exist_ok=True)
    (temp_vault / "build.tmp").mkdir(exist_ok=True)

    vault = read_vault(temp_vault, mock_config.vault.ignore_patterns)
    child_names = {c.name for c in vault.children}
    assert "node_modules" not in child_names
    assert "build.tmp" not in child_names


def test_read_vault_with_files(temp_vault: Path, mock_config) -> None:
    (temp_vault / "notes.txt").write_text("content")
    docs_dir = temp_vault / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "guide.md").write_text("# Guide")

    vault = read_vault(temp_vault, mock_config.vault.ignore_patterns, include_files=True)
    file_names = {c.name for c in vault.children if c.is_file}
    assert "notes.txt" in file_names

    docs = next(c for c in vault.children if c.name == "docs")
    guide = next(c for c in docs.children if c.name == "guide.md")
    assert guide.is_file
    assert guide.size > 0


def test_read_vault_max_depth(temp_vault: Path, mock_config) -> None:
    (temp_vault / "a" / "b" / "c" / "d").mkdir(parents=True)

    vault = read_vault(temp_vault, mock_config.vault.ignore_patterns, max_depth=2)
    a = next(c for c in vault.children if c.name == "a")
    b = next(c for c in a.children if c.name == "b")
    assert "c" not in {c.name for c in b.children}


def test_read_vault_calculate_stats(sample_vault_path: Path, mock_config) -> None:
    vault = read_vault(sample_vault_path, mock_config.vault.ignore_patterns, include_files=True)
    total_dirs, total_files, total_size = calculate_stats(vault)

    assert total_dirs == 5
    assert total_files == 4
    assert total_size > 0


def test_find_node(sample_vault_path: Path, mock_config) -> None:
    vault = read_vault(sample_vault_path, mock_config.vault.ignore_patterns)
    daily = find_node(vault, str(sample_vault_path / "daily"))
    assert daily is not None
    assert daily.name == "daily"

    not_found = find_node(vault, "/nonexistent")
    assert not_found is None


def test_filter_vault(sample_vault_path: Path, mock_config) -> None:
    vault = read_vault(sample_vault_path, mock_config.vault.ignore_patterns)

    filtered = filter_vault(vault, lambda n: n.name.startswith("d"))
    assert filtered is not None
    assert filtered.name == "daily"
    assert len(filtered.children) == 2

    filtered_none = filter_vault(vault, lambda n: n.name == "nonexistent")
    assert filtered_none is None


def test_read_vault_symlinks(temp_vault: Path, mock_config) -> None:
    target = temp_vault / "target_dir"
    target.mkdir()
    (target / "file.txt").write_text("content")
    link = temp_vault / "link_dir"
    link.symlink_to(target)

    vault = read_vault(temp_vault, mock_config.vault.ignore_patterns, follow_symlinks=True)
    link_node = next((c for c in vault.children if c.name == "link_dir"), None)
    assert link_node is not None
    assert link_node.is_symlink