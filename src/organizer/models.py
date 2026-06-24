from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ActionType(StrEnum):
    CREATE_CATEGORY = "create_category"
    CREATE_CHANNEL = "create_channel"
    MOVE_CHANNEL = "move_channel"
    DELETE_CHANNEL = "delete_channel"
    DELETE_CATEGORY = "delete_category"
    UPDATE_CHANNEL = "update_channel"
    UPDATE_CATEGORY = "update_category"
    IGNORE = "ignore"


class ConflictStrategy(StrEnum):
    VAULT_WINS = "vault_wins"
    DISCORD_WINS = "discord_wins"
    MANUAL = "manual"
    SKIP = "skip"


class SyncMode(StrEnum):
    ONE_WAY_VAULT_TO_DISCORD = "vault_to_discord"
    ONE_WAY_DISCORD_TO_VAULT = "discord_to_vault"
    BIDIRECTIONAL = "bidirectional"
    MIRROR = "mirror"


@dataclass
class VaultNode:
    name: str
    path: str
    children: list[VaultNode] = field(default_factory=list)
    is_file: bool = False
    size: int = 0
    modified: datetime | None = None
    is_symlink: bool = False
    symlink_target: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_file": self.is_file,
            "size": self.size,
            "modified": self.modified.isoformat() if self.modified else None,
            "is_symlink": self.is_symlink,
            "symlink_target": self.symlink_target,
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VaultNode:
        node = cls(
            name=data["name"],
            path=data["path"],
            is_file=data.get("is_file", False),
            size=data.get("size", 0),
            modified=datetime.fromisoformat(data["modified"]) if data.get("modified") else None,
            is_symlink=data.get("is_symlink", False),
            symlink_target=data.get("symlink_target"),
            metadata=data.get("metadata", {}),
        )
        node.children = [cls.from_dict(c) for c in data.get("children", [])]
        return node


@dataclass
class PlanAction:
    type: ActionType
    target_name: str
    parent_name: str | None = None
    reason: str = ""
    source_id: int | None = None
    target_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __lt__(self, other: PlanAction) -> bool:
        order = {
            ActionType.DELETE_CHANNEL: 0,
            ActionType.DELETE_CATEGORY: 1,
            ActionType.MOVE_CHANNEL: 2,
            ActionType.CREATE_CATEGORY: 3,
            ActionType.CREATE_CHANNEL: 4,
            ActionType.UPDATE_CHANNEL: 5,
            ActionType.UPDATE_CATEGORY: 6,
            ActionType.IGNORE: 7,
        }
        return order.get(self.type, 99) < order.get(other.type, 99)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "target_name": self.target_name,
            "parent_name": self.parent_name,
            "reason": self.reason,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanAction:
        return cls(
            type=ActionType(data["type"]),
            target_name=data["target_name"],
            parent_name=data.get("parent_name"),
            reason=data.get("reason", ""),
            source_id=data.get("source_id"),
            target_id=data.get("target_id"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Plan:
    actions: list[PlanAction] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add(self, action: PlanAction) -> None:
        self.actions.append(action)

    def sort(self) -> None:
        self.actions.sort()

    def by_type(self, action_type: ActionType) -> list[PlanAction]:
        return [a for a in self.actions if a.type == action_type]

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.type.value] = counts.get(action.type.value, 0) + 1
        return counts

    def filter(self, action_type: ActionType) -> list[PlanAction]:
        return [a for a in self.actions if a.type == action_type]

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        plan = cls(
            actions=[PlanAction.from_dict(a) for a in data.get("actions", [])],
            metadata=data.get("metadata", {}),
        )
        return plan


@dataclass
class ExistingChannel:
    id: int
    name: str
    category_id: int | None = None
    category_name: str | None = None
    position: int = 0
    topic: str | None = None
    nsfw: bool = False
    slowmode_delay: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "position": self.position,
            "topic": self.topic,
            "nsfw": self.nsfw,
            "slowmode_delay": self.slowmode_delay,
            "metadata": self.metadata,
        }


@dataclass
class ExistingCategory:
    id: int
    name: str
    position: int = 0
    channels: list[ExistingChannel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "position": self.position,
            "channels": [c.to_dict() for c in self.channels],
            "metadata": self.metadata,
        }


@dataclass
class GuildState:
    guild_id: int
    guild_name: str
    categories: list[ExistingCategory] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncResult:
    created: int = 0
    moved: int = 0
    updated: int = 0
    deleted: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_changes(self) -> int:
        return self.created + self.moved + self.updated + self.deleted

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append(f"created={self.created}")
        if self.moved:
            parts.append(f"moved={self.moved}")
        if self.updated:
            parts.append(f"updated={self.updated}")
        if self.deleted:
            parts.append(f"deleted={self.deleted}")
        if self.ignored:
            parts.append(f"ignored={self.ignored}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return ", ".join(parts) if parts else "no changes"


@dataclass
class BackupMetadata:
    guild_id: int
    guild_name: str
    timestamp: datetime
    version: str = "1.0"
    categories_count: int = 0
    channels_count: int = 0
    file_path: str = ""
    compressed: bool = False
    size_bytes: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WatchEvent:
    event_type: str
    path: str
    is_directory: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


class NormalizationRule(StrEnum):
    KEBAB_CASE = "kebab-case"
    SNAKE_CASE = "snake_case"
    LOWERCASE = "lowercase"
    NONE = "none"


@dataclass
class NormalizationConfig:
    rule: NormalizationRule = NormalizationRule.KEBAB_CASE
    strip_prefixes: list[str] = field(default_factory=list)
    custom_replacements: dict[str, str] = field(default_factory=dict)
    max_length: int = 100
    allow_unicode: bool = False

    def apply(self, name: str) -> str:
        result = name
        for prefix in self.strip_prefixes:
            if result.startswith(prefix):
                result = result[len(prefix):]
        for old, new in self.custom_replacements.items():
            result = result.replace(old, new)

        if self.rule == NormalizationRule.KEBAB_CASE:
            import unicodedata
            nfkd = unicodedata.normalize("NFKD", result)
            ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c) and (self.allow_unicode or ord(c) < 128))
            cleaned = " ".join(ascii_only.split())
            result = cleaned.lower().replace(" ", "-").replace("_", "-")
        elif self.rule == NormalizationRule.SNAKE_CASE:
            import unicodedata
            nfkd = unicodedata.normalize("NFKD", result)
            ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c) and (self.allow_unicode or ord(c) < 128))
            cleaned = " ".join(ascii_only.split())
            result = cleaned.lower().replace(" ", "_").replace("-", "_")
        elif self.rule == NormalizationRule.LOWERCASE:
            import unicodedata
            nfkd = unicodedata.normalize("NFKD", result)
            ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c) and (self.allow_unicode or ord(c) < 128))
            cleaned = " ".join(ascii_only.split())
            result = cleaned.lower().replace(" ", "").replace("-", "").replace("_", "")
        elif self.rule == NormalizationRule.NONE:
            pass

        if len(result) > self.max_length:
            result = result[:self.max_length].rstrip("-_")

        return result