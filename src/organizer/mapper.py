from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .config import Config, ConflictStrategy, NamingConfig
from .models import (
    ActionType,
    ExistingCategory,
    ExistingChannel,
    Plan,
    PlanAction,
    VaultNode,
)


@dataclass
class MappingResult:
    plan: Plan
    conflicts: list[dict[str, Any]]
    stats: dict[str, int]


def normalize(name: str, config: NamingConfig | None = None) -> str:
    """Normalize name according to config rules."""
    if config is None:
        config = NamingConfig()

    # Apply custom replacements first
    for old, new in config.custom_replacements.items():
        name = name.replace(old, new)

    # Strip prefixes
    for prefix in config.strip_prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]

    # Unicode normalization
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(
        c for c in nfkd
        if not unicodedata.combining(c) and (ord(c) < 128 or config.allow_unicode)
    )

    # Strip and collapse whitespace
    cleaned = " ".join(ascii_only.split())

    # Apply case/format rule
    rule = config.folder_to_channel
    if rule == "kebab-case":
        result = cleaned.lower().replace(" ", "-").replace("_", "-")
    elif rule == "snake_case":
        result = cleaned.lower().replace(" ", "_").replace("-", "_")
    elif rule == "lowercase":
        result = cleaned.lower().replace(" ", "").replace("-", "").replace("_", "")
    elif rule == "none":
        result = cleaned
    else:
        result = cleaned.lower().replace(" ", "-").replace("_", "-")

    # Truncate if needed
    if len(result) > config.max_length:
        result = result[:config.max_length].rstrip("-_")

    return result


def _should_ignore(name: str, ignore_patterns: list[str]) -> bool:
    for pattern in ignore_patterns:
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if name.startswith(prefix):
                return True
        elif name == pattern:
            return True
    return False


def _normalize_discord_name(name: str, prefix: str, config: NamingConfig) -> str:
    """Normalize Discord name removing known prefix."""
    if name.startswith(prefix):
        name = name[len(prefix):]
    return normalize(name, config)


def _build_existing_maps(
    existing_categories: Iterable[ExistingCategory],
    config: Config,
) -> tuple[dict[str, ExistingCategory], dict[str, ExistingChannel]]:
    cat_map: dict[str, ExistingCategory] = {}
    chan_map: dict[str, ExistingChannel] = {}

    cat_prefix = config.naming.prefix_categories
    chan_prefix = config.naming.prefix_channels

    for cat in existing_categories:
        norm_name = _normalize_discord_name(cat.name, cat_prefix, config.naming)
        cat_map[norm_name] = cat
        for chan in cat.channels:
            norm_chan = _normalize_discord_name(chan.name, chan_prefix, config.naming)
            chan_map[norm_chan] = chan

    return cat_map, chan_map


def _collect_vault_categories(vault: VaultNode, config: Config) -> list[VaultNode]:
    categories: list[VaultNode] = []
    for child in vault.children:
        if child.is_file:
            continue
        if not _should_ignore(child.name, config.vault.ignore_patterns):
            categories.append(child)
    return categories


def _detect_conflicts(
    vault_categories: list[VaultNode],
    config: Config,
    cat_map: dict[str, ExistingCategory],
    chan_map: dict[str, ExistingChannel],
) -> list[dict[str, Any]]:
    conflicts = []

    for vault_cat in vault_categories:
        norm_cat_name = normalize(vault_cat.name, config.naming)
        display_cat_name = config.naming.prefix_categories + norm_cat_name

        existing_cat = cat_map.get(norm_cat_name)

        if existing_cat:
            for vault_chan in vault_cat.children:
                if vault_chan.is_file:
                    continue
                if _should_ignore(vault_chan.name, config.vault.ignore_patterns):
                    continue

                combined_name = f"{vault_cat.name}-{vault_chan.name}"
                norm_chan_name = normalize(combined_name, config.naming)
                display_chan_name = config.naming.prefix_channels + norm_chan_name

                existing_chan = chan_map.get(norm_chan_name)
                if existing_chan and existing_chan.category_id != existing_cat.id:
                    conflicts.append({
                        "type": "channel_wrong_category",
                        "vault_category": vault_cat.name,
                        "vault_channel": vault_chan.name,
                        "discord_channel": existing_chan.name,
                        "discord_category": existing_cat.name,
                        "expected_category": display_cat_name,
                        "resolution": "move",
                    })

    return conflicts


