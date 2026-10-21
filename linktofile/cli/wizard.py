"""Interactive setup wizard (`python main.py --setup`).

Also callable non-interactively so the one-line installer can reuse it:

    python -c "from cli.wizard import run_wizard; run_wizard(bot_token='123:abc')"
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from linktofile.infrastructure.system.version import VERSION

console = Console()
ENV_PATH = Path(".env")

WELCOME = f"""
[bold cyan]⚡ Link → File[/bold cyan] [dim]v{VERSION}[/dim]
[dim]Send any link, get a file back.[/dim]

This wizard writes a ready-to-run [bold].env[/bold] in about 30 seconds.
"""


def show_welcome() -> None:
    console.print(Panel(WELCOME.strip(), expand=False, border_style="cyan"))


def check_ffmpeg() -> bool:
    if shutil.which("ffmpeg"):
        return True
    rprint("[yellow]⚠  ffmpeg not found — YouTube/Instagram merging needs it.[/yellow]")
    rprint("[dim]   Debian/Ubuntu: sudo apt install ffmpeg[/dim]")
    rprint("[dim]   macOS:         brew install ffmpeg[/dim]\n")
    return False


def existing_config() -> dict[str, str]:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=True)
    return {
        "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
        "ADMIN_ID": os.getenv("ADMIN_ID", ""),
        "MAX_FILE_SIZE_MB": os.getenv("MAX_FILE_SIZE_MB", "2000"),
        "DOWNLOAD_TIMEOUT": os.getenv("DOWNLOAD_TIMEOUT", "600"),
        "TELEGRAM_API_URL": os.getenv("TELEGRAM_API_URL", ""),
    }


def _token_looks_valid(token: str) -> bool:
    head, _, tail = token.partition(":")
    return head.isdigit() and len(tail) >= 20


def verify_token(token: str) -> Optional[str]:
    """Ask Telegram who this token belongs to. Returns the username or None."""
    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getMe",
            headers={"User-Agent": "link-to-file-setup"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.load(response)
        if payload.get("ok"):
            return payload["result"].get("username")
        rprint(f"[red]Telegram rejected the token: {payload.get('description')}[/red]")
        return None
    except Exception as exc:  # noqa: BLE001 - offline setup is allowed
        rprint(f"[yellow]Could not verify the token ({exc}). Continuing.[/yellow]")
        return "unverified"


def _parse_ip_port(api_url: str) -> tuple[str, str]:
    if not api_url:
        return "127.0.0.1", "8081"
    cleaned = api_url.replace("https://", "").replace("http://", "").strip("/")
    host, _, port = cleaned.partition(":")
    return host or "127.0.0.1", port or "8081"


def run_wizard(
    *,
    bot_token: Optional[str] = None,
    admin_id: Optional[int] = None,
    max_size: Optional[int] = None,
    timeout: Optional[int] = None,
    api_url: Optional[str] = None,
    yes: bool = False,
    verify: bool = True,
    env_path: Optional[Path] = None,
) -> bool:
    """Collect settings and write ``.env``. Returns True on success."""
    target = Path(env_path) if env_path else ENV_PATH
    show_welcome()
    check_ffmpeg()
    current = existing_config()

    token = (bot_token or "").strip()
    if not token:
        token = Prompt.ask(
            "[bold yellow]Bot token[/bold yellow] [dim](from @BotFather)[/dim]",
            default=current["BOT_TOKEN"] or None,
        )
    token = (token or "").strip()
    while not _token_looks_valid(token):
        rprint("[red]That doesn't look like a bot token (123456:ABC…).[/red]")
        token = Prompt.ask("[bold yellow]Bot token[/bold yellow]").strip()

    if verify:
        username = verify_token(token)
        if username and username != "unverified":
            rprint(f"[green]✔ Token works: @{username}[/green]")

    if admin_id is None:
        default_admin = int(current["ADMIN_ID"]) if current["ADMIN_ID"].isdigit() else None
        admin_id = IntPrompt.ask(
            "[bold yellow]Your Telegram user ID[/bold yellow] [dim](@userinfobot)[/dim]",
            default=default_admin,
        )

    if max_size is None:
        max_size = IntPrompt.ask(
            "[bold yellow]Max file size (MB)[/bold yellow] [dim]1–2000[/dim]",
            default=int(current["MAX_FILE_SIZE_MB"] or 2000),
        )
    max_size = max(1, min(2000, int(max_size)))

    if timeout is None:
        timeout = IntPrompt.ask(
            "[bold blue]Download timeout (seconds)[/bold blue]",
            default=int(current["DOWNLOAD_TIMEOUT"] or 600),
        )

    if api_url is None:
        api_url = current["TELEGRAM_API_URL"]
        if Confirm.ask(
            "[bold blue]Use a self-hosted Telegram Bot API?[/bold blue] "
            "[dim](needed for >50 MB uploads)[/dim]",
            default=bool(api_url),
        ):
            choice = Prompt.ask(
                "How do you want to enter it?",
                choices=["ip_port", "full"],
                default="ip_port",
            )
            if choice == "ip_port":
                host, port = _parse_ip_port(api_url)
                host = Prompt.ask("Bot API host", default=host)
                port = Prompt.ask("Bot API port", default=port)
                api_url = f"http://{host}:{port}"
            else:
                api_url = Prompt.ask("Full Bot API URL", default=api_url or "http://127.0.0.1:8081")
        else:
            api_url = ""

    table = Table(title="Configuration", header_style="bold magenta", show_header=True)
    table.add_column("Setting", style="dim")
    table.add_column("Value")
    table.add_row("Bot token", f"{token[:8]}…{token[-4:]}" if token else "—")
    table.add_row("Admin ID", str(admin_id))
    table.add_row("Max file size", f"{max_size} MB")
    table.add_row("Download timeout", f"{timeout}s")
    table.add_row("Bot API", api_url or "Telegram cloud (50 MB limit)")
    table.add_row("Config file", str(target.resolve()))
    console.print("", table, "")

    if not yes and not Confirm.ask("Save these settings?", default=True):
        rprint("[red]Nothing saved.[/red]")
        return False

    content = f"""# ⚡ Link → File — generated by the setup wizard
# Docs: https://github.com/Opselon/Link-To-File-Telegram-Bot
BOT_TOKEN={token}
ADMIN_ID={admin_id}

# Limits
MAX_FILE_SIZE_MB={max_size}
DOWNLOAD_TIMEOUT={timeout}
RATE_LIMIT_SECONDS=3
QUEUE_CONCURRENCY=3

# Optional: self-hosted Bot API server (raises uploads to 2 GB)
TELEGRAM_API_URL={api_url}
"""
    target.write_text(content, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass

    try:
        from db import db

        db.init_db()
    except Exception as exc:  # noqa: BLE001 - the bot re-creates the DB anyway
        rprint(f"[yellow]Could not initialise the database now: {exc}[/yellow]")

    rprint(f"[bold green]✅ Saved {target}[/bold green]")
    if shutil.which("docker") and Path("docker-compose.yml").exists():
        rprint("Start it with: [cyan]docker compose up -d[/cyan]  (or [cyan]python main.py[/cyan])")
    else:
        rprint("Start it with: [cyan]python main.py[/cyan]")
    return True


if __name__ == "__main__":
    try:
        run_wizard()
    except KeyboardInterrupt:
        rprint("\n[red]Setup cancelled.[/red]")
        sys.exit(1)
