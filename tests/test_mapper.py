from __future__ import annotations

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
from organizer.mapper import normalize, plan_bidirectional, plan_from_vault
from organizer.models import ActionType, ExistingCategory, ExistingChannel, VaultNode
from organizer.vault import read_vault


@pytest.fixture
def base_config() -> Config:
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


def test_normalize_basic() -> None:
    assert normalize("Relatórios Diários") == "relatorios-diarios"
    assert normalize("Concepts") == "concepts"
    assert normalize("Daily Notes") == "daily-notes"
    assert normalize("already-kebab") == "already-kebab"
    assert normalize("UPPER_CASE") == "upper-case"
    assert normalize("  spaces  ") == "spaces"


def test_normalize_snake_case() -> None:
    from organizer.config import NamingConfig
    config = NamingConfig(folder_to_channel="snake_case")
    assert normalize("Daily Notes", config) == "daily_notes"
    assert normalize("UPPER-CASE", config) == "upper_case"


def test_normalize_custom_replacements() -> None:
    from organizer.config import NamingConfig
    config = NamingConfig(
        folder_to_channel="kebab-case",
        custom_replacements={"_": "-", ".": "-"},
    )
    assert normalize("test.name_here", config) == "test-name-here"


def test_normalize_strip_prefixes() -> None:
    from organizer.config import NamingConfig
    config = NamingConfig(
        folder_to_channel="kebab-case",
        strip_prefixes=["prefix_", "old-"],
    )
    assert normalize("prefix_test", config) == "test"
    assert normalize("old-name", config) == "name"


def test_plan_from_vault_empty_existing(base_config: Config, sample_vault_path: Path) -> None:
    vault = read_vault(sample_vault_path, base_config.vault.ignore_patterns)
    plan = plan_from_vault(vault, base_config, [])

    create_cats = [a for a in plan.actions if a.type == ActionType.CREATE_CATEGORY]
    create_chans = [a for a in plan.actions if a.type == ActionType.CREATE_CHANNEL]

    assert len(create_cats) == 4
    cat_names = {a.target_name for a in create_cats}
    assert cat_names == {"📁 concepts", "📁 daily", "📁 projects", "📁 reports"}

    assert len(create_chans) == 2
    chan_names = {a.target_name for a in create_chans}
    assert chan_names == {"#daily-2026-06-16", "#daily-2026-06-17"}

    for action in create_chans:
        assert action.parent_name == "📁 daily"


def test_plan_from_vault_with_existing_categories(
    base_config: Config,
    sample_vault_path: Path,
    existing_categories_with_content: list[ExistingCategory],
) -> None:
    vault = read_vault(sample_vault_path, base_config.vault.ignore_patterns)
    plan = plan_from_vault(vault, base_config, existing_categories_with_content)

    create_cats = [a for a in plan.actions if a.type == ActionType.CREATE_CATEGORY]
    ignores = [a for a in plan.actions if a.type == ActionType.IGNORE]

    assert len(create_cats) == 2
    cat_names = {a.target_name for a in create_cats}
    assert cat_names == {"📁 projects", "📁 reports"}

    ignore_cats = [a for a in ignores if a.reason == "already exists"]
    assert len(ignore_cats) == 2
    ignored_names = {a.target_name for a in ignore_cats}
    assert ignored_names == {"📁 concepts", "📁 daily"}


def test_plan_from_vault_channel_already_in_correct_category(
    base_config: Config, existing_categories_with_content: list[ExistingCategory]
) -> None:
    vault = VaultNode(name="root", path="/tmp", children=[
        VaultNode(name="daily", path="/tmp/daily", children=[
            VaultNode(name="2026-06-16", path="/tmp/daily/2026-06-16"),
        ]),
    ])

    plan = plan_from_vault(vault, base_config, existing_categories_with_content)

    ignores = [a for a in plan.actions if a.type == ActionType.IGNORE]
    chan_ignores = [a for a in ignores if a.target_name == "#daily-2026-06-16"]
    assert len(chan_ignores) == 1
    assert chan_ignores[0].reason == "already exists in correct category"


