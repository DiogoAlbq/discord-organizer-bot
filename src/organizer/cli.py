from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.prompt import Confirm
from rich.table import Table
from rich.tree import Tree

from .backup import backup_guild, cleanup_backups, list_backups, restore_guild
from .config import Config
from .discord_client import DiscordClient
from .mapper import plan_bidirectional, plan_from_vault
from .models import ActionType, Plan, SyncResult, VaultNode
from .sync import run_plan
from .vault import calculate_stats, read_vault

load_dotenv()

app = typer.Typer(
    help="Discord Organizer Bot - Synchronize local folder tree with Discord categories/channels",
    rich_markup_mode="rich",
    no_args_is_help=True,
)
console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _load_config(guild_id: int, config_path: Path | None) -> Config:
    if config_path is None:
        config_path = Path(f"config/guild_{guild_id}.yaml")
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    return Config.load(config_path)


def _get_bot_token() -> str:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")
    return token


def _print_plan_rich(plan: Plan, title: str = "Sync Plan") -> None:
    if not plan.actions:
        console.print("[green]No actions needed (already synchronized)[/green]")
        return

    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("Action", style="cyan", no_wrap=True)
    table.add_column("Target", style="white")
    table.add_column("Parent", style="dim")
    table.add_column("Reason", style="yellow")

    action_styles = {
        ActionType.CREATE_CATEGORY: "[bold green]CREATE CAT[/bold green]",
        ActionType.CREATE_CHANNEL: "[bold blue]CREATE CHAN[/bold blue]",
        ActionType.MOVE_CHANNEL: "[bold yellow]MOVE CHAN[/bold yellow]",
        ActionType.UPDATE_CHANNEL: "[bold cyan]UPDATE CHAN[/bold cyan]",
        ActionType.DELETE_CHANNEL: "[bold red]DELETE CHAN[/bold red]",
        ActionType.DELETE_CATEGORY: "[bold red]DELETE CAT[/bold red]",
        ActionType.UPDATE_CATEGORY: "[bold cyan]UPDATE CAT[/bold cyan]",
        ActionType.IGNORE: "[dim]IGNORE[/dim]",
    }

    for action in plan.actions:
        style = action_styles.get(action.type, f"[white]{action.type.value}[/white]")
        parent = action.parent_name or "-"
        table.add_row(style, action.target_name, parent, action.reason)

    console.print(table)
    console.print(f"\nTotal: [bold]{len(plan.actions)}[/bold] actions")


async def _run_with_progress(
    plan: Plan,
    guild_id: int,
    bot_token: str,
    backup_path: Path | None,
    dry_run: bool = False,
) -> SyncResult:
    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be applied[/yellow]")
        return SyncResult(ignored=len(plan.actions))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing...", total=len(plan.actions))

        def progress_callback(p):
            progress.update(task, completed=p.completed, description=p.current_item)

        result = await run_plan(plan, guild_id, bot_token, backup_path, progress_callback)

        progress.update(task, completed=len(plan.actions))
        return result


@app.command()
def dry_run(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    vault: Annotated[Path | None, typer.Option("--vault", "-v", help="Vault path override")] = None,
    output: Annotated[
        str, typer.Option("--output", "-o", help="Output format (table, json, yaml)")
    ] = "table",
) -> None:
    """Show sync plan without applying changes."""
    cfg = _load_config(guild, config)
    if vault:
        cfg.vault.path = str(vault.expanduser().resolve())

    vault_tree = read_vault(
        Path(cfg.vault.path).expanduser().resolve(),
        cfg.vault.ignore_patterns,
        follow_symlinks=cfg.vault.follow_symlinks,
        include_files=cfg.vault.include_files,
        max_depth=cfg.vault.max_depth,
    )

    if guild == 0:
        existing = []
    else:
        client = DiscordClient(_get_bot_token())
        existing = asyncio.run(client.list_guild_state(cfg.discord.guild_id))

    plan = plan_from_vault(vault_tree, cfg, existing)

    if output == "json":
        import json
        console.print_json(json.dumps(plan.to_dict()))
    elif output == "yaml":
        import yaml
        console.print(yaml.dump(plan.to_dict()))
    else:
        _print_plan_rich(plan, f"DRY RUN for guild {cfg.discord.guild_id}")


