import os
import sys
import shutil
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

console = Console()

def show_welcome():
    welcome_text = """
[bold cyan]Telegram File Downloader Bot[/bold cyan]
[dim]Modern Setup Wizard v1.0.0[/dim]

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

def run_wizard():
    show_welcome()
    check_ffmpeg()

    # 1. Bot Token
    bot_token = Prompt.ask("[bold yellow]Enter your Telegram Bot Token[/bold yellow] (from @BotFather)")
    while not bot_token or ":" not in bot_token:
        rprint("[red]Invalid token format. It usually looks like 123456:ABC-DEF...[/red]")
        bot_token = Prompt.ask("[bold yellow]Enter your Telegram Bot Token[/bold yellow]")

    # 2. Admin ID
    admin_id = IntPrompt.ask("[bold yellow]Enter your Telegram Admin ID[/bold yellow] (use @userinfobot to find it)")

    # 3. Max File Size
    max_size = IntPrompt.ask("[bold yellow]Enter Maximum File Size (MB)[/bold yellow]", default=100)

    # 4. Advanced Settings
    advanced = Prompt.ask("[bold blue]Configure advanced settings?[/bold blue]", choices=["y", "n"], default="n")

    timeout = 300
    if advanced == "y":
        timeout = IntPrompt.ask("Download Timeout (seconds)", default=300)

    # 5. Saving Config
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
"""
        with open(".env", "w") as f:
            f.write(env_content)

        import time
        time.sleep(1)

    # 6. Initialize Database
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
