from __future__ import annotations

import gzip
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from organizer.backup import (
    _extract_metadata,
    backup_guild,
    cleanup_backups,
    list_backups,
    load_backup,
)
from organizer.models import BackupMetadata


def test_backup_guild_creates_json(temp_vault: Path) -> None:
    client = MagicMock()
    guild = MagicMock()
    guild.id = 123456789
    guild.name = "Test Guild"
    client.get_guild.return_value = guild

    cat1 = MagicMock()
    cat1.id = 1
    cat1.name = "📁 Category 1"
    cat1.position = 0
    cat1.text_channels = []

    guild.categories = [cat1]

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        backup_path = backup_guild(client, 123456789, out_dir)

        assert backup_path.exists()
        assert backup_path.suffix == ".json"

        with backup_path.open() as f:
            data = json.load(f)

        assert data["guild_id"] == 123456789
        assert data["guild_name"] == "Test Guild"
        assert len(data["categories"]) == 1


def test_backup_guild_compressed(temp_vault: Path) -> None:
    import gzip
    import json
    import tempfile

    client = MagicMock()
    guild = MagicMock()
    guild.id = 123456789
    guild.name = "Test Guild"
    client.get_guild.return_value = guild

    cat1 = MagicMock()
    cat1.id = 1
    cat1.name = "📁 Category 1"
    cat1.position = 0
    cat1.text_channels = []

    guild.categories = [cat1]

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        backup_path = backup_guild(client, 123456789, out_dir, compress=True)

        assert backup_path.exists()
        assert backup_path.name.endswith(".json.gz")

        with gzip.open(backup_path, "rt", encoding="utf-8") as f:
            data = json.load(f)

        assert data["guild_id"] == 123456789


def test_load_backup_json(temp_vault: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        backup_file = Path(tmp) / "backup.json"
        test_data = {
            "guild_id": 123,
            "guild_name": "Test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "categories": [],
        }
        with backup_file.open("w") as f:
            json.dump(test_data, f)

        loaded = load_backup(backup_file)
        assert loaded["guild_id"] == 123


def test_load_backup_compressed(temp_vault: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        backup_file = Path(tmp) / "backup.json.gz"
        test_data = {
            "guild_id": 123,
            "guild_name": "Test",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "categories": [],
        }
        with gzip.open(backup_file, "wt", encoding="utf-8") as f:
            json.dump(test_data, f)

        loaded = load_backup(backup_file)
        assert loaded["guild_id"] == 123


def test_list_backups(temp_vault: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        for i in range(3):
            backup_file = out_dir / f"guild_123_2024010{i}_000000.json"
            backup_file.write_text(
                '{"guild_id": 123, "guild_name": "Test", '
                '"timestamp": "2024-01-01T00:00:00+00:00", "categories": []}'
            )

        backups = list_backups(out_dir, 123)
        assert len(backups) == 3
        assert all(isinstance(b, BackupMetadata) for b in backups)


def test_cleanup_backups(temp_vault: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        for i in range(5):
            backup_file = out_dir / f"guild_123_2024010{i}_000000.json"
            backup_file.write_text(
                r'{"guild_id": 123, "guild_name": "Test", '
                r'"timestamp": "2024-01-01T00:00:00+00:00", "categories": []}'
            )

        deleted = cleanup_backups(out_dir, max_backups=2, retention_days=30, dry_run=True)
        assert len(deleted) == 3


def test_cleanup_backups_by_age(temp_vault: Path) -> None:
    from datetime import timedelta

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        old_date = (datetime.now(UTC) - timedelta(days=60)).strftime("%Y%m%d")

        old_backup = out_dir / f"guild_123_{old_date}_000000.json"
        old_backup.write_text(
            '{"guild_id": 123, "guild_name": "Test", '
            '"timestamp": "2024-01-01T00:00:00+00:00", "categories": []}'
        )
        old_time = (datetime.now(UTC) - timedelta(days=60)).timestamp()
        os.utime(old_backup, (old_backup.stat().st_atime, old_time))

        now = datetime.now(UTC)
        new_backup = out_dir / f"guild_123_{now.strftime('%Y%m%d')}_000000.json"
        new_backup.write_text(
            '{"guild_id": 123, "guild_name": "Test", '
            '"timestamp": "' + now.isoformat() + '", "categories": []}'
        )

        deleted = cleanup_backups(out_dir, max_backups=10, retention_days=30, dry_run=True)
        assert len(deleted) == 1
        assert deleted[0].name == old_backup.name


def test_extract_metadata(temp_vault: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        backup_file = Path(tmp) / "guild_123_20240101_000000.json"
        test_data = {
            "guild_id": 123,
            "guild_name": "Test Guild",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "categories": [
                {"id": 1, "name": "Cat1", "channels": [{"id": 10, "name": "chan1"}]},
                {"id": 2, "name": "Cat2", "channels": [
                    {"id": 20, "name": "chan2"},
                    {"id": 30, "name": "chan3"},
                ]},
            ],
        }
        with backup_file.open("w") as f:
            json.dump(test_data, f)

        meta = _extract_metadata(backup_file, compressed=False)
        assert meta.guild_id == 123
        assert meta.guild_name == "Test Guild"
        assert meta.categories_count == 2
        assert meta.channels_count == 3