@app.command()
def sync(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    vault: Annotated[Path | None, typer.Option("--vault", "-v", help="Vault path override")] = None,
    apply: Annotated[
        bool, typer.Option("--apply", "-a", help="Apply changes (default: dry-run)")
    ] = False,
    bidirectional: Annotated[
        bool, typer.Option("--bidirectional", "-b", help="Bidirectional sync")
    ] = False,
) -> None:
    """Synchronize vault with Discord."""
    cfg = _load_config(guild, config)
    if vault:
        cfg.vault.path = str(vault.expanduser().resolve())

    if not apply:
        console.print("[yellow]Dry run mode (use --apply to execute)[/yellow]")
        dry_run(guild=guild, config=config, vault=vault)
        return

    vault_tree = read_vault(
        Path(cfg.vault.path).expanduser().resolve(),
        cfg.vault.ignore_patterns,
        follow_symlinks=cfg.vault.follow_symlinks,
        include_files=cfg.vault.include_files,
        max_depth=cfg.vault.max_depth,
    )

    client = DiscordClient(_get_bot_token())
    existing = asyncio.run(client.list_guild_state(cfg.discord.guild_id))

    if bidirectional:
        plan = plan_bidirectional(vault_tree, cfg, existing)
    else:
        plan = plan_from_vault(vault_tree, cfg, existing)

    console.print(f"[bold]SYNC for guild {cfg.discord.guild_id}[/bold]")
    _print_plan_rich(plan)

    if not Confirm.ask("Proceed with sync?"):
        console.print("[yellow]Cancelled[/yellow]")
        return

    result = asyncio.run(
        _run_with_progress(
            plan, cfg.discord.guild_id,
            _get_bot_token(), cfg.get_backup_dir(),
        )
    )

    console.print(Panel.fit(
        f"[green]Created:[/green] {result.created}  "
        f"[yellow]Moved:[/yellow] {result.moved}  "
        f"[cyan]Updated:[/cyan] {result.updated}  "
        f"[red]Deleted:[/red] {result.deleted}  "
        f"[dim]Ignored:[/dim] {result.ignored}\n"
        f"Duration: {result.duration_ms:.0f}ms",
        title="Sync Complete",
        border_style="green" if result.success else "red",
    ))

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for err in result.errors:
            console.print(f"  - {err}")


@app.command()
def backup(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    out_dir: Annotated[
        Path | None, typer.Option("--out-dir", "-o", help="Output directory")
    ] = None,
    compress: Annotated[bool, typer.Option("--compress", help="Compress backup")] = False,
) -> None:
    """Create backup of current guild state."""
    cfg = _load_config(guild, config)
    backup_dir = out_dir or cfg.get_backup_dir()

    # Guild 0 = test mode without Discord connection
    if guild == 0:
        console.print("[yellow]Cannot create backup without Discord connection (guild=0)[/yellow]")
        return

    client = DiscordClient(_get_bot_token())

    async def _do_backup():
        async with client.connect():
            return backup_guild(client._client, guild, backup_dir, compress=compress)

    backup_file = asyncio.run(_do_backup())
    console.print(f"[green]Backup saved to:[/green] {backup_file}")


@app.command()
def restore(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    file: Annotated[Path, typer.Option("--file", "-f", help="Backup file to restore")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview restore without applying")
    ] = True,
) -> None:
    """Restore guild from backup file."""
    _load_config(guild, config)
    client = DiscordClient(_get_bot_token())

    async def _do_restore():
        async with client.connect():
            return await restore_guild(client._client, guild, file, dry_run=dry_run)

    if dry_run:
        console.print("[yellow]DRY RUN - No changes will be applied[/yellow]")

    stats = asyncio.run(_do_restore())

    console.print(Panel.fit(
        f"[green]Categories created:[/green] {stats.get('categories_created', 0)}  "
        f"[blue]Channels created:[/blue] {stats.get('channels_created', 0)}  "
        f"[dim]Skipped:[/dim] "
        f"{stats.get('categories_skipped', 0) + stats.get('channels_skipped', 0)}  "
        f"[red]Errors:[/red] {stats.get('errors', 0)}",
        title="Restore Complete" if not dry_run else "Restore Preview",
        border_style="green",
    ))


