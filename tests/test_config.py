from __future__ import annotations

from pathlib import Path

import pytest

from organizer.config import (
    Config,
)


def test_load_config_example() -> None:
    config_path = Path(__file__).parent.parent / "config" / "config.example.yaml"
    cfg = Config.load(config_path)

    assert cfg.discord.guild_id == 123456789012345678
    assert cfg.vault.path == "~/my-vault"
    assert cfg.naming.folder_to_channel == "kebab-case"
    assert cfg.naming.prefix_categories == "📁 "
    assert cfg.naming.prefix_channels == "#"
    assert "venv" in cfg.vault.ignore_patterns
    assert "__pycache__" in cfg.vault.ignore_patterns
    assert cfg.backup.directory == "./backups"
    assert cfg.sync.dry_run is True


def test_config_to_dict_roundtrip(tmp_path: Path) -> None:
    config_path = Path(__file__).parent.parent / "config" / "config.example.yaml"
    cfg = Config.load(config_path)

    out_path = tmp_path / "test_config.yaml"
    cfg.save(out_path)

    cfg2 = Config.load(out_path)
    assert cfg2.discord.guild_id == cfg.discord.guild_id
    assert cfg2.vault.path == cfg.vault.path
    assert cfg2.naming.folder_to_channel == cfg.naming.folder_to_channel
    assert cfg2.vault.ignore_patterns == cfg.vault.ignore_patterns


def test_load_config_missing_required_field(tmp_path: Path) -> None:
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("vault:\n  path: /tmp\n")

    with pytest.raises(Exception):  # noqa: B017
        Config.load(bad_config)


def test_config_profiles(tmp_path: Path) -> None:
    config_data = {
        "profile": "development",
        "profiles": {
            "development": {
                "extends": "default",
                "name": "development",
                "sync": {"dry_run": True, "max_concurrent_ops": 1},
                "logging": {"level": "DEBUG"},
            },
            "default": {
                "name": "default",
                "sync": {"dry_run": True, "max_concurrent_ops": 5},
                "logging": {"level": "INFO"},
            },
        },
        "vault": {"path": "~/my-vault"},
        "discord": {"guild_id": 123456789},
        "naming": {"folder_to_channel": "kebab-case"},
        "sync": {"dry_run": True},
        "watch": {"enabled": False},
        "backup": {"directory": "./backups"},
        "logging": {"level": "INFO"},
    }

    import yaml
    config_path = tmp_path / "test_profiles.yaml"
    with config_path.open("w") as f:
        yaml.dump(config_data, f)

    cfg = Config.load(config_path)

    assert cfg.sync.dry_run is True
    assert cfg.sync.max_concurrent_ops == 1
    assert cfg.logging.level == "DEBUG"


def test_config_env_override(tmp_path: Path, monkeypatch) -> None:
    config_data = {
        "vault": {"path": "~/my-vault"},
        "discord": {"guild_id": 123456789},
        "naming": {"folder_to_channel": "kebab-case"},
        "sync": {"dry_run": True},
        "watch": {"enabled": False},
        "backup": {"directory": "./backups"},
        "logging": {"level": "INFO"},
    }

    import yaml
    config_path = tmp_path / "test_env.yaml"
    with config_path.open("w") as f:
        yaml.dump(config_data, f)

    monkeypatch.setenv("ORGANIZER_SYNC__DRY_RUN", "false")
    monkeypatch.setenv("ORGANIZER_SYNC__MAX_CONCURRENT_OPS", "10")
    monkeypatch.setenv("ORGANIZER_LOGGING__LEVEL", "DEBUG")

    cfg = Config.load(config_path)

    assert cfg.sync.dry_run is False
    assert cfg.sync.max_concurrent_ops == 10
    assert cfg.logging.level == "DEBUG"


def test_config_validation() -> None:
    with pytest.raises(Exception):  # noqa: B017
        Config(
            vault=type('obj', (object,), {'path': 'test'})(),
            discord=type('obj', (object,), {'guild_id': 'not_an_int'})(),
            naming=type('obj', (object,), {'folder_to_channel': 'invalid'})(),
            sync=type('obj', (object,), {'dry_run': True})(),
            watch=type('obj', (object,), {'enabled': False})(),
            backup=type('obj', (object,), {'directory': './backups'})(),
            logging=type('obj', (object,), {'level': 'INFO'})(),
        )


def test_config_generate_example(tmp_path: Path) -> None:
    out_path = tmp_path / "generated_config.yaml"
    Config.generate_example(out_path)

    assert out_path.exists()

    cfg = Config.load(out_path)
    assert cfg.discord.guild_id == 123456789012345678
    assert "production" in cfg.profiles
    assert "development" in cfg.profiles