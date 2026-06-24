from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import discord
from discord import CategoryChannel, TextChannel

from .models import ExistingCategory, ExistingChannel

logger = logging.getLogger(__name__)


@dataclass
class RateLimitState:
    remaining: int = 1
    reset_at: float = 0.0
    limit: int = 50
    bucket: str = "global"

    def is_limited(self) -> bool:
        return self.remaining <= 0 and time.time() < self.reset_at

    def wait_time(self) -> float:
        if self.is_limited():
            return max(0, self.reset_at - time.time())
        return 0.0


class DiscordClient:
    """Enhanced Discord client with persistent connection and rate limit handling."""

    def __init__(
        self,
        bot_token: str,
        *,
        connect_timeout: float = 30.0,
        request_timeout: float = 60.0,
        max_retries: int = 5,
        base_delay: float = 1.0,
        intents: discord.Intents | None = None,
    ):
        self.bot_token = bot_token
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.intents = intents or self._default_intents()

        self._client: discord.Client | None = None
        self._connected = False
        self._rate_limits: dict[str, RateLimitState] = {}
        self._global_ratelimit = RateLimitState()
        self._lock = asyncio.Lock()

    def _default_intents(self) -> discord.Intents:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True
        return intents

    @asynccontextmanager
    async def connect(self):
        """Context manager for connection lifecycle."""
        await self._ensure_connected()
        try:
            yield self
        finally:
            pass

    async def _ensure_connected(self) -> None:
        async with self._lock:
            if self._connected and self._client and not self._client.is_closed():
                return

            client = discord.Client(intents=self.intents)
            try:
                await asyncio.wait_for(client.login(self.bot_token), timeout=self.connect_timeout)
                await asyncio.wait_for(client.connect(), timeout=self.connect_timeout)
                self._client = client
                self._connected = True
                logger.info("Connected to Discord")
            except TimeoutError:
                logger.error("Connection timeout")
                raise
            except Exception as e:
                logger.error(f"Connection failed: {e}")
                raise

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client and not self._client.is_closed():
                await self._client.close()
            self._client = None
            self._connected = False
            logger.info("Disconnected from Discord")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client and not self._client.is_closed()

    async def _handle_rate_limit(self, bucket: str, headers: dict[str, str]) -> None:
        """Update rate limit state from response headers."""
        if "X-RateLimit-Remaining" in headers:
            state = self._rate_limits.get(bucket, RateLimitState(bucket=bucket))
            state.remaining = int(headers.get("X-RateLimit-Remaining", 1))
            state.limit = int(headers.get("X-RateLimit-Limit", 50))
            reset = headers.get("X-RateLimit-Reset")
            if reset:
                state.reset_at = float(reset)
            self._rate_limits[bucket] = state

        if "Retry-After" in headers:
            retry_after = float(headers["Retry-After"])
            self._global_ratelimit.reset_at = time.time() + retry_after
            self._global_ratelimit.remaining = 0

    async def _retry_with_backoff(
        self,
        func,
        *args,
        bucket: str = "default",
        **kwargs,
    ):
        """Execute function with exponential backoff on rate limits."""
        last_exception = None

        for attempt in range(self.max_retries):
            if self._global_ratelimit.is_limited():
                wait = self._global_ratelimit.wait_time()
                logger.warning(f"Global rate limited, waiting {wait:.2f}s")
                await asyncio.sleep(wait)

            state = self._rate_limits.get(bucket, RateLimitState(bucket=bucket))
            if state.is_limited():
                wait = state.wait_time()
                logger.warning(f"Bucket {bucket} rate limited, waiting {wait:.2f}s")
                await asyncio.sleep(wait)

            try:
                result = await func(*args, **kwargs)
                if hasattr(result, "response") and result.response:
                    await self._handle_rate_limit(bucket, dict(result.response.headers))
                return result
            except discord.HTTPException as e:
                last_exception = e
                if e.status == 429:
                    retry_after = e.retry_after or (self.base_delay * (2 ** attempt))
                    logger.warning(
                        f"Rate limited (attempt {attempt + 1}/{self.max_retries}), "
                        f"waiting {retry_after:.2f}s"
                    )
                    await asyncio.sleep(retry_after)
                    continue
                elif e.status >= 500:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"Server error {e.status}, retrying in {delay:.2f}s")
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise
            except discord.NotFound:
                raise
            except discord.Forbidden:
                raise
            except TimeoutError:
                delay = self.base_delay * (2 ** attempt)
                logger.warning(f"Timeout, retrying in {delay:.2f}s")
                await asyncio.sleep(delay)
                continue
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                raise

        raise RuntimeError(f"Max retries exceeded: {last_exception}")

    async def list_guild_state(self, guild_id: int) -> list[ExistingCategory]:
        async def _inner(client: discord.Client) -> list[ExistingCategory]:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")

            result: list[ExistingCategory] = []
            for category in guild.categories:
                channels: list[ExistingChannel] = []
                for channel in category.text_channels:
                    channels.append(ExistingChannel(
                        id=channel.id,
                        name=channel.name,
                        category_id=category.id,
                        category_name=category.name,
                        position=channel.position,
                        topic=channel.topic,
                        nsfw=channel.nsfw,
                        slowmode_delay=channel.slowmode_delay,
                    ))
                result.append(ExistingCategory(
                    id=category.id,
                    name=category.name,
                    position=category.position,
                    channels=channels,
                ))
            return result

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="guild_state")

    async def fetch_category(self, guild_id: int, category_id: int) -> CategoryChannel | None:
        async def _inner(client: discord.Client) -> CategoryChannel | None:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            return guild.get_channel(category_id)

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="category")

    async def fetch_channel(self, guild_id: int, channel_id: int) -> TextChannel | None:
        async def _inner(client: discord.Client) -> TextChannel | None:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            return guild.get_channel(channel_id)

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="channel")

    async def create_category(
        self,
        guild_id: int,
        name: str,
        position: int | None = None,
        reason: str | None = None,
    ) -> CategoryChannel:
        async def _inner(client: discord.Client) -> CategoryChannel:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            return await self._retry_with_backoff(
                guild.create_category,
                name=name,
                position=position,
                reason=reason,
                bucket="create_category",
            )

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="create_category")

    async def create_text_channel(
        self,
        guild_id: int,
        category_id: int,
        name: str,
        topic: str | None = None,
        position: int | None = None,
        slowmode_delay: int = 0,
        nsfw: bool = False,
        reason: str | None = None,
    ) -> TextChannel:
        async def _inner(client: discord.Client) -> TextChannel:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            category = guild.get_channel(category_id)
            if category is None:
                raise ValueError(f"Category {category_id} not found")
            return await self._retry_with_backoff(
                category.create_text_channel,
                name=name,
                topic=topic,
                position=position,
                slowmode_delay=slowmode_delay,
                nsfw=nsfw,
                reason=reason,
                bucket="create_channel",
            )

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="create_channel")

    async def move_channel(
        self,
        guild_id: int,
        channel_id: int,
        category_id: int,
        reason: str | None = None,
    ) -> TextChannel:
        async def _inner(client: discord.Client) -> TextChannel:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            channel = guild.get_channel(channel_id)
            if channel is None:
                raise ValueError(f"Channel {channel_id} not found")
            category = guild.get_channel(category_id)
            if category is None:
                raise ValueError(f"Category {category_id} not found")
            await self._retry_with_backoff(
                channel.edit,
                category=category,
                reason=reason,
                bucket="move_channel",
            )
            return channel

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="move_channel")

    async def update_channel(
        self,
        guild_id: int,
        channel_id: int,
        **kwargs,
    ) -> TextChannel:
        async def _inner(client: discord.Client) -> TextChannel:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            channel = guild.get_channel(channel_id)
            if channel is None:
                raise ValueError(f"Channel {channel_id} not found")
            await self._retry_with_backoff(
                channel.edit,
                **kwargs,
                bucket="update_channel",
            )
            return channel

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="update_channel")

    async def delete_channel(
        self,
        guild_id: int,
        channel_id: int,
        reason: str | None = None,
    ) -> None:
        async def _inner(client: discord.Client) -> None:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            channel = guild.get_channel(channel_id)
            if channel is None:
                return
            await self._retry_with_backoff(
                channel.delete,
                reason=reason,
                bucket="delete_channel",
            )

        await self._ensure_connected()
        await self._retry_with_backoff(_inner, self._client, bucket="delete_channel")

    async def delete_category(
        self,
        guild_id: int,
        category_id: int,
        reason: str | None = None,
    ) -> None:
        async def _inner(client: discord.Client) -> None:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            category = guild.get_channel(category_id)
            if category is None:
                return
            await self._retry_with_backoff(
                category.delete,
                reason=reason,
                bucket="delete_category",
            )

        await self._ensure_connected()
        await self._retry_with_backoff(_inner, self._client, bucket="delete_category")

    async def get_guild_info(self, guild_id: int) -> dict[str, Any]:
        async def _inner(client: discord.Client) -> dict[str, Any]:
            guild = client.get_guild(guild_id)
            if guild is None:
                guild = await self._retry_with_backoff(client.fetch_guild, guild_id, bucket="guild")
            return {
                "id": guild.id,
                "name": guild.name,
                "member_count": guild.member_count,
                "icon": str(guild.icon) if guild.icon else None,
                "features": list(guild.features),
            }

        await self._ensure_connected()
        return await self._retry_with_backoff(_inner, self._client, bucket="guild_info")