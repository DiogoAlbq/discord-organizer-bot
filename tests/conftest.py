from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from organizer.config import (
    BackupConfig,
    Config,
    DiscordConfig,
    LoggingConfig,
    NamingConfig,
    SyncConfig,
    VaultConfig,
    WatchConfig,
)
from organizer.models import (
    ActionType,
    ExistingCategory,
    ExistingChannel,
    Plan,
    PlanAction,
    VaultNode,
)


@pytest.fixture
def sample_vault_path() -> Path:
    return Path(__file__).parent / "fixtures" / "sample_vault"


@pytest.fixture
def temp_vault() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "concepts").mkdir()
        (base / "daily" / "2026-06-16").mkdir(parents=True)
        (base / "daily" / "2026-06-17").mkdir(parents=True)
        (base / "projects").mkdir()
        (base / "reports").mkdir()
        (base / "venv").mkdir()
        (base / "__pycache__").mkdir()
        (base / ".git").mkdir()
        (base / "node_modules").mkdir()
        yield base


@pytest.fixture
def mock_config() -> Config:
    return Config(
        vault=VaultConfig(path="/fake/vault"),
        discord=DiscordConfig(guild_id=123456789),
        naming=NamingConfig(
            folder_to_channel="kebab-case",
            prefix_categories="📁 ",
            prefix_channels="#",
        ),
        sync=SyncConfig(
            conflict_strategy="manual",
            delete_orphaned=False,
            update_existing=True,
            create_missing=True,
            move_misplaced=True,
            dry_run=True,
            backup_before_sync=True,
        ),
        watch=WatchConfig(),
        backup=BackupConfig(),
        logging=LoggingConfig(),
    )


@pytest.fixture
def empty_existing_categories() -> list[ExistingCategory]:
    return []


@pytest.fixture
def existing_categories_with_content() -> list[ExistingCategory]:
    return [
        ExistingCategory(
            id=1,
            name="📁 concepts",
            channels=[
                ExistingChannel(
                    id=10,
                    name="#concepts",
                    category_id=1,
                    category_name="📁 concepts",
                )
            ],
        ),
        ExistingCategory(
            id=2,
            name="📁 daily",
            channels=[
                ExistingChannel(
                    id=20,
                    name="#daily-2026-06-16",
                    category_id=2,
                    category_name="📁 daily",
                )
            ],
        ),
    ]


@pytest.fixture
def sample_plan() -> Plan:
    return Plan(
        actions=[
            PlanAction(
                type=ActionType.CREATE_CATEGORY,
                target_name="📁 test",
                reason="test",
            ),
            PlanAction(
                type=ActionType.CREATE_CHANNEL,
                target_name="#test-channel",
                parent_name="📁 test",
                reason="test",
            ),
        ],
        metadata={"guild_id": 123},
    )


@pytest.fixture
def sample_vault_node() -> VaultNode:
    return VaultNode(
        name="root",
        path="/tmp/root",
        children=[
            VaultNode(name="cat1", path="/tmp/root/cat1", children=[
                VaultNode(name="chan1", path="/tmp/root/cat1/chan1"),
                VaultNode(name="chan2", path="/tmp/root/cat1/chan2"),
            ]),
            VaultNode(name="cat2", path="/tmp/root/cat2", children=[
                VaultNode(name="chan3", path="/tmp/root/cat2/chan3"),
            ]),
        ],
    )


@pytest.fixture
def event_loop():
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()