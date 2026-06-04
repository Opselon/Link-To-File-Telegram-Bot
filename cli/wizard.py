import os
import sys
import shutil
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich import print as rprint
from dotenv import load_dotenv

console = Console()

def show_welcome():
    welcome_text = """
[bold cyan]Telegram File Downloader Bot[/bold cyan]
[dim]Modern Setup Wizard v1.1.0[/dim]

This wizard will help you configure your bot in seconds.
    """
    console.print(Panel(welcome_text, expand=False, border_style="cyan"))

def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        rprint("[yellow]⚠️  Warning: FFmpeg was not found in your PATH.[/yellow]")
        rprint("[yellow]YouTube downloads might fail. It is recommended to install FFmpeg.[/yellow]")
        rprint("[dim]On Ubuntu: sudo apt install ffmpeg[/dim]\n")
        return False
    return True

def get_existing_config():
    """Loads existing config from .env if it exists."""
    if Path(".env").exists():
        load_dotenv(".env", override=True)

    return {
        "BOT_TOKEN": os.getenv("BOT_TOKEN", ""),
        "ADMIN_ID": os.getenv("ADMIN_ID", ""),
        "MAX_FILE_SIZE_MB": os.getenv("MAX_FILE_SIZE_MB", "100"),
        "DOWNLOAD_TIMEOUT": os.getenv("DOWNLOAD_TIMEOUT", "300"),
        "TELEGRAM_API_URL": os.getenv("TELEGRAM_API_URL", ""),
    }

def run_wizard():
    show_welcome()
    check_ffmpeg()

    config = get_existing_config()

    # 1. Bot Token
    bot_token = Prompt.ask(
        "[bold yellow]Enter your Telegram Bot Token[/bold yellow] (from @BotFather)",
        default=config["BOT_TOKEN"]
    )
    while not bot_token or ":" not in bot_token:
        rprint("[red]Invalid token format. It usually looks like 123456:ABC-DEF...[/red]")
        bot_token = Prompt.ask("[bold yellow]Enter your Telegram Bot Token[/bold yellow]")

    # 2. Admin ID
    admin_id = IntPrompt.ask(
        "[bold yellow]Enter your Telegram Admin ID[/bold yellow] (use @userinfobot to find it)",
        default=int(config["ADMIN_ID"]) if config["ADMIN_ID"] else None
    )

    # 3. Max File Size
    max_size = IntPrompt.ask(
        "[bold yellow]Enter Maximum File Size (MB)[/bold yellow]",
        default=int(config["MAX_FILE_SIZE_MB"])
    )

    # 4. Advanced Settings
    timeout = IntPrompt.ask(
        "[bold blue]Download Timeout (seconds)[/bold blue]",
        default=int(config["DOWNLOAD_TIMEOUT"])
    )

    telegram_api_url = config["TELEGRAM_API_URL"]
    configure_api = Confirm.ask(
        "[bold blue]Configure Custom Telegram Bot API URL?[/bold blue]",
        default=bool(telegram_api_url)
    )

    if configure_api:
        api_choice = Prompt.ask(
            "How would you like to set the API URL?",
            choices=["full", "ip_port"],
            default="ip_port" if telegram_api_url and ":" in telegram_api_url.replace("http://", "").replace("https://", "") else "full"
        )

        if api_choice == "ip_port":
            # Try to parse existing
            existing_ip = "127.0.0.1"
            existing_port = "8081"
            if telegram_api_url:
                parts = telegram_api_url.replace("http://", "").replace("https://", "").strip("/").split(":")
                if len(parts) == 2:
                    existing_ip, existing_port = parts

            ip = Prompt.ask("Enter API Server IP", default=existing_ip)
            port = IntPrompt.ask("Enter API Server Port", default=int(existing_port))
            telegram_api_url = f"http://{ip}:{port}"
        else:
            telegram_api_url = Prompt.ask("Enter Full Custom API URL", default=telegram_api_url)
    else:
        telegram_api_url = ""

    # 5. Summary
    table = Table(title="Configuration Summary", show_header=True, header_style="bold magenta")
    table.add_column("Setting", style="dim")
    table.add_column("Value")

    table.add_row("Bot Token", f"{bot_token[:6]}...{bot_token[-4:]}" if bot_token else "None")
    table.add_row("Admin ID", str(admin_id))
    table.add_row("Max File Size", f"{max_size} MB")
    table.add_row("Timeout", f"{timeout} seconds")
    table.add_row("Telegram API URL", telegram_api_url or "Default")

    console.print("\n", table, "\n")

    if not Confirm.ask("Do you want to save these settings?"):
        rprint("[red]Setup cancelled. Changes not saved.[/red]")
        return

    # 6. Saving Config
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="Saving configuration...", total=None)

        env_content = f"""# Bot Configuration
BOT_TOKEN={bot_token}
ADMIN_ID={admin_id}

# Downloader Configuration
MAX_FILE_SIZE_MB={max_size}
DOWNLOAD_TIMEOUT={timeout}

# Optional: Custom Telegram Bot API (for files up to 2GB)
TELEGRAM_API_URL={telegram_api_url}
"""
        with open(".env", "w") as f:
            f.write(env_content)

        import time
        time.sleep(1)

    # 7. Initialize Database
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        progress.add_task(description="Initializing database...", total=None)
        from db import db
        db.init_db()
        time.sleep(1)

    rprint("\n[bold green]✅ Setup Complete![/bold green]")
    rprint("You can now start the bot by running: [cyan]python main.py[/cyan]\n")

if __name__ == "__main__":
    try:
        run_wizard()
    except KeyboardInterrupt:
        rprint("\n[red]Setup cancelled.[/red]")
        sys.exit(1)
