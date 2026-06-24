from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import discord
from discord import CategoryChannel, TextChannel

from .models import BackupMetadata

logger = logging.getLogger(__name__)


def _serialize_channel(channel: TextChannel) -> dict[str, Any]:
    return {
        "id": channel.id,
        "name": channel.name,
        "topic": channel.topic,
        "position": channel.position,
        "nsfw": channel.nsfw,
        "slowmode_delay": channel.slowmode_delay,
    }


def _serialize_category(category: CategoryChannel) -> dict[str, Any]:
    channels = [_serialize_channel(chan) for chan in category.text_channels]
    return {
        "id": category.id,
        "name": category.name,
        "position": category.position,
        "channels": channels,
    }


def _deserialize_category(data: dict[str, Any], guild: discord.Guild) -> tuple[str, list[dict]]:
    return data["name"], data.get("channels", [])


def backup_guild(
    client: discord.Client,
    guild_id: int,
    out_dir: Path,
    compress: bool = False,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Serialize guild state to JSON backup file."""
    guild = client.get_guild(guild_id)
    if guild is None:
        raise ValueError(f"Guild {guild_id} not found in client cache")

    timestamp = datetime.now(UTC)
    categories_data = [_serialize_category(cat) for cat in guild.categories]

    total_channels = sum(len(c["channels"]) for c in categories_data)

    backup_data = {
        "version": "1.0",
        "guild_id": guild_id,
        "guild_name": guild.name,
        "timestamp": timestamp.isoformat(),
        "categories": categories_data,
        "metadata": metadata or {},
    }

    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"guild_{guild_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}"
    if compress:
        filename += ".json.gz"
        out_path = out_dir / filename
        json_bytes = json.dumps(backup_data, indent=2, ensure_ascii=False).encode("utf-8")
        with gzip.open(out_path, "wb") as f:
            f.write(json_bytes)
    else:
        filename += ".json"
        out_path = out_dir / filename
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2, ensure_ascii=False)

    size = out_path.stat().st_size
    BackupMetadata(
        guild_id=guild_id,
        guild_name=guild.name,
        timestamp=timestamp,
        categories_count=len(categories_data),
        channels_count=total_channels,
        file_path=str(out_path),
        compressed=compress,
        size_bytes=size,
        metadata=metadata or {},
    )
    logger.info(
        "Backup created: %s (categories=%d, channels=%d, size=%d bytes)",
        out_path,
        len(categories_data),
        total_channels,
        size,
    )
    return out_path


def load_backup(backup_file: Path) -> dict[str, Any]:
    """Load backup from file (supports .json and .json.gz)."""
    if backup_file.suffix == ".gz":
        with gzip.open(backup_file, "rt", encoding="utf-8") as f:
            return json.load(f)
    else:
        with backup_file.open(encoding="utf-8") as f:
            return json.load(f)


async def restore_guild(
    client: discord.Client,
    guild_id: int,
    backup_file: Path,
    dry_run: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Restore guild from backup file."""
    backup_data = load_backup(backup_file)

    if backup_data.get("guild_id") != guild_id:
        raise ValueError(f"Backup guild_id {backup_data.get('guild_id')} doesn't match {guild_id}")

    guild = client.get_guild(guild_id)
    if guild is None:
        guild = await client.fetch_guild(guild_id)

    stats = {
        "categories_created": 0,
        "channels_created": 0,
        "categories_skipped": 0,
        "channels_skipped": 0,
        "errors": 0,
    }

    existing_cats = {cat.name: cat for cat in guild.categories}

    for cat_data in backup_data.get("categories", []):
        cat_name = cat_data["name"]
        cat_position = cat_data.get("position", 0)

        if cat_name in existing_cats:
            if progress_callback:
                progress_callback(f"Category exists: {cat_name}")
            stats["categories_skipped"] += 1
            category = existing_cats[cat_name]
        else:
            if dry_run:
                if progress_callback:
                    progress_callback(f"[DRY RUN] Would create category: {cat_name}")
                stats["categories_created"] += 1
                continue

            try:
                category = await guild.create_category(name=cat_name, position=cat_position)
                existing_cats[cat_name] = category
                if progress_callback:
                    progress_callback(f"Created category: {cat_name}")
                stats["categories_created"] += 1
            except discord.HTTPException as e:
                logger.error("Failed to create category %s: %s", cat_name, e)
                stats["errors"] += 1
                continue

        for chan_data in cat_data.get("channels", []):
            chan_name = chan_data["name"]
            existing_chan = discord.utils.get(category.text_channels, name=chan_name)

            if existing_chan:
                if progress_callback:
                    progress_callback(f"Channel exists: {chan_name} in {cat_name}")
                stats["channels_skipped"] += 1
                continue

            if dry_run:
                if progress_callback:
                    progress_callback(f"[DRY RUN] Would create channel: {chan_name} in {cat_name}")
                stats["channels_created"] += 1
                continue

            try:
                await category.create_text_channel(
                    name=chan_name,
                    topic=chan_data.get("topic"),
                    position=chan_data.get("position", 0),
                    nsfw=chan_data.get("nsfw", False),
                    slowmode_delay=chan_data.get("slowmode_delay", 0),
                )
                if progress_callback:
                    progress_callback(f"Created channel: {chan_name} in {cat_name}")
                stats["channels_created"] += 1
            except discord.HTTPException as e:
                logger.error("Failed to create channel %s in %s: %s", chan_name, cat_name, e)
                stats["errors"] += 1

    return stats


def list_backups(backup_dir: Path, guild_id: int | None = None) -> list[BackupMetadata]:
    """List all backup files with metadata."""
    backups = []
    pattern = f"guild_{guild_id}_*" if guild_id else "guild_*"

    for backup_file in backup_dir.glob(f"{pattern}.json"):
        backups.append(_extract_metadata(backup_file, compressed=False))
    for backup_file in backup_dir.glob(f"{pattern}.json.gz"):
        backups.append(_extract_metadata(backup_file, compressed=True))

    backups.sort(key=lambda m: m.timestamp, reverse=True)
    return backups


def _extract_metadata(backup_file: Path, compressed: bool) -> BackupMetadata:
    try:
        data = load_backup(backup_file)
        return BackupMetadata(
            guild_id=data.get("guild_id", 0),
            guild_name=data.get("guild_name", "Unknown"),
            timestamp=datetime.fromisoformat(data.get("timestamp", "").replace("Z", "+00:00")),
            version=data.get("version", "1.0"),
            categories_count=len(data.get("categories", [])),
            channels_count=sum(len(c.get("channels", [])) for c in data.get("categories", [])),
            file_path=str(backup_file),
            compressed=compressed,
            size_bytes=backup_file.stat().st_size,
        )
    except Exception as e:
        logger.warning("Failed to read backup %s: %s", backup_file, e)
        return BackupMetadata(
            guild_id=0,
            guild_name="Corrupt",
            timestamp=datetime.fromtimestamp(backup_file.stat().st_mtime),
            file_path=str(backup_file),
            compressed=compressed,
            size_bytes=backup_file.stat().st_size,
        )


def cleanup_backups(
    backup_dir: Path,
    max_backups: int = 10,
    retention_days: int = 30,
    guild_id: int | None = None,
    dry_run: bool = False,
) -> list[Path]:
    """Remove old backups based on count and age."""
    backups = list_backups(backup_dir, guild_id)
    to_delete = []

    cutoff_date = datetime.now(UTC) - timedelta(days=retention_days)

    for i, backup in enumerate(backups):
        if i >= max_backups:
            to_delete.append(Path(backup.file_path))
        elif backup.timestamp < cutoff_date:
            to_delete.append(Path(backup.file_path))

    if dry_run:
        return to_delete

    for path in to_delete:
        try:
            path.unlink()
            logger.info("Deleted old backup: %s", path)
        except OSError as e:
            logger.error("Failed to delete backup %s: %s", path, e)

    return to_delete


def create_incremental_backup(
    client: discord.Client,
    guild_id: int,
    out_dir: Path,
    last_backup: Path | None = None,
) -> Path | None:
    """Create incremental backup only if changes detected."""
    if last_backup is None:
        return backup_guild(client, guild_id, out_dir)

    last_data = load_backup(last_backup)
    last_cats = {c["name"]: c for c in last_data.get("categories", [])}

    guild = client.get_guild(guild_id)
    if guild is None:
        raise ValueError(f"Guild {guild_id} not found")

    current_cats = {cat.name: _serialize_category(cat) for cat in guild.categories}

    has_changes = False
    if len(current_cats) != len(last_cats):
        has_changes = True
    else:
        for name, cat in current_cats.items():
            if name not in last_cats:
                has_changes = True
                break
            if cat != last_cats[name]:
                has_changes = True
                break

    if not has_changes:
        logger.info("No changes since last backup, skipping incremental")
        return None

    return backup_guild(
        client, guild_id, out_dir,
        metadata={"incremental": True, "base": str(last_backup)},
    )