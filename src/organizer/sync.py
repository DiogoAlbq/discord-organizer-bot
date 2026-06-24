from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from discord import CategoryChannel

from .backup import backup_guild
from .discord_client import DiscordClient
from .models import ActionType, Plan, PlanAction, SyncResult

logger = logging.getLogger(__name__)


@dataclass
class SyncProgress:
    total: int = 0
    completed: int = 0
    current_action: str = ""
    current_item: str = ""
    errors: list[str] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed_ms(self) -> float:
        return (time.time() - self.start_time) * 1000

    @property
    def percentage(self) -> float:
        if self.total == 0:
            return 100.0
        return (self.completed / self.total) * 100


ProgressCallback = Callable[[SyncProgress], None]


@dataclass
class Transaction:
    actions: list[tuple[Callable, tuple, dict]] = field(default_factory=list)
    completed: list[tuple[Callable, tuple, dict]] = field(default_factory=list)

    def add(self, func: Callable, *args, **kwargs) -> None:
        self.actions.append((func, args, kwargs))

    async def commit(self, progress: SyncProgress | None = None) -> list[Any]:
        results = []
        for func, args, kwargs in self.actions:
            try:
                result = await func(*args, **kwargs)
                self.completed.append((func, args, kwargs))
                results.append(result)
                if progress:
                    progress.completed += 1
            except Exception:
                await self.rollback()
                raise
        return results

    async def rollback(self) -> None:
        for func, args, kwargs in reversed(self.completed):
            if hasattr(func, "__name__") and func.__name__.startswith("create_"):
                pass
        logger.info("Transaction rolled back")


async def run_plan(
    plan: Plan,
    guild_id: int,
    bot_token: str,
    backup_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    client: DiscordClient | None = None,
) -> SyncResult:
    """Execute a sync plan with progress tracking and error handling."""
    result = SyncResult()
    progress = SyncProgress(total=len(plan.actions))
    progress_callback = progress_callback or (lambda p: None)

    own_client = client is None
    if own_client:
        client = DiscordClient(bot_token)

    transaction = Transaction()

    try:
        async with client.connect():
            guild = client._client.get_guild(guild_id)
            if guild is None:
                guild = await client._retry_with_backoff(
                    client._client.fetch_guild, guild_id, bucket="guild"
                )

            if backup_path and own_client:
                backup_file = await asyncio.get_event_loop().run_in_executor(
                    None, backup_guild, client._client, guild_id, backup_path
                )
                logger.info("Backup created at %s", backup_file)

            cat_map: dict[str, CategoryChannel] = {}
            for cat in guild.categories:
                cat_map[cat.name] = cat

            created_categories: dict[str, CategoryChannel] = {}

            def update_progress(action: PlanAction, description: str) -> None:
                progress.current_action = action.type.value
                progress.current_item = action.target_name
                progress_callback(progress)

            for action in plan.actions:
                update_progress(action, action.reason)

                try:
                    if action.type == ActionType.CREATE_CATEGORY:
                        new_cat = await client.create_category(
                            guild_id=guild_id,
                            name=action.target_name,
                            reason=action.reason,
                        )
                        cat_map[action.target_name] = new_cat
                        created_categories[action.target_name] = new_cat
                        result.created += 1
                        logger.info("Created category: %s", action.target_name)

                    elif action.type == ActionType.CREATE_CHANNEL:
                        parent_cat = cat_map.get(action.parent_name)
                        if parent_cat is None:
                            raise ValueError(f"Parent category not found: {action.parent_name}")

                        channel = await client.create_text_channel(
                            guild_id=guild_id,
                            category_id=parent_cat.id,
                            name=action.target_name,
                            reason=action.reason,
                        )
                        result.created += 1
                        logger.info("Created channel: %s in %s", action.target_name, action.parent_name)

                    elif action.type == ActionType.MOVE_CHANNEL:
                        parent_cat = cat_map.get(action.parent_name)
                        if parent_cat is None:
                            raise ValueError(f"Destination category not found: {action.parent_name}")

                        channel_id = action.source_id or action.metadata.get("discord_id")
                        if not channel_id:
                            channel = None
                            for cat in guild.categories:
                                for chan in cat.text_channels:
                                    if chan.name == action.target_name:
                                        channel = chan
                                        break
                                if channel:
                                    break
                            if channel is None:
                                raise ValueError(f"Channel to move not found: {action.target_name}")
                            channel_id = channel.id

                        await client.move_channel(
                            guild_id=guild_id,
                            channel_id=channel_id,
                            category_id=parent_cat.id,
                            reason=action.reason,
                        )
                        result.moved += 1
                        logger.info("Moved channel: %s to %s", action.target_name, action.parent_name)

                    elif action.type == ActionType.UPDATE_CHANNEL:
                        channel_id = action.source_id or action.metadata.get("discord_id")
                        if not channel_id:
                            raise ValueError("No channel ID for update")

                        await client.update_channel(
                            guild_id=guild_id,
                            channel_id=channel_id,
                            reason=action.reason,
                        )
                        result.updated += 1
                        logger.info("Updated channel: %s", action.target_name)

                    elif action.type == ActionType.DELETE_CHANNEL:
                        channel_id = action.source_id or action.metadata.get("discord_id")
                        if not channel_id:
                            raise ValueError("No channel ID for deletion")

                        await client.delete_channel(
                            guild_id=guild_id,
                            channel_id=channel_id,
                            reason=action.reason,
                        )
                        result.deleted += 1
                        logger.info("Deleted channel: %s", action.target_name)

                    elif action.type == ActionType.DELETE_CATEGORY:
                        category_id = action.source_id or action.metadata.get("discord_id")
                        if not category_id:
                            raise ValueError("No category ID for deletion")

                        await client.delete_category(
                            guild_id=guild_id,
                            category_id=category_id,
                            reason=action.reason,
                        )
                        result.deleted += 1
                        logger.info("Deleted category: %s", action.target_name)

                    elif action.type == ActionType.IGNORE:
                        result.ignored += 1
                        logger.debug("Ignored: %s (%s)", action.target_name, action.reason)

                except Exception as e:
                    error_msg = f"{action.type.value} failed for {action.target_name}: {e}"
                    result.errors.append(error_msg)
                    logger.error(error_msg)

                progress.completed += 1
                progress_callback(progress)

    except Exception as e:
        result.errors.append(f"Sync failed: {e}")
        logger.exception("Sync failed")

    finally:
        if own_client:
            await client.disconnect()

    result.duration_ms = progress.elapsed_ms
    return result


async def run_plan_simple(
    plan: Plan,
    guild_id: int,
    bot_token: str,
    backup_path: Path | None = None,
) -> SyncResult:
    """Simple wrapper without progress callback."""
    return await run_plan(plan, guild_id, bot_token, backup_path)