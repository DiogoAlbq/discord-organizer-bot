from __future__ import annotations

from organizer.models import (
    ActionType,
    ConflictStrategy,
    ExistingCategory,
    ExistingChannel,
    NormalizationConfig,
    NormalizationRule,
    Plan,
    PlanAction,
    SyncResult,
    VaultNode,
)


def test_action_type_enum():
    assert ActionType.CREATE_CATEGORY == "create_category"
    assert ActionType.CREATE_CHANNEL == "create_channel"
    assert ActionType.MOVE_CHANNEL == "move_channel"
    assert ActionType.DELETE_CHANNEL == "delete_channel"
    assert ActionType.DELETE_CATEGORY == "delete_category"
    assert ActionType.UPDATE_CHANNEL == "update_channel"
    assert ActionType.UPDATE_CATEGORY == "update_category"
    assert ActionType.IGNORE == "ignore"


def test_conflict_strategy_enum():
    assert ConflictStrategy.VAULT_WINS == "vault_wins"
    assert ConflictStrategy.DISCORD_WINS == "discord_wins"
    assert ConflictStrategy.MANUAL == "manual"
    assert ConflictStrategy.SKIP == "skip"


def test_plan_action_creation():
    action = PlanAction(
        type=ActionType.CREATE_CATEGORY,
        target_name="📁 test",
        reason="test reason",
    )
    assert action.type == ActionType.CREATE_CATEGORY
    assert action.target_name == "📁 test"
    assert action.reason == "test reason"


def test_plan_action_to_dict():
    action = PlanAction(
        type=ActionType.CREATE_CHANNEL,
        target_name="#test-channel",
        parent_name="📁 test",
        reason="test",
        metadata={"key": "value"},
    )
    d = action.to_dict()
    assert d["type"] == "create_channel"
    assert d["target_name"] == "#test-channel"
    assert d["parent_name"] == "📁 test"
    assert d["metadata"] == {"key": "value"}


def test_plan_action_from_dict():
    data = {
        "type": "create_category",
        "target_name": "📁 test",
        "parent_name": None,
        "reason": "test",
        "metadata": {"key": "value"},
    }
    action = PlanAction.from_dict(data)
    assert action.type == ActionType.CREATE_CATEGORY
    assert action.target_name == "📁 test"
    assert action.metadata == {"key": "value"}


def test_plan_creation():
    plan = Plan()
    plan.add(PlanAction(type=ActionType.CREATE_CATEGORY, target_name="📁 test", reason="test"))
    assert len(plan.actions) == 1


def test_plan_filter():
    plan = Plan(actions=[
        PlanAction(type=ActionType.CREATE_CATEGORY, target_name="cat1", reason="test"),
        PlanAction(type=ActionType.CREATE_CHANNEL, target_name="#chan1", reason="test"),
        PlanAction(type=ActionType.CREATE_CHANNEL, target_name="#chan2", reason="test"),
    ])
    cats = plan.filter(ActionType.CREATE_CATEGORY)
    chans = plan.filter(ActionType.CREATE_CHANNEL)
    assert len(cats) == 1
    assert len(chans) == 2


def test_plan_stats():
    plan = Plan(actions=[
        PlanAction(type=ActionType.CREATE_CATEGORY, target_name="cat1", reason="test"),
        PlanAction(type=ActionType.CREATE_CHANNEL, target_name="#chan1", reason="test"),
        PlanAction(type=ActionType.CREATE_CHANNEL, target_name="#chan2", reason="test"),
    ])
    stats = plan.stats()
    assert stats["create_category"] == 1
    assert stats["create_channel"] == 2


def test_plan_sort():
    plan = Plan(actions=[
        PlanAction(type=ActionType.CREATE_CHANNEL, target_name="#chan1", reason="test"),
        PlanAction(type=ActionType.CREATE_CATEGORY, target_name="cat1", reason="test"),
        PlanAction(type=ActionType.MOVE_CHANNEL, target_name="#chan2", reason="test"),
    ])
    plan.sort()
    assert plan.actions[0].type == ActionType.CREATE_CATEGORY
    assert plan.actions[1].type == ActionType.MOVE_CHANNEL
    assert plan.actions[2].type == ActionType.CREATE_CHANNEL


def test_vault_node():
    node = VaultNode(name="test", path="/test", children=[
        VaultNode(name="child", path="/test/child"),
    ])
    assert node.name == "test"
    assert node.path == "/test"
    assert len(node.children) == 1
    assert not node.is_leaf


def test_vault_node_to_dict():
    node = VaultNode(name="test", path="/test")
    d = node.to_dict()
    assert d["name"] == "test"
    assert d["path"] == "/test"
    assert d["children"] == []


def test_vault_node_from_dict():
    data = {"name": "test", "path": "/test", "children": []}
    node = VaultNode.from_dict(data)
    assert node.name == "test"
    assert node.path == "/test"


def test_existing_category():
    cat = ExistingCategory(
        id=1,
        name="📁 test",
        channels=[
            ExistingChannel(id=10, name="#chan1", category_id=1, category_name="📁 test"),
        ],
    )
    assert cat.id == 1
    assert cat.name == "📁 test"
    assert len(cat.channels) == 1


def test_existing_channel():
    chan = ExistingChannel(
        id=10,
        name="#test",
        category_id=1,
        category_name="📁 test",
        topic="Test topic",
        position=5,
        nsfw=True,
        slowmode_delay=10,
    )
    assert chan.id == 10
    assert chan.topic == "Test topic"
    assert chan.position == 5
    assert chan.nsfw is True
    assert chan.slowmode_delay == 10


def test_sync_result():
    result = SyncResult(
        created=5,
        moved=2,
        updated=1,
        deleted=1,
        ignored=3,
        errors=["error1"],
    )
    assert result.total_changes == 9
    assert not result.success


def test_sync_result_summary():
    result = SyncResult(created=2, moved=1, ignored=3)
    assert result.summary() == "created=2, moved=1, ignored=3"


def test_normalization_config():
    config = NormalizationConfig(
        rule=NormalizationRule.KEBAB_CASE,
        strip_prefixes=["prefix_"],
        custom_replacements={"_": "-"},
        max_length=50,
    )
    assert config.apply("prefix_Test_Name") == "test-name"
    assert config.apply("A" * 60) == "a" * 50


def test_normalization_rules():
    config = NormalizationConfig(rule=NormalizationRule.SNAKE_CASE)
    assert config.apply("Test Name") == "test_name"

    config = NormalizationConfig(rule=NormalizationRule.LOWERCASE)
    assert config.apply("Test Name") == "testname"

    config = NormalizationConfig(rule=NormalizationRule.NONE)
    assert config.apply("Test Name") == "Test Name"


def test_normalization_custom_replacements():
    config = NormalizationConfig(
        rule=NormalizationRule.KEBAB_CASE,
        custom_replacements={".": "-", "_": "-", " ": "-"},
    )
    assert config.apply("test.name_here") == "test-name-here"