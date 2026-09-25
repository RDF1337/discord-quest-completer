import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winsound
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

# ============================================================
# Настройки
# ============================================================

API_URL = "https://discord.com/api/v9/applications/detectable"

# 15 минут + 15 секунд запаса на сетевые задержки Discord
DEFAULT_DURATION = 15 * 60 + 15

BASE_DIR = Path(os.environ.get("TEMP", ".")) / "DiscordQuests"
MUTEX_NAME = r"Local\DiscordQuestCompleter"

console = Console()


# ============================================================
# Управление заголовком окна консоли
# ============================================================

def set_console_title(title: str):
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


# ============================================================
# Windows Mutex (Защита от повторного запуска)
# ============================================================

class SingleInstance:
    def __init__(self, name: str):
        self.name = name
        self.handle = None

    def acquire(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        self.handle = kernel32.CreateMutexW(None, False, self.name)
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True

    def release(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None


# ============================================================
# Вспомогательные функции
# ============================================================

def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_current_executable() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve()
    return Path(__file__).resolve()


def format_time(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


# ============================================================
# Discord API
# ============================================================

def get_detectable_games():
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "DiscordQuestCompleter/1.0"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read().decode("utf-8")
        result = json.loads(data)

        if not isinstance(result, list):
            raise RuntimeError("Discord API вернул неожиданный формат данных.")
        return result

    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Discord API: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Не удалось подключиться к Discord API: {e.reason}")
    except json.JSONDecodeError:
        raise RuntimeError("Discord API вернул некорректный JSON.")


# ============================================================
# Поиск игры
# ============================================================

def find_games(games, game_name: str):
    query = game_name.casefold().strip()

    exact = [g for g in games if str(g.get("name", "")).casefold() == query]
    if exact:
        return exact

    return [g for g in games if query in str(g.get("name", "")).casefold()]


def select_game(games, game_name: str):
    found = find_games(games, game_name)

    if not found:
        raise RuntimeError(
            f"Игра «{game_name}» не найдена среди поддерживаемых Discord игр."
        )

    if len(found) == 1:
        return found[0]

    console.print("\n[yellow]Найдено несколько совпадений:[/yellow]\n")
    limit = min(len(found), 15)

    for index in range(limit):
        game = found[index]
        console.print(
            f"[cyan][{index}][/cyan] {game.get('name', 'Без названия')} "
            f"[dim](ID: {game.get('id', '?')})[/dim]"
        )

    while True:
        choice = console.input("\nВыберите номер [0]: ").strip()
        if not choice:
            return found[0]
        try:
            index = int(choice)
            if 0 <= index < limit:
                return found[index]
        except ValueError:
            pass
        console.print("[red]Некорректный номер.[/red]")


def get_windows_executable(game):
    executables = game.get("executables", [])
    for executable in executables:
        if executable.get("os") == "win32":
            name = executable.get("name")
            if name:
                return name.replace("/", "\\")

    raise RuntimeError(f"Для «{game.get('name', '?')}» не найден Windows EXE.")


# ============================================================
# Подготовка Dummy-файла с авто-снятием блокировок
# ============================================================

def prepare_dummy_executable(game, executable_name: str):
    game_id = str(game["id"])
    game_dir = BASE_DIR / game_id
    full_exe_path = game_dir / executable_name
    full_exe_path.parent.mkdir(parents=True, exist_ok=True)

    exe_file_name = Path(executable_name).name

    # 1. Принудительно убиваем старый зависший процесс с таким именем, если он остался в фоне
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/IM", exe_file_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(0.3)

    # 2. Если файл уже существует и мы можем его перезаписать/использовать
    if is_frozen():
        source_exe = get_current_executable()
        # Попытка удалить старый файл перед копированием
        if full_exe_path.exists():
            try:
                full_exe_path.unlink()
            except Exception:
                pass
        shutil.copy2(source_exe, full_exe_path)
    else:
        csc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
        if not csc.exists():
            csc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"

        if not csc.exists():
            raise RuntimeError("Для запуска из .py необходим csc.exe либо соберите проект в .exe через PyInstaller.")

        cs_code = f"""using System;
using System.Drawing;
using System.Windows.Forms;
class P {{
    [STAThread] static void Main() {{
        var f = new Form {{ Text = "{game.get('name', 'Game')}", Width = 420, Height = 180, FormBorderStyle = FormBorderStyle.FixedDialog, StartPosition = FormStartPosition.CenterScreen, MaximizeBox = false }};
        var l = new Label {{ Text = "{game.get('name', 'Game')}\\n\\nОкно для квеста Discord.\\nНе закрывайте до завершения таймера.", Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter, Font = new Font("Segoe UI", 10) }};
        f.Controls.Add(l);
        Application.Run(f);
    }}
}}"""
        cs_file = full_exe_path.parent / "stub.cs"
        cs_file.write_text(cs_code, encoding="utf-8")
        subprocess.run([str(csc), "/nologo", "/target:winexe", f"/out:{full_exe_path}", str(cs_file)], check=True, capture_output=True)
        cs_file.unlink(missing_ok=True)

    return game_dir, full_exe_path


# ============================================================
# Dummy Mode (Окно игры для детекта Discord)
# ============================================================

def dummy_mode(game_name: str):
    """
    Создает видимое GUI-окно на tkinter, чтобы Discord зафиксировал
    активность и позволил запустить трансляцию (стрим).
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title(game_name)
        root.geometry("420x180")
        root.resizable(False, False)

        # Центрирование окна
        root.eval('tk::PlaceWindow . center')

        lbl_title = tk.Label(root, text=game_name, font=("Segoe UI", 12, "bold"))
        lbl_title.pack(pady=(25, 5))

        lbl_desc = tk.Label(
            root,
            text="Окно для выполнения квеста Discord.\nНе закрывайте его до завершения таймера.",
            font=("Segoe UI", 10),
            fg="#444444"
        )
        lbl_desc.pack(pady=5)

        root.mainloop()
    except Exception:
        while True:
            time.sleep(1)


# ============================================================
# Управление процессом
# ============================================================

def start_dummy(exe_path: Path, game_name: str):
    cmd = [str(exe_path)]
    if is_frozen():
        cmd.extend(["--dummy", game_name])

    creation_flags = 0
    if os.name == "nt":
        creation_flags |= subprocess.CREATE_NO_WINDOW

    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags
    )


# ============================================================
# Надежная остановка всего дерева процессов
# ============================================================

def stop_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        # /T закрывает процесс и все его дочерние подпроцессы (дерево PyInstaller)
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        else:
            process.terminate()
            process.wait(timeout=2)
    except Exception:
        pass


def cleanup(game_dir):
    if not game_dir or not game_dir.exists():
        return
    try:
        shutil.rmtree(game_dir, ignore_errors=True)
    except Exception as e:
        console.print(f"[yellow]Не удалось удалить временные файлы: {e}[/yellow]")


# ============================================================
# Интерфейс Rich
# ============================================================

def build_status_panel(
    game_name: str,
    executable_name: str,
    pid: int,
    progress: Progress,
    status_api: str,
    status_game: str,
    status_process: str,
    active: bool,
    remaining: int
):
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold", width=12)
    table.add_column()

    table.add_row("Игра:", game_name)
    table.add_row("EXE:", executable_name)
    table.add_row("PID:", str(pid))
    table.add_row("", "")
    table.add_row("Статус:", "")
    table.add_row("", f"{status_api} Discord API")
    table.add_row("", f"{status_game} Игра найдена")
    table.add_row("", f"{status_process} Процесс запущен")
    table.add_row("", "")

    quest_status = (
        "[green]Квест активен[/green]"
        if active
        else "[bold green]Квест завершен![/bold green]"
    )
    table.add_row("", quest_status)
    table.add_row("", "")
    table.add_row("", progress)
    table.add_row("", "")
    table.add_row("", f"Осталось: [bold]{format_time(remaining)}[/bold]")

    return Panel(
        table,
        title="[bold]DISCORD QUEST COMPLETER[/bold]",
        border_style="bright_blue",
        padding=(1, 2)
    )


def run_timer(process, game_name, executable_name, duration):
    progress = Progress(
        TextColumn(" "),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%")
    )
    task = progress.add_task("quest", total=duration)
    start_time = time.monotonic()

    with Live(refresh_per_second=4, console=console) as live:
        while True:
            elapsed = time.monotonic() - start_time
            completed = min(elapsed, duration)
            remaining = max(0, int(duration - elapsed))

            progress.update(task, completed=completed)

            set_console_title(f"[{format_time(remaining)}] Discord Quest Completer — {game_name}")

            panel = build_status_panel(
                game_name=game_name,
                executable_name=executable_name,
                pid=process.pid,
                progress=progress,
                status_api="[green]✓[/green]",
                status_game="[green]✓[/green]",
                status_process="[green]✓[/green]",
                active=remaining > 0,
                remaining=remaining
            )

            live.update(panel)

            if remaining <= 0:
                break

            if process.poll() is not None:
                raise RuntimeError("Окно игры было закрыто пользователем до завершения таймера.")

            time.sleep(0.25)

    try:
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def show_error(message: str):
    console.print()
    console.print(
        Panel(
            Text(f"✗ {message}", style="red"),
            title="Ошибка",
            border_style="red"
        )
    )


# ============================================================
# Точка входа
# ============================================================

def main():
    set_console_title("Discord Quest Completer")

    parser = argparse.ArgumentParser(description="Discord Quest Completer")
    parser.add_argument("game", nargs="?", help="Название игры")
    parser.add_argument("--game", dest="game_option", help="Название игры")
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Длительность в секундах (по умолчанию: {DEFAULT_DURATION})"
    )

    args = parser.parse_args()
    game_name = args.game_option or args.game

    if not game_name:
        game_name = console.input("Введите название игры (например, Marvel Rivals): ").strip()

    if not game_name:
        show_error("Название игры не указано.")
        return 1

    if args.duration <= 0:
        show_error("Длительность должна быть больше 0 секунд.")
        return 1

    instance = SingleInstance(MUTEX_NAME)
    if not instance.acquire():
        console.print()
        console.print(
            Panel(
                "[yellow]Discord Quest Completer уже запущен.[/yellow]\n\n"
                "Дождитесь окончания текущей сессии.",
                title="Уже запущено",
                border_style="yellow"
            )
        )
        return 1

    game_dir = None
    process = None

    try:
        console.print("\n[cyan]Получение базы поддерживаемых игр Discord...[/cyan]")
        games = get_detectable_games()

        selected_game = select_game(games, game_name)
        selected_name = selected_game.get("name", game_name)
        executable_name = get_windows_executable(selected_game)

        game_dir, full_exe_path = prepare_dummy_executable(selected_game, executable_name)

        process = start_dummy(full_exe_path, selected_name)

        # Небольшая пауза на создание процесса
        time.sleep(0.5)

        if process.poll() is not None:
            raise RuntimeError("Процесс эмуляции игры не смог запуститься.")

        run_timer(
            process=process,
            game_name=selected_name,
            executable_name=executable_name,
            duration=args.duration
        )

        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]Квест прерван пользователем.[/yellow]")
        return 130
    except Exception as e:
        show_error(str(e))
        return 1
    finally:
        stop_process(process)
        cleanup(game_dir)
        instance.release()
        set_console_title("Discord Quest Completer")


if __name__ == "__main__":
    if "--dummy" in sys.argv:
        name = sys.argv[2] if len(sys.argv) > 2 else "Discord Quest Game"
        dummy_mode(name)
        sys.exit(0)

    sys.exit(main())