@app.command()
def list_backups_cmd(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
) -> None:
    """List available backups for a guild."""
    cfg = _load_config(guild, config)
    backups = list_backups(cfg.get_backup_dir(), guild)

    if not backups:
        console.print("[yellow]No backups found[/yellow]")
        return

    table = Table(title=f"Backups for guild {guild}")
    table.add_column("File", style="cyan")
    table.add_column("Date", style="white")
    table.add_column("Categories", justify="right")
    table.add_column("Channels", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Compressed", justify="center")

    for b in backups:
        table.add_row(
            Path(b.file_path).name,
            b.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            str(b.categories_count),
            str(b.channels_count),
            f"{b.size_bytes / 1024:.1f} KB",
            "✓" if b.compressed else "✗",
        )

    console.print(table)


@app.command()
def cleanup(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    max_backups: Annotated[int, typer.Option("--max", help="Max backups to keep")] = 10,
    retention_days: Annotated[int, typer.Option("--days", help="Retention period in days")] = 30,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without deleting")] = True,
) -> None:
    """Clean up old backups."""
    cfg = _load_config(guild, config)
    deleted = cleanup_backups(cfg.get_backup_dir(), max_backups, retention_days, guild, dry_run)

    if deleted:
        console.print(
            f"[yellow]{'Would delete' if dry_run else 'Deleted'} "
            f"{len(deleted)} backups:[/yellow]"
        )
        for p in deleted:
            console.print(f"  - {p.name}")
    else:
        console.print("[green]No backups to clean up[/green]")


@app.command()
def list_vault(
    vault: Annotated[Path, typer.Option("--vault", "-v", help="Vault path")],
    ignore: Annotated[
        list[str] | None, typer.Option("--ignore", "-i", help="Ignore patterns")
    ] = None,
    max_depth: Annotated[
        int | None, typer.Option("--max-depth", "-d", help="Max recursion depth")
    ] = None,
    show_files: Annotated[bool, typer.Option("--files", "-f", help="Include files")] = False,
) -> None:
    """List vault tree in human-readable format."""
    vault_tree = read_vault(
        vault.expanduser().resolve(),
        ignore or [],
        include_files=show_files,
        max_depth=max_depth,
    )

    total_dirs, total_files, total_size = calculate_stats(vault_tree)

    tree = Tree(f"[bold blue]{vault_tree.name}[/bold blue]")
    _build_rich_tree(vault_tree, tree)

    console.print(tree)
    console.print(
        f"\n[dim]Directories: {total_dirs}  Files: {total_files}  "
        f"Size: {total_size / 1024:.1f} KB[/dim]"
    )


def _build_rich_tree(node: VaultNode, tree: Tree) -> None:
    style = "blue" if not node.is_file else "white"
    prefix = "📁 " if not node.is_file else "📄 "
    label = f"[{style}]{prefix}{node.name}[/{style}]"
    if node.is_file:
        label += f" [dim]({node.size} bytes)[/dim]"
    branch = tree.add(label)
    for child in node.children:
        _build_rich_tree(child, branch)


@app.command()
def diff(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    vault: Annotated[Path | None, typer.Option("--vault", "-v", help="Vault path override")] = None,
    output: Annotated[
        str, typer.Option("--output", "-o", help="Output format (table, json)")
    ] = "table",
) -> None:
    """Show differences between vault and Discord (alias for dry-run)."""
    dry_run(guild=guild, config=config, vault=vault, output=output)


@app.command()
def doctor(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    vault: Annotated[Path | None, typer.Option("--vault", "-v", help="Vault path override")] = None,
) -> None:
    """Run health checks on configuration and connectivity."""
    console.print("[bold]Running health checks...[/bold]\n")

    cfg = _load_config(guild, config)
    if vault:
        cfg.vault.path = str(vault.expanduser().resolve())

    checks = []

    # Check config file
    config_path = config or Path(f"config/guild_{guild}.yaml")
    checks.append(("Config file exists", config_path.exists(), str(config_path)))

    # Check vault path
    vault_path = Path(cfg.vault.path).expanduser().resolve()
    checks.append(("Vault path exists", vault_path.exists(), str(vault_path)))
    checks.append(("Vault is directory", vault_path.is_dir(), str(vault_path)))

    # Check token
    token = os.getenv("DISCORD_BOT_TOKEN")
    checks.append(("DISCORD_BOT_TOKEN set", bool(token), "env var" if token else "MISSING"))

    # Check Discord connection
    if token:
        try:
            client = DiscordClient(token)
            asyncio.run(client._ensure_connected())
            guild_info = asyncio.run(client.get_guild_info(guild))
            asyncio.run(client.disconnect())
            checks.append((
                "Discord connection", True,
                f"{guild_info.get('name', 'Unknown')} "
                f"({guild_info.get('member_count', 0)} members)",
            ))
        except Exception as e:
            checks.append(("Discord connection", False, str(e)))

    # Check backup directory
    backup_dir = cfg.get_backup_dir()
    checks.append(("Backup dir writable", os.access(backup_dir.parent, os.W_OK), str(backup_dir)))

    table = Table(title="Health Check Results")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Details", style="dim")

    all_ok = True
    for name, ok, detail in checks:
        status = "[green]✓ PASS[/green]" if ok else "[red]✗ FAIL[/red]"
        if not ok:
            all_ok = False
        table.add_row(name, status, detail)

    console.print(table)
    console.print(
        f"\nOverall: {'[green]All checks passed[/green]' if all_ok else '[red]Some checks failed[/red]'}"
    )


@app.command()
def stats(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
) -> None:
    """Show guild statistics."""
    cfg = _load_config(guild, config)

    # Guild 0 = test mode without Discord connection
    if guild == 0:
        console.print("[yellow]Cannot show stats without Discord connection (guild=0)[/yellow]")
        return

    client = DiscordClient(_get_bot_token())

    async def _get_stats():
        async with client.connect():
            info = await client.get_guild_info(cfg.discord.guild_id)
            state = await client.list_guild_state(cfg.discord.guild_id)

            total_categories = len(state)
            total_channels = sum(len(c.channels) for c in state)

            table = Table(title=f"Guild Stats: {info.get('name', 'Unknown')}")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Guild ID", str(info.get("id", "Unknown")))
            table.add_row("Name", info.get("name", "Unknown"))
            table.add_row("Members", str(info.get("member_count", 0)))
            table.add_row("Categories", str(total_categories))
            table.add_row("Text Channels", str(total_channels))
            table.add_row("Features", ", ".join(info.get("features", [])))

            console.print(table)

            cat_table = Table(title="Categories & Channels")
            cat_table.add_column("Category", style="blue")
            cat_table.add_column("Channels", justify="right")
            cat_table.add_column("Position", justify="right")

            for cat in sorted(state, key=lambda c: c.position):
                cat_table.add_row(cat.name, str(len(cat.channels)), str(cat.position))

            console.print(cat_table)

    asyncio.run(_get_stats())


@app.command()
def watch(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    vault: Annotated[Path | None, typer.Option("--vault", "-v", help="Vault path override")] = None,
) -> None:
    """Watch vault for changes and auto-sync (requires watchdog)."""
    cfg = _load_config(guild, config)
    if vault:
        cfg.vault.path = str(vault.expanduser().resolve())

    if not cfg.watch.enabled:
        console.print("[red]Watch mode not enabled in config. Set watch.enabled=true[/yes[/red]")
        return

    console.print(f"[bold]Watching {cfg.vault.path} for changes...[/bold]")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        console.print("[red]watchdog not installed. Install with: pip install watchdog[/red]")
        return

    class VaultHandler(FileSystemEventHandler):
        def __init__(self):
            self.last_sync = 0
            self.debounce = cfg.watch.debounce_seconds

        def on_any_event(self, event):
            import time
            now = time.time()
            if now - self.last_sync < self.debounce:
                return
            self.last_sync = now

            if event.is_directory:
                console.print(
                    f"[yellow]Directory changed:[/yellow] "
                    f"{event.src_path} ({event.event_type})"
                )
            else:
                console.print(
                    f"[yellow]File changed:[/yellow] "
                    f"{event.src_path} ({event.event_type})"
                )

            if cfg.watch.auto_sync:
                console.print("[blue]Auto-syncing...[/blue]")
                try:
                    vault_tree = read_vault(
                        Path(cfg.vault.path).expanduser().resolve(),
                        cfg.vault.ignore_patterns,
                    )
                    client = DiscordClient(_get_bot_token())
                    existing = asyncio.run(client.list_guild_state(cfg.discord.guild_id))
                    plan = plan_from_vault(vault_tree, cfg, existing)
                    result = asyncio.run(
                        _run_with_progress(
                            plan, cfg.discord.guild_id, _get_bot_token(), cfg.get_backup_dir()
                        )
                    )
                    console.print(f"[green]Sync complete:[/green] {result.summary()}")
                except Exception as e:
                    console.print(f"[red]Sync error:[/red] {e}")

    observer = Observer()
    handler = VaultHandler()
    observer.schedule(handler, cfg.vault.path, recursive=cfg.watch.recursive)
    observer.start()

    try:
        while True:
            asyncio.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        console.print("\n[yellow]Stopped watching[/yellow]")
    observer.join()


@app.command()
def prune(
    guild: Annotated[int, typer.Option("--guild", "-g", help="Discord server ID")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Config YAML path")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without deleting")] = True,
) -> None:
    """Remove orphaned Discord channels/categories not in vault."""
    cfg = _load_config(guild, config)

    # Guild 0 = test mode without Discord connection
    if guild == 0:
        vault_tree = read_vault(
            Path(cfg.vault.path).expanduser().resolve(),
            cfg.vault.ignore_patterns,
        )
        existing = []
        plan = plan_bidirectional(vault_tree, cfg, existing)

        delete_actions = [a for a in plan.actions if a.type in (ActionType.DELETE_CHANNEL, ActionType.DELETE_CATEGORY)]

        if not delete_actions:
            console.print("[green]No orphaned items to prune[/green]")
            return

        console.print(f"[yellow]Found {len(delete_actions)} orphaned items[/yellow]")
        _print_plan_rich(Plan(actions=delete_actions), "Items to Delete")

        if dry_run:
            console.print("[yellow]DRY RUN - No items deleted[/yellow]")
            return

        console.print("[yellow]Cannot delete without Discord connection (guild=0)[/yellow]")
        return

    client = DiscordClient(_get_bot_token())

    async def _prune():
        async with client.connect():
            vault_tree = read_vault(
                Path(cfg.vault.path).expanduser().resolve(),
                cfg.vault.ignore_patterns,
            )
            existing = await client.list_guild_state(cfg.discord.guild_id)
            plan = plan_bidirectional(vault_tree, cfg, existing)

        delete_actions = [
            a for a in plan.actions
            if a.type in (ActionType.DELETE_CHANNEL, ActionType.DELETE_CATEGORY)
        ]

        if not delete_actions:
            console.print("[green]No orphaned items to prune[/green]")
            return

        console.print(f"[yellow]Found {len(delete_actions)} orphaned items[/yellow]")
        _print_plan_rich(Plan(actions=delete_actions), "Items to Delete")

        if dry_run:
            console.print("[yellow]DRY RUN - No items deleted[/yellow]")
            return

        if not Confirm.ask("Delete these items?"):
            console.print("[yellow]Cancelled[/yellow]")
            return

        result = await run_plan(Plan(actions=delete_actions), cfg.discord.guild_id, _get_bot_token(), cfg.get_backup_dir())
        console.print(f"[green]Pruned {result.deleted} items[/green]")

    asyncio.run(_prune())


@app.command()
def generate_config(
    path: Annotated[Path, typer.Option("--path", "-p", help="Output path")] = Path("config.example.yaml"),
    profile: Annotated[str, typer.Option("--profile", help="Profile name")] = "default",
) -> None:
    """Generate example configuration file."""
    Config.generate_example(path)
    console.print(f"[green]Example config generated at:[/green] {path}")


if __name__ == "__main__":
    app()