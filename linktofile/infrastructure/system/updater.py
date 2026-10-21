"""Self-update support for bare-metal installs (`python main.py --update`).

Docker users should instead run ``docker compose pull && docker compose up -d``;
the updater detects a non-git checkout and says so.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from linktofile.config import GITHUB_REPO
from linktofile.infrastructure.system.version import VERSION

console = Console()
logger = logging.getLogger(__name__)

BACKUP_FILES = (".env", "bot_database.db")


class Updater:
    def __init__(self, repo_url: str = f"https://github.com/{GITHUB_REPO}"):
        self.repo_url = repo_url
        self.base_dir = Path(__file__).resolve().parent.parent
        self.backup_dir = self.base_dir / "backup"

    # --- version check ----------------------------------------------------
    def latest_release(self) -> str | None:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        try:
            request = urllib.request.Request(
                url, headers={"Accept": "application/vnd.github+json", "User-Agent": "link-to-file"}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)
            return str(payload.get("tag_name") or "").lstrip("v") or None
        except Exception as exc:  # noqa: BLE001 - offline is not fatal
            logger.debug("Update check failed: %s", exc)
            return None

    def check_for_updates(self) -> bool:
        """True when a newer release is published."""
        latest = self.latest_release()
        if not latest:
            return False
        return _version_tuple(latest) > _version_tuple(VERSION)

    # --- backup / restore -------------------------------------------------
    def backup_config(self) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_backup = self.backup_dir / timestamp
        target_backup.mkdir(parents=True, exist_ok=True)

        for name in BACKUP_FILES:
            source = self.base_dir / name
            if source.exists():
                shutil.copy2(source, target_backup / name)

        console.print(f"[dim]Backup created at {target_backup}[/dim]")
        return target_backup

    # --- git --------------------------------------------------------------
    def is_git_checkout(self) -> bool:
        return (self.base_dir / ".git").exists()

    def pull_latest(self) -> bool:
        if not self.is_git_checkout():
            console.print(
                "[yellow]This install is not a git checkout (Docker or a release zip).[/yellow]\n"
                "[yellow]Update with:[/yellow] [cyan]docker compose pull && docker compose up -d[/cyan]"
            )
            return False

        console.print("[yellow]Pulling the latest code from GitHub…[/yellow]")
        try:
            result = subprocess.run(
                ["git", "-C", str(self.base_dir), "pull", "--ff-only"],
                capture_output=True,
                text=True,
                check=True,
            )
            if result.stdout.strip():
                console.print(f"[dim]{result.stdout.strip()}[/dim]")
            console.print("[green]Code updated.[/green]")
            return True
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]git pull failed: {exc.stderr.strip()}[/red]")
            return False
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Update failed: {exc}[/red]")
            return False

    # --- orchestration ----------------------------------------------------
    def run_update(self) -> bool:
        console.print(
            Panel(
                f"Current version: [bold]{VERSION}[/bold]",
                title="Update",
                border_style="blue",
            )
        )
        latest = self.latest_release()
        if latest and _version_tuple(latest) <= _version_tuple(VERSION):
            console.print(f"[green]Already on the latest release (v{VERSION}).[/green]")
            return True

        backup_path = self.backup_config()

        if self.pull_latest():
            console.print("[bold green]Update completed.[/bold green]")
            console.print("[dim]Restart the bot to use the new version.[/dim]")
            return True

        console.print("[bold red]Update failed — restoring configuration…[/bold red]")
        for name in BACKUP_FILES:
            if (backup_path / name).exists():
                shutil.copy2(backup_path / name, self.base_dir / name)
        console.print("[yellow]Rollback complete.[/yellow]")
        return False


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def update_system() -> bool:
    return Updater().run_update()
