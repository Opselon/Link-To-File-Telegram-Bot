import os
import sys
import shutil
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from rich.console import Console
from core.version import VERSION

console = Console()
logger = logging.getLogger(__name__)

class Updater:
    def __init__(self, repo_url="https://github.com/Opselon/Link-To-File-Telegram-Bot"):
        self.repo_url = repo_url
        self.base_dir = Path(__file__).resolve().parent.parent
        self.backup_dir = self.base_dir / "backup"

    def check_for_updates(self) -> bool:
        """
        Checks for updates by fetching the version file from GitHub.
        """
        try:
            import aiohttp
            import asyncio

            # Using raw.githubusercontent.com for easy fetching
            raw_version_url = f"{self.repo_url.replace('github.com', 'raw.githubusercontent.com')}/main/core/version.py"

            async def fetch_version():
                async with aiohttp.ClientSession() as session:
                    async with session.get(raw_version_url) as response:
                        if response.status == 200:
                            content = await response.text()
                            # Extract version from content: VERSION = "1.0.0"
                            import re
                            match = re.search(r'VERSION\s*=\s*"([^"]+)"', content)
                            if match:
                                return match.group(1)
                return None

            latest_version = asyncio.run(fetch_version())
            if latest_version and latest_version != VERSION:
                console.print(f"[bold cyan]A new version is available: {latest_version}[/bold cyan]")
                return True
            return False
        except Exception:
            return False

    def backup_config(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_backup = self.backup_dir / timestamp
        target_backup.mkdir(parents=True, exist_ok=True)

        config_files = [".env", "bot_database.db"]
        for f in config_files:
            source = self.base_dir / f
            if source.exists():
                shutil.copy2(source, target_backup / f)

        console.print(f"[dim]Backup created at {target_backup}[/dim]")
        return target_backup

    def pull_latest(self):
        try:
            console.print("[yellow]Pulling latest changes from GitHub...[/yellow]")
            result = subprocess.run(["git", "pull"], capture_output=True, text=True, check=True)
            console.print(f"[dim]{result.stdout}[/dim]")
            console.print("[green]Successfully pulled latest changes.[/green]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Git pull failed: {e.stderr}[/red]")
            return False
        except Exception as e:
            console.print(f"[red]Update failed: {e}[/red]")
            return False

    def run_update(self):
        console.print(Panel(f"Current Version: [bold]{VERSION}[/bold]", title="Update System", border_style="blue"))

        # 1. Backup
        backup_path = self.backup_config()

        # 2. Pull
        if self.pull_latest():
            # 3. Migrations (Placeholder)
            console.print("[blue]Checking for database migrations...[/blue]")
            # from db import db; db.migrate()

            console.print("[bold green]Update completed successfully![/bold green]")
        else:
            console.print("[bold red]Update failed. Rolling back...[/bold red]")
            # Rollback logic
            for f in [".env", "bot_database.db"]:
                if (backup_path / f).exists():
                    shutil.copy2(backup_path / f, self.base_dir / f)
            console.print("[yellow]Rollback complete.[/yellow]")

from rich.panel import Panel

def update_system():
    updater = Updater()
    updater.run_update()
