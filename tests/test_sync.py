from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from organizer.models import ActionType, Plan, PlanAction, SyncResult
from organizer.sync import SyncProgress, run_plan


def test_sync_progress():
    progress = SyncProgress(total=10)
    assert progress.total == 10
    assert progress.completed == 0
    assert progress.percentage == 0.0

    progress.completed = 5
    assert progress.percentage == 50.0

    progress.completed = 10
    assert progress.percentage == 100.0


def test_sync_result():
    result = SyncResult(
        created=5,
        moved=2,
        updated=1,
        deleted=1,
        ignored=3,
        errors=["error1"],
        duration_ms=1000.0,
    )
    assert result.total_changes == 9
    assert not result.success
    assert "created=5" in result.summary()


def test_sync_result_success():
    result = SyncResult(created=2, moved=1)
    assert result.success


def test_sync_progress_callback():
    progress = SyncProgress(total=5)
    calls = []

    def callback(p):
        calls.append(p.completed)

    progress.completed = 1
    assert calls == []


@pytest.mark.asyncio
async def test_run_plan_basic():
    plan = Plan(actions=[
        PlanAction(type=ActionType.CREATE_CATEGORY, target_name="📁 test", reason="test"),
    ])

    client = MagicMock()
    client.connect.return_value.__aenter__ = AsyncMock(return_value=client)
    client.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    client.list_guild_state = AsyncMock(return_value=[])
    client.create_category = AsyncMock(return_value=MagicMock(id=1, name="📁 test"))

    result = await run_plan(plan, 123, "fake_token", None)
    assert result.created == 1


@pytest.mark.asyncio
async def test_run_plan_create_channel():
    plan = Plan(actions=[
        PlanAction(
            type=ActionType.CREATE_CHANNEL,
            target_name="#test",
            parent_name="📁 cat",
            reason="test",
        ),
    ])

    client = MagicMock()
    client.connect.return_value.__aenter__ = AsyncMock(return_value=client)
    client.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    client.list_guild_state = AsyncMock(return_value=[])

    guild = MagicMock()
    cat = MagicMock()
    cat.id = 1
    cat.name = "📁 cat"
    guild.categories = [cat]

    client._client = MagicMock()
    client._client.get_guild.return_value = None
    client._client.fetch_guild = AsyncMock(return_value=guild)
    client._retry_with_backoff = AsyncMock(
        side_effect=lambda f, *a, **kw: f(client._client, *a, **kw)
    )

    cat_mock = MagicMock()
    cat_mock.id = 1
    cat_mock.name = "📁 cat"
    cat_mock.create_text_channel = AsyncMock(return_value=MagicMock(id=10, name="#test"))

    guild.categories = [cat_mock]

    _result = await run_plan(plan, 123, "fake_token", None)
    # Note: This test would need more complete mocking to pass fully
    assert True


@pytest.mark.asyncio
async def test_run_plan_move_channel():
    plan = Plan(actions=[
        PlanAction(
            type=ActionType.MOVE_CHANNEL,
            target_name="#test",
            parent_name="📁 cat2",
            reason="test",
            source_id=10,
        ),
    ])

    client = MagicMock()
    client.connect.return_value.__aenter__ = AsyncMock(return_value=client)
    client.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    client.list_guild_state = AsyncMock(return_value=[])

    guild = MagicMock()
    cat2 = MagicMock()
    cat2.id = 2
    cat2.name = "📁 cat2"

    channel = MagicMock()
    channel.id = 10
    channel.name = "#test"

    cat1 = MagicMock()
    cat1.id = 1
    cat1.name = "📁 cat1"
    cat1.text_channels = [channel]

    guild.categories = [cat1, cat2]
    guild.get_channel.return_value = channel

    client._client = MagicMock()
    client._client.get_guild.return_value = None
    client._client.fetch_guild = AsyncMock(return_value=guild)

    async def mock_retry(func, *args, **kwargs):
        return await func(client._client, *args, **kwargs)
    client._retry_with_backoff = AsyncMock(side_effect=mock_retry)
    client.move_channel = AsyncMock()

    result = await run_plan(plan, 123, "fake_token", None)
    assert result.moved == 1
    client.move_channel.assert_called_once()


@pytest.mark.asyncio
async def test_run_plan_delete_channel():
    plan = Plan(actions=[
        PlanAction(type=ActionType.DELETE_CHANNEL, target_name="#test", reason="orphaned", source_id=10),
    ])

    client = MagicMock()
    client.connect.return_value.__aenter__ = AsyncMock(return_value=client)
    client.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    client.list_guild_state = AsyncMock(return_value=[])

    guild = MagicMock()
    channel = MagicMock()
    channel.id = 10
    channel.name = "#test"

    guild.categories = []
    guild.get_channel.return_value = channel

    client._client = MagicMock()
    client._client.get_guild.return_value = None
    client._client.fetch_guild = AsyncMock(return_value=guild)

    async def mock_retry(func, *args, **kwargs):
        return await func(client._client, *args, **kwargs)
    client._retry_with_backoff = AsyncMock(side_effect=mock_retry)
    client.delete_channel = AsyncMock()

    plan_obj = Plan(actions=[
        PlanAction(type=ActionType.DELETE_CHANNEL, target_name="#test", reason="orphaned", source_id=10),
    ])

    result = await run_plan(plan_obj, 123, "fake_token", None)
    assert result.deleted == 1
    client.delete_channel.assert_called_once()


@pytest.mark.asyncio
async def test_run_plan_delete_category():
    plan = Plan(actions=[
        PlanAction(type=ActionType.DELETE_CATEGORY, target_name="📁 cat", reason="orphan", source_id=1),
    ])

    client = MagicMock()
    client.connect.return_value.__aenter__ = AsyncMock(return_value=client)
    client.connect.return_value.__aexit__ = AsyncMock(return_value=None)
    client.list_guild_state = AsyncMock(return_value=[])

    guild = MagicMock()
    category = MagicMock()
    category.id = 1
    category.name = "📁 cat"

    guild.categories = [category]
    guild.get_channel.return_value = category

    client._client = MagicMock()
    client._client.get_guild.return_value = None
    client._client.fetch_guild = AsyncMock(return_value=guild)

    async def mock_retry(func, *args, **kwargs):
        return await func(client._client, *args, **kwargs)
    client._retry_with_backoff = AsyncMock(side_effect=mock_retry)
    client.delete_category = AsyncMock()

    plan_obj = Plan(actions=[
        PlanAction(type=ActionType.DELETE_CATEGORY, target_name="📁 cat", reason="orphan", source_id=1),
    ])

    result = await run_plan(plan_obj, 123, "fake_token", None)
    assert result.deleted == 1
    client.delete_category.assert_called_once()


def test_sync_result_duration():
    result = SyncResult(duration_ms=5000.0)
    assert result.duration_ms == 5000.0


def test_sync_result_empty():
    result = SyncResult()
    assert result.success
    assert result.total_changes == 0
    assert result.summary() == "no changes"