def _resolve_conflicts(
    conflicts: list[dict[str, Any]],
    strategy: ConflictStrategy,
) -> dict[str, str]:
    resolutions = {}
    for conflict in conflicts:
        if strategy == ConflictStrategy.VAULT_WINS:
            resolutions[conflict["vault_channel"]] = "move"
        elif strategy == ConflictStrategy.DISCORD_WINS:
            resolutions[conflict["vault_channel"]] = "ignore"
        elif strategy == ConflictStrategy.SKIP:
            resolutions[conflict["vault_channel"]] = "skip"
        else:
            resolutions[conflict["vault_channel"]] = "manual"
    return resolutions


def plan_from_vault(
    vault: VaultNode,
    config: Config,
    existing: Iterable[ExistingCategory],
    mode: str = "vault_to_discord",
) -> Plan:
    """Generate sync plan from vault structure."""
    plan = Plan()
    existing_categories = list(existing)

    cat_map, chan_map = _build_existing_maps(existing_categories, config)
    vault_categories = _collect_vault_categories(vault, config)

    conflicts = _detect_conflicts(vault_categories, config, cat_map, chan_map)
    resolutions = _resolve_conflicts(conflicts, config.sync.conflict_strategy)

    for vault_cat in vault_categories:
        norm_cat_name = normalize(vault_cat.name, config.naming)
        display_cat_name = config.naming.prefix_categories + norm_cat_name

        existing_cat = cat_map.get(norm_cat_name)

        if existing_cat is None:
            plan.add(PlanAction(
                type=ActionType.CREATE_CATEGORY,
                target_name=display_cat_name,
                reason="category does not exist",
                metadata={"vault_path": vault_cat.path},
            ))
            existing_cat_for_channels = None
        else:
            plan.add(PlanAction(
                type=ActionType.IGNORE,
                target_name=display_cat_name,
                reason="already exists",
                metadata={"discord_id": existing_cat.id},
            ))
            existing_cat_for_channels = existing_cat

        for vault_chan in vault_cat.children:
            if vault_chan.is_file:
                continue
            if _should_ignore(vault_chan.name, config.vault.ignore_patterns):
                plan.add(PlanAction(
                    type=ActionType.IGNORE,
                    target_name=vault_chan.name,
                    reason="ignored by pattern",
                ))
                continue

            combined_name = f"{vault_cat.name}-{vault_chan.name}"
            norm_chan_name = normalize(combined_name, config.naming)
            display_chan_name = config.naming.prefix_channels + norm_chan_name

            existing_chan = chan_map.get(norm_chan_name)
            resolution = resolutions.get(vault_chan.name, "manual")

            if existing_chan is None:
                if config.sync.create_missing:
                    plan.add(PlanAction(
                        type=ActionType.CREATE_CHANNEL,
                        target_name=display_chan_name,
                        parent_name=display_cat_name,
                        reason="channel does not exist",
                        metadata={"vault_path": vault_chan.path},
                    ))
                else:
                    plan.add(PlanAction(
                        type=ActionType.IGNORE,
                        target_name=display_chan_name,
                        parent_name=display_cat_name,
                        reason="create_missing disabled",
                    ))
            else:
                in_correct_cat = (
                    existing_cat_for_channels
                    and existing_chan.category_id == existing_cat_for_channels.id
                )

                if in_correct_cat:
                    if config.sync.update_existing:
                        plan.add(PlanAction(
                            type=ActionType.UPDATE_CHANNEL,
                            target_name=display_chan_name,
                            parent_name=display_cat_name,
                            reason="update existing channel",
                            source_id=existing_chan.id,
                            metadata={"discord_id": existing_chan.id},
                        ))
                    else:
                        plan.add(PlanAction(
                            type=ActionType.IGNORE,
                            target_name=display_chan_name,
                            parent_name=display_cat_name,
                            reason="already exists in correct category",
                            metadata={"discord_id": existing_chan.id},
                        ))
                else:
                    resolution = resolutions.get(vault_chan.name, "manual")
                    if resolution == "move" and config.sync.move_misplaced:
                        plan.add(PlanAction(
                            type=ActionType.MOVE_CHANNEL,
                            target_name=display_chan_name,
                            parent_name=display_cat_name,
                            reason="channel exists in wrong category",
                            source_id=existing_chan.id,
                            metadata={
                                "discord_id": existing_chan.id,
                                "from_category": existing_chan.category_name,
                            },
                        ))
                    elif resolution == "ignore":
                        plan.add(PlanAction(
                            type=ActionType.IGNORE,
                            target_name=display_chan_name,
                            parent_name=display_cat_name,
                            reason="conflict resolved: keep in current category",
                            metadata={"discord_id": existing_chan.id},
                        ))
                    else:
                        plan.add(PlanAction(
                            type=ActionType.IGNORE,
                            target_name=display_chan_name,
                            parent_name=display_cat_name,
                            reason="manual resolution required",
                            metadata={
                                "discord_id": existing_chan.id,
                                "conflict": True,
                                "current_category": existing_chan.category_name,
                            },
                        ))

    if config.sync.delete_orphaned:
        existing_chan_names = set(chan_map.keys())
        vault_chan_names = set()
        for vault_cat in vault_categories:
            for vault_chan in vault_cat.children:
                if not vault_chan.is_file and not _should_ignore(vault_chan.name, config.vault.ignore_patterns):
                    combined = f"{vault_cat.name}-{vault_chan.name}"
                    vault_chan_names.add(normalize(combined, config.naming))

        orphaned = existing_chan_names - vault_chan_names
        for orphan in orphaned:
            chan = chan_map[orphan]
            plan.add(PlanAction(
                type=ActionType.DELETE_CHANNEL,
                target_name=config.naming.prefix_channels + orphan,
                reason="orphaned channel not in vault",
                source_id=chan.id,
                metadata={"discord_id": chan.id, "category": chan.category_name},
            ))

    plan.metadata = {
        "conflicts": conflicts,
        "resolutions": resolutions,
        "mode": mode,
        "vault_categories": len(vault_categories),
        "vault_channels": sum(len(c.children) for c in vault_categories),
    }
    plan.stats = plan.stats()

    return plan