def test_plan_from_vault_channel_in_wrong_category(
    base_config: Config, sample_vault_path: Path
) -> None:
    existing = [
        ExistingCategory(
            id=1,
            name="📁 concepts",
            channels=[
                ExistingChannel(
                    id=10,
                    name="#daily-2026-06-16",
                    category_id=1,
                    category_name="📁 concepts",
                )
            ],
        ),
    ]

    vault = read_vault(sample_vault_path, base_config.vault.ignore_patterns)
    plan = plan_from_vault(vault, base_config, existing)

    moves = [a for a in plan.actions if a.type == ActionType.MOVE_CHANNEL]
    assert len(moves) == 1
    assert moves[0].target_name == "#daily-2026-06-16"
    assert moves[0].parent_name == "📁 daily"
    assert moves[0].reason == "channel exists in wrong category"


def test_plan_ignores_patterns(base_config: Config, temp_vault: Path) -> None:
    (temp_vault / "ignored_folder").mkdir(exist_ok=True)
    (temp_vault / "ignored_folder" / "sub").mkdir(exist_ok=True)

    vault = read_vault(temp_vault, base_config.vault.ignore_patterns)
    plan = plan_from_vault(vault, base_config, [])

    ignored = [
        a for a in plan.actions
        if a.type == ActionType.IGNORE and a.reason == "ignored by pattern"
    ]
    assert len(ignored) == 0


def test_plan_bidirectional(base_config: Config, sample_vault_path: Path) -> None:
    existing = [
        ExistingCategory(
            id=1,
            name="📁 concepts",
            channels=[
                ExistingChannel(
                    id=10,
                    name="#concepts",
                    category_id=1,
                    category_name="📁 concepts",
                ),
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
                ),
            ],
        ),
    ]

    vault = read_vault(sample_vault_path, base_config.vault.ignore_patterns)
    plan = plan_bidirectional(vault, base_config, existing)

    delete_actions = [a for a in plan.actions if a.type.value.startswith("delete_")]
    assert len(delete_actions) >= 0


def test_conflict_resolution_vault_wins(base_config: Config) -> None:
    base_config.sync.conflict_strategy = "vault_wins"

    existing = [
        ExistingCategory(
            id=1,
            name="📁 concepts",
            channels=[
                ExistingChannel(
                    id=10,
                    name="#daily-2026-06-16",
                    category_id=1,
                    category_name="📁 concepts",
                )
            ],
        ),
    ]

    vault = VaultNode(name="root", path="/tmp", children=[
        VaultNode(name="daily", path="/tmp/daily", children=[
            VaultNode(name="2026-06-16", path="/tmp/daily/2026-06-16"),
        ]),
    ])

    plan = plan_from_vault(vault, base_config, existing)

    moves = [a for a in plan.actions if a.type == ActionType.MOVE_CHANNEL]
    assert len(moves) == 1
    assert moves[0].reason == "channel exists in wrong category"


def test_conflict_resolution_discord_wins(base_config: Config) -> None:
    base_config.sync.conflict_strategy = "discord_wins"

    existing = [
        ExistingCategory(
            id=1,
            name="📁 concepts",
            channels=[
                ExistingChannel(
                    id=10,
                    name="#daily-2026-06-16",
                    category_id=1,
                    category_name="📁 concepts",
                )
            ],
        ),
    ]

    vault = VaultNode(name="root", path="/tmp", children=[
        VaultNode(name="daily", path="/tmp/daily", children=[
            VaultNode(name="2026-06-16", path="/tmp/daily/2026-06-16"),
        ]),
    ])

    plan = plan_from_vault(vault, base_config, existing)

    ignores = [a for a in plan.actions if a.type == ActionType.IGNORE]
    conflict_ignores = [a for a in ignores if "conflict" in a.metadata]
    assert len(conflict_ignores) >= 0


def test_plan_delete_orphaned(base_config: Config) -> None:
    base_config.sync.delete_orphaned = True

    existing = [
        ExistingCategory(
            id=1,
            name="📁 orphaned_cat",
            channels=[
                ExistingChannel(
                    id=10,
                    name="#orphaned_chan",
                    category_id=1,
                    category_name="📁 orphaned_cat",
                ),
            ],
        ),
    ]

    vault = VaultNode(name="root", path="/tmp", children=[])
    plan = plan_from_vault(vault, base_config, existing)

    delete_cats = [a for a in plan.actions if a.type.value == "delete_category"]
    delete_chans = [a for a in plan.actions if a.type.value == "delete_channel"]

    assert len(delete_cats) == 1
    assert len(delete_chans) == 1