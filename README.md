# discord-organizer-bot

Discord bot that synchronizes a local folder tree with Discord categories and text channels.

Each top-level folder becomes a Discord category; each subfolder becomes a text channel inside its category. Names are normalized to kebab-case with configurable prefixes.

## Quickstart

1. Create a Discord application at https://discord.com/developers/applications
2. Copy the bot token to `.env` as `DISCORD_BOT_TOKEN`
3. Copy `config/config.example.yaml` to `config/guild_<id>.yaml` and adjust paths
4. `python3 -m venv .venv && source .venv/bin/activate`
5. `pip install -e ".[dev]"`
6. `organizer dry-run --guild <guild-id>`
7. `organizer sync --guild <guild-id> --apply` when ready

## Configuration

The config file (`config/guild_<id>.yaml`) controls:

- `guild_id` - Discord server ID
- `vault_path` - Local folder to synchronize (e.g., `~/my-vault` or `/path/to/vault`)
- `naming` - Prefixes and normalization rules
- `ignore_patterns` - Folders/files to skip (globs supported)
- `backup_dir` - Where to store guild backups
- `dry_run` - Default mode (true = preview only)

## Example Vault Structure

```
~/my-vault/
├── concepts/                →  📁 concepts / #concepts
├── daily/
│   ├── 2024-01-15/          →  📁 daily / #daily-2024-01-15
│   └── 2024-01-16/          →                 #daily-2024-01-16
├── projects/                →  📁 projects / #projects
└── reports/                 →  📁 reports / #reports
```

## Commands

- `organizer dry-run --guild <id>` - Show sync plan without applying
- `organizer sync --guild <id> --apply` - Execute synchronization
- `organizer backup --guild <id>` - Create guild state backup
- `organizer list-vault --vault <path>` - Print vault tree
- `organizer diff --guild <id>` - Alias for dry-run

## Tests

```bash
pytest -q
ruff check .
```

## License

MIT License - see [LICENSE](LICENSE)