def plan_bidirectional(
    vault: VaultNode,
    config: Config,
    existing: Iterable[ExistingCategory],
) -> Plan:
    """Generate plan for bidirectional sync."""
    plan = Plan()
    existing_categories = list(existing)

    cat_map, chan_map = _build_existing_maps(existing_categories, config)
    vault_categories = _collect_vault_categories(vault, config)

    # First pass: vault -> discord (creates, moves)
    forward_plan = plan_from_vault(vault, config, existing, "vault_to_discord")
    plan.actions.extend(forward_plan.actions)

    # Second pass: discord -> vault (detects channels not in vault)
    for cat in existing_categories:
        norm_cat = _normalize_discord_name(cat.name, config.naming.prefix_categories, config.naming)
        vault_cat_match = next(
            (vc for vc in vault_categories if normalize(vc.name, config.naming) == norm_cat),
            None
        )

        if vault_cat_match is None:
            if config.sync.delete_orphaned:
                plan.add(PlanAction(
                    type=ActionType.DELETE_CATEGORY,
                    target_name=cat.name,
                    reason="orphaned category not in vault",
                    source_id=cat.id,
                ))
            continue

        for chan in cat.channels:
            norm_chan = _normalize_discord_name(chan.name, config.naming.prefix_channels, config.naming)
            vault_chan_match = False
            for vault_chan in vault_cat_match.children:
                if vault_chan.is_file:
                    continue
                combined = f"{vault_cat_match.name}-{vault_chan.name}"
                if normalize(combined, config.naming) == norm_chan:
                    vault_chan_match = True
                    break

            if not vault_chan_match and config.sync.delete_orphaned:
                plan.add(PlanAction(
                    type=ActionType.DELETE_CHANNEL,
                    target_name=chan.name,
                    reason="orphaned channel not in vault",
                    source_id=chan.id,
                ))

    plan.metadata = forward_plan.metadata
    plan.stats = plan.stats()

    return plan