from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NormalizationRule(str):
    KEBAB_CASE = "kebab-case"
    SNAKE_CASE = "snake_case"
    LOWERCASE = "lowercase"
    NONE = "none"


class ConflictStrategy(str):
    VAULT_WINS = "vault_wins"
    DISCORD_WINS = "discord_wins"
    MANUAL = "manual"
    SKIP = "skip"


class NamingConfig(BaseModel):
    folder_to_channel: str = "kebab-case"
    prefix_categories: str = "📁 "
    prefix_channels: str = "#"
    strip_prefixes: list[str] = Field(default_factory=list)
    custom_replacements: dict[str, str] = Field(default_factory=dict)
    max_length: int = 100
    allow_unicode: bool = False

    @field_validator("folder_to_channel")
    @classmethod
    def validate_rule(cls, v: str) -> str:
        allowed = ["kebab-case", "snake_case", "lowercase", "none"]
        if v not in allowed:
            raise ValueError(f"folder_to_channel must be one of {allowed}")
        return v

    @field_validator("max_length")
    @classmethod
    def validate_max_length(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("max_length must be between 1 and 100")
        return v


class SyncConfig(BaseModel):
    conflict_strategy: str = "manual"
    delete_orphaned: bool = False
    update_existing: bool = True
    create_missing: bool = True
    move_misplaced: bool = True
    dry_run: bool = True
    backup_before_sync: bool = True
    max_concurrent_ops: int = 5
    rate_limit_buffer: float = 1.5


class WatchConfig(BaseModel):
    enabled: bool = False
    debounce_seconds: float = 2.0
    recursive: bool = True
    ignore_patterns: list[str] = Field(default_factory=list)
    auto_sync: bool = False
    sync_on_startup: bool = True


class BackupConfig(BaseModel):
    enabled: bool = True
    directory: str = "./backups"
    max_backups: int = 10
    compress: bool = False
    retention_days: int = 30
    incremental: bool = False


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file: str | None = None
    max_bytes: int = 10_485_760
    backup_count: int = 5
    json_format: bool = False


class DiscordConfig(BaseModel):
    token: str | None = None
    guild_id: int
    intents: list[str] = Field(default_factory=lambda: ["guilds", "guild_messages"])
    rate_limit_retries: int = 5
    rate_limit_base_delay: float = 1.0
    connect_timeout: float = 30.0
    request_timeout: float = 60.0


class VaultConfig(BaseModel):
    path: str
    ignore_patterns: list[str] = Field(default_factory=lambda: [
        "venv", "__pycache__", ".git", "node_modules", "*.tmp", "backups",
        ".env", ".venv", "dist", "build", "*.egg-info", ".pytest_cache",
        ".ruff_cache", ".mypy_cache", ".coverage", "htmlcov",
    ])
    follow_symlinks: bool = False
    include_files: bool = False
    max_depth: int | None = None
    use_gitignore: bool = True

    def get_vault_path(self) -> Path:
        return Path(self.path).expanduser().resolve()


class ProfileConfig(BaseModel):
    name: str
    extends: str | None = None
    vault: VaultConfig | None = None
    discord: DiscordConfig | None = None
    naming: NamingConfig | None = None
    sync: SyncConfig | None = None
    watch: WatchConfig | None = None
    backup: BackupConfig | None = None
    logging: LoggingConfig | None = None


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORGANIZER_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    profile: str = "default"
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)

    vault: VaultConfig = Field(default_factory=lambda: VaultConfig(path="~/my-vault"))
    discord: DiscordConfig = Field(default_factory=lambda: DiscordConfig(guild_id=0))
    naming: NamingConfig = Field(default_factory=NamingConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)
    backup: BackupConfig = Field(default_factory=BackupConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @model_validator(mode="after")
    def apply_profile(self) -> Config:
        if self.profile and self.profile in self.profiles:
            profile = self.profiles[self.profile]
            if profile.extends and profile.extends in self.profiles:
                base = self.profiles[profile.extends]
                self._merge_profile(base)
            self._merge_profile(profile)
        return self

    def _merge_profile(self, profile: ProfileConfig) -> None:
        if profile.vault:
            self.vault = profile.vault
        if profile.discord:
            self.discord = profile.discord
        if profile.naming:
            self.naming = profile.naming
        if profile.sync:
            self.sync = profile.sync
        if profile.watch:
            self.watch = profile.watch
        if profile.backup:
            self.backup = profile.backup
        if profile.logging:
            self.logging = profile.logging

    @classmethod
    def load(cls, path: Path) -> Config:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        env_overrides = cls._collect_env_overrides()
        data = cls._deep_merge(data, env_overrides)

        return cls.model_validate(data)

    @classmethod
    def _collect_env_overrides(cls) -> dict[str, Any]:
        overrides = {}
        prefix = "ORGANIZER_"
        for key, value in os.environ.items():
            if key.startswith(prefix):
                nested_key = key[len(prefix):].lower().replace("__", ".")
                cls._set_nested(overrides, nested_key, value)
        return overrides

    @classmethod
    def _set_nested(cls, d: dict, key: str, value: str) -> None:
        parts = key.split(".")
        current = d
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        try:
            current[parts[-1]] = yaml.safe_load(value)
        except yaml.YAMLError:
            current[parts[-1]] = value

    @classmethod
    def _deep_merge(cls, base: dict, override: dict) -> dict:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = cls._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def get_vault_path(self) -> Path:
        return Path(self.vault.path).expanduser().resolve()

    def get_backup_dir(self) -> Path:
        return Path(self.backup.directory).expanduser().resolve()

    def to_dict(self) -> dict:
        return self.model_dump(mode="python")

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    @classmethod
    def generate_example(cls, path: Path) -> None:
        example = cls(
            profile="default",
            profiles={
                "production": ProfileConfig(
                    name="production",
                    sync=SyncConfig(dry_run=False, delete_orphaned=True),
                    watch=WatchConfig(enabled=True, auto_sync=True),
                    backup=BackupConfig(compress=True, retention_days=90),
                    logging=LoggingConfig(level="WARNING", file="logs/organizer.log", json_format=True),
                ),
                "development": ProfileConfig(
                    name="development",
                    extends="default",
                    sync=SyncConfig(dry_run=True, max_concurrent_ops=1),
                    logging=LoggingConfig(level="DEBUG"),
                ),
            },
            vault=VaultConfig(path="~/my-vault"),
            discord=DiscordConfig(guild_id=123456789012345678),
            naming=NamingConfig(
                folder_to_channel="kebab-case",
                prefix_categories="📁 ",
                prefix_channels="#",
                max_length=100,
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
            watch=WatchConfig(
                enabled=False,
                debounce_seconds=2.0,
                auto_sync=False,
            ),
            backup=BackupConfig(
                enabled=True,
                directory="./backups",
                max_backups=10,
                retention_days=30,
            ),
            logging=LoggingConfig(
                level="INFO",
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            ),
        )
        example.save(path)