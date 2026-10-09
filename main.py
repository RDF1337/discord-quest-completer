import argparse
import ctypes
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import winreg
import winsound
from pathlib import Path

try:
    import tkinter
except ImportError:
    pass

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

# Файл кэша API (живёт 1 час)
CACHE_FILE = BASE_DIR / "_cache_detectable.json"
CACHE_TTL = 3600

# Известные соответствия для игр без EXE в Discord API
KNOWN_OVERRIDES = {
    "1445982809792708628": "AION2.exe",                    # AION 2
    "1531874756096295054": "ea sports fc 27/fc27.exe",     # EA Sports FC 27
    "451543892358201393": "aion.bin",                      # Aion (классический)
}

# Настройка кодировки UTF-8 для Windows консоли
if sys.platform == "win32":
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(legacy_windows=False)


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


def get_steam_install_path() -> Path | None:
    """Определяет путь установки Steam на компьютере пользователя."""
    reg_paths = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    ]
    for root, path, val_name in reg_paths:
        try:
            with winreg.OpenKey(root, path) as key:
                val, _ = winreg.QueryValueEx(key, val_name)
                if val and os.path.exists(val):
                    return Path(val)
        except Exception:
            pass

    default_paths = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Steam",
    ]
    for p in default_paths:
        if p.exists():
            return p
    return None


# ============================================================
# Discord IPC (Rich Presence через named pipe)
# ============================================================

class DiscordIPC:
    """
    Подключается к Discord через локальный IPC (named pipe)
    и регистрирует игровую активность (Rich Presence).
    """

    OP_HANDSHAKE = 0
    OP_FRAME = 1
    OP_CLOSE = 2

    def __init__(self, client_id: str):
        self.client_id = client_id
        self._pipe = None
        self.connected = False
        self.user_info = None

    def connect(self) -> bool:
        for i in range(10):
            pipe_path = rf"\\.\pipe\discord-ipc-{i}"
            try:
                self._pipe = open(pipe_path, "r+b", 0)
                break
            except (FileNotFoundError, OSError):
                continue

        if self._pipe is None:
            return False

        try:
            self._send(self.OP_HANDSHAKE, {
                "v": 1,
                "client_id": self.client_id
            })

            op, data = self._recv()
            if data and data.get("evt") == "READY":
                self.connected = True
                self.user_info = data.get("data", {}).get("user", {})
                return True

            self._close_pipe()
            return False

        except Exception:
            self._close_pipe()
            return False

    def set_activity(self, pid: int, game_name: str) -> bool:
        if not self.connected:
            return False

        try:
            payload = {
                "cmd": "SET_ACTIVITY",
                "args": {
                    "pid": pid,
                    "activity": {
                        "timestamps": {
                            "start": int(time.time())
                        },
                        "details": f"Playing {game_name}",
                    }
                },
                "nonce": str(time.time_ns())
            }

            self._send(self.OP_FRAME, payload)
            op, data = self._recv()
            return data is not None

        except Exception:
            return False

    def close(self):
        if self._pipe:
            try:
                self._send(self.OP_CLOSE, {})
            except Exception:
                pass
            self._close_pipe()
        self.connected = False

    def _send(self, opcode: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        header = struct.pack("<II", opcode, len(raw))
        self._pipe.write(header + raw)
        self._pipe.flush()

    def _recv(self):
        header = self._read_exact(8)
        if header is None:
            return None, None

        opcode, length = struct.unpack("<II", header)

        body = self._read_exact(length)
        if body is None:
            return opcode, None

        try:
            return opcode, json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return opcode, None

    def _read_exact(self, n: int):
        data = b""
        while len(data) < n:
            chunk = self._pipe.read(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _close_pipe(self):
        if self._pipe:
            try:
                self._pipe.close()
            except Exception:
                pass
            self._pipe = None


# ============================================================
# Discord API (с кэшированием)
# ============================================================

def get_detectable_games():
    """
    Загружает список поддерживаемых игр из Discord API.
    Результат кэшируется в файл на 1 час для ускорения повторных запусков.
    """
    if CACHE_FILE.exists():
        try:
            cache_age = time.time() - CACHE_FILE.stat().st_mtime
            if cache_age < CACHE_TTL:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    result = json.load(f)
                if isinstance(result, list) and len(result) > 1000:
                    return result
        except Exception:
            pass

    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "DiscordQuestCompleter/2.2"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read().decode("utf-8")
        result = json.loads(data)

        if not isinstance(result, list):
            raise RuntimeError("Discord API вернул неожиданный формат данных.")

        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
        except Exception:
            pass

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

def _score_match(game_name_lower: str, query: str) -> int:
    """
    Возвращает приоритет совпадения (меньше = лучше):
      0 — точное совпадение
      1 — начинается с запроса
      2 — содержит запрос как подстроку
      3 — все слова запроса присутствуют в названии (в порядке)
      -1 — нет совпадения
    """
    name = game_name_lower
    if name == query:
        return 0
    if name.startswith(query):
        return 1
    if query in name:
        return 2

    query_tokens = query.split()
    if len(query_tokens) >= 2:
        pos = 0
        matched = True
        for token in query_tokens:
            idx = name.find(token, pos)
            if idx < 0:
                matched = False
                break
            pos = idx + len(token)
        if matched:
            return 3

    return -1


def find_games(games, game_name: str):
    """Поиск игр с ранжированием."""
    query = game_name.casefold().strip()

    scored = []
    for g in games:
        name = str(g.get("name", "")).casefold()
        score = _score_match(name, query)

        if score < 0:
            for alias in g.get("aliases", []):
                alias_score = _score_match(str(alias).casefold(), query)
                if alias_score >= 0:
                    score = alias_score + 5
                    break

        if score >= 0:
            scored.append((score, g))

    scored.sort(key=lambda x: (x[0], len(str(x[1].get("name", "")))))
    return [g for _, g in scored]


def select_game(games, game_name: str):
    found = find_games(games, game_name)

    if not found:
        raise RuntimeError(
            f"Игра «{game_name}» не найдена среди поддерживаемых Discord игр.\n"
            f"  Подсказка: попробуйте другое написание или часть названия."
        )

    if len(found) == 1:
        return found[0]

    if str(found[0].get("name", "")).casefold() == game_name.casefold().strip():
        return found[0]

    console.print("\n[yellow]Найдено несколько совпадений:[/yellow]\n")
    limit = min(len(found), 15)

    for index in range(limit):
        game = found[index]
        has_exe = "✓" if game.get("executables") else "✗"
        exe_color = "green" if game.get("executables") else "yellow"
        console.print(
            f"[cyan][{index}][/cyan] {game.get('name', 'Без названия')} "
            f"[{exe_color}][EXE: {has_exe}][/{exe_color}] "
            f"[dim](ID: {game.get('id', '?')})[/dim]"
        )

    if len(found) > limit:
        console.print(f"[dim]  ...и ещё {len(found) - limit} результатов[/dim]")

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


# ============================================================
# Интеллектуальный поиск и генерация Executable
# ============================================================

def infer_franchise_exe(game_name: str, all_games: list) -> str | None:
    """
    Если игра с номером/годом (например, 'EA Sports FC 27'),
    ищет предыдущую часть (26, 25...) и адаптирует паттерн имени EXE.
    """
    m = re.search(r"(\d+)\s*$", game_name)
    if not m:
        return None
    num_str = m.group(1)
    num = int(num_str)
    prefix = game_name[:m.start()].strip().lower()

    for prev_num in range(num - 1, max(0, num - 5), -1):
        prev_name = f"{prefix} {prev_num}"
        for g in all_games:
            if g.get("name", "").lower() == prev_name:
                execs = [
                    e.get("name") for e in g.get("executables", [])
                    if e.get("os") == "win32" and not e.get("is_launcher")
                ]
                if execs:
                    # Предпочитаем не-trial и не-showcase
                    clean_execs = [e for e in execs if "trial" not in e.lower() and "showcase" not in e.lower()]
                    best = clean_execs[0] if clean_execs else execs[0]
                    return best.replace(str(prev_num), num_str)
    return None


def get_windows_executable(game: dict, all_games: list = None, custom_exe: str = None) -> tuple[str, str]:
    """
    Определяет имя исполняемого файла для игры.
    Возвращает (executable_name, source_type).
    source_type может быть:
      - 'custom': указан вручную
      - 'api': найден в официальном API Discord
      - 'known': найден в таблице известных соответствий
      - 'franchise': выведен из предыдущих частей серии
      - 'steam': сформирован из данных Steam SKU
      - 'generated': сгенерирован автоматически
    """
    if custom_exe:
        return custom_exe.replace("/", "\\"), "custom"

    game_id = str(game.get("id", ""))
    game_name = game.get("name", "Game")

    # 1. Проверяем таблицу известных игр
    if game_id in KNOWN_OVERRIDES:
        return KNOWN_OVERRIDES[game_id].replace("/", "\\"), "known"

    # 2. Проверяем официальные executables из Discord API
    executables = game.get("executables", [])
    win_execs = [e for e in executables if e.get("os") == "win32"]
    GENERIC_NAMES = {"javaw.exe", "java.exe", "python.exe", "pythonw.exe", "node.exe"}

    valid_execs = []
    for e in win_execs:
        name = e.get("name", "")
        if name.startswith(">"):
            continue
        basename = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if basename in GENERIC_NAMES:
            continue
        valid_execs.append(e)

    if valid_execs:
        non_launcher = [e for e in valid_execs if not e.get("is_launcher")]
        chosen = non_launcher[0] if non_launcher else valid_execs[0]
        name = chosen.get("name")
        if name:
            return name.replace("/", "\\"), "api"

    # 3. Пытаемся вывести паттерн из франшизы
    if all_games:
        inferred = infer_franchise_exe(game_name, all_games)
        if inferred:
            return inferred.replace("/", "\\"), "franchise"

    # 4. Если игра из Steam (third_party_skus), формируем аккуратное имя
    skus = game.get("third_party_skus", [])
    has_steam = any(s.get("distributor") == "steam" for s in skus)
    if has_steam:
        clean = re.sub(r"[^a-zA-Z0-9]", "", game_name)
        if clean:
            return f"{clean}.exe", "steam"

    # 5. Обычная генерация
    clean = re.sub(r"[^a-zA-Z0-9]", "", game_name)
    if clean:
        return f"{clean}.exe", "generated"
    return "game.exe", "generated"


# ============================================================
# Подготовка Dummy-файла
# ============================================================

def prepare_dummy_executable(game: dict, executable_name: str) -> tuple[Path, Path, bool]:
    """
    Подготавливает директорию и исполняемый файл для эмуляции игры.
    Если у игры есть Steam SKU и Steam установлен, пытается создать
    структуру Steam-библиотеки для нативного детектирования.
    Возвращает (game_dir, full_exe_path, is_steam_integration).
    """
    game_id = str(game["id"])
    game_name = game.get("name", "Game")
    steam_path = get_steam_install_path()

    # Проверяем, есть ли Steam SKU
    steam_sku = None
    for s in game.get("third_party_skus", []):
        if s.get("distributor") == "steam" and s.get("id"):
            steam_sku = s.get("id")
            break

    use_steam_integration = False
    steam_created_files = []

    # Если Steam установлен и есть AppID — можно создать файл в steamapps/common
    if steam_path and steam_sku:
        try:
            steam_common = steam_path / "steamapps" / "common" / game_name
            steam_common.mkdir(parents=True, exist_ok=True)
            manifest = steam_path / "steamapps" / f"appmanifest_{steam_sku}.acf"
            if not manifest.exists():
                manifest_content = (
                    f'"AppState"\n{{\n\t"appid"\t\t"{steam_sku}"\n\t"name"\t\t"{game_name}"\n'
                    f'\t"installdir"\t\t"{game_name}"\n\t"StateFlags"\t\t"4"\n}}\n'
                )
                manifest.write_text(manifest_content, encoding="utf-8")
                steam_created_files.append(manifest)

            game_dir = steam_common
            full_exe_path = game_dir / Path(executable_name).name
            use_steam_integration = True
        except Exception:
            use_steam_integration = False

    if not use_steam_integration:
        game_dir = BASE_DIR / game_id
        full_exe_path = game_dir / executable_name
        full_exe_path.parent.mkdir(parents=True, exist_ok=True)

    exe_file_name = Path(executable_name).name

    # Принудительно убиваем старый процесс с таким именем
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/IM", exe_file_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(0.3)

    if is_frozen():
        source_exe = get_current_executable()
        if full_exe_path.exists():
            try:
                full_exe_path.unlink()
            except Exception:
                pass
        shutil.copy2(source_exe, full_exe_path)
    else:
        _compile_dummy_cs(game, full_exe_path)

    # Небольшая пауза для антивируса
    time.sleep(0.3)

    return game_dir, full_exe_path, use_steam_integration


def _compile_dummy_cs(game: dict, full_exe_path: Path):
    """Компилирует видимый нативный GUI-exe через .NET csc.exe."""
    csc = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    )
    if not csc.exists():
        csc = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"
        )

    if not csc.exists():
        raise RuntimeError(
            "Для запуска из исходников необходим csc.exe (.NET Framework 4.0+).\n"
            "Либо скомпилируйте проект в standalone .exe."
        )

    safe_name = game.get("name", "Game").replace('"', "'")
    cs_code = f"""using System;
using System.Drawing;
using System.Windows.Forms;
class P {{
    [STAThread] static void Main() {{
        var f = new Form {{
            Text = "{safe_name}",
            Width = 460, Height = 200,
            FormBorderStyle = FormBorderStyle.FixedDialog,
            StartPosition = FormStartPosition.CenterScreen,
            MaximizeBox = false,
            TopMost = false
        }};
        var l = new Label {{
            Text = "{safe_name}\\n\\n" +
                   "Окно для выполнения квеста Discord.\\n" +
                   "Не закрывайте его до завершения таймера!",
            Dock = DockStyle.Fill,
            TextAlign = ContentAlignment.MiddleCenter,
            Font = new Font("Segoe UI", 11)
        }};
        f.Controls.Add(l);
        Application.Run(f);
    }}
}}"""

    cs_file = full_exe_path.parent / "stub.cs"
    cs_file.write_text(cs_code, encoding="utf-8")

    result = subprocess.run(
        [str(csc), "/nologo", "/target:winexe", f"/out:{full_exe_path}", str(cs_file)],
        capture_output=True,
        text=True
    )
    cs_file.unlink(missing_ok=True)

    if result.returncode != 0:
        raise RuntimeError(f"Ошибка компиляции dummy-exe:\n{result.stderr}")


# ============================================================
# Dummy Mode (Окно игры для детекта Discord)
# ============================================================

def dummy_mode(game_name: str):
    """
    Создаёт видимое GUI-окно на tkinter (для frozen standalone-сборки).
    """
    if os.name == "nt":
        try:
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
        except Exception:
            pass

    try:
        import tkinter as tk
        root = tk.Tk()
        root.title(game_name)
        root.geometry("460x200")
        root.resizable(False, False)
        root.eval("tk::PlaceWindow . center")

        lbl_title = tk.Label(root, text=f"{game_name}", font=("Segoe UI", 13, "bold"))
        lbl_title.pack(pady=(25, 5))

        lbl_desc = tk.Label(
            root,
            text="Окно для выполнения квеста Discord.\nНе закрывайте его до завершения таймера!",
            font=("Segoe UI", 10),
            fg="#333333"
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
    """Запускает процесс эмуляции игры с реальным видимым окном."""
    cmd = [str(exe_path)]
    if is_frozen():
        cmd.extend(["--dummy", game_name])

    # Запускаем без скрытия окна — окно должно быть видимым для стрима и захвата Discord
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    try:
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


def cleanup(game_dir: Path):
    if not game_dir or not game_dir.exists():
        return

    time.sleep(0.5)

    for _ in range(3):
        try:
            shutil.rmtree(game_dir, ignore_errors=False)
            return
        except Exception:
            time.sleep(0.5)

    shutil.rmtree(game_dir, ignore_errors=True)


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
    ipc_connected: bool,
    exe_source: str,
    active: bool,
    remaining: int
):
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold", width=16)
    table.add_column()

    table.add_row("Игра:", f"[bold cyan]{game_name}[/bold cyan]")

    source_labels = {
        "api": "[dim](Discord API)[/dim]",
        "known": "[dim](база)[/dim]",
        "franchise": "[dim](серия)[/dim]",
        "steam": "[dim](Steam SKU)[/dim]",
        "custom": "[dim](ручной)[/dim]",
        "generated": "[dim](авто)[/dim]"
    }
    exe_display = f"{executable_name} {source_labels.get(exe_source, '')}"
    table.add_row("EXE:", exe_display)
    table.add_row("PID:", str(pid))
    table.add_row("", "")

    table.add_row("Статус:", "")
    table.add_row("", f"{status_api} База игр Discord")
    table.add_row("", f"{status_game} Игра определена")
    table.add_row("", f"{status_process} Процесс эмуляции запущен")

    if ipc_connected:
        table.add_row("", f"[green]✓[/green] Discord статус: [bold green]🎮 Играет в {game_name}[/bold green]")
    else:
        table.add_row("", f"[yellow]~[/yellow] Discord статус: [dim]фоновое отслеживание[/dim]")

    table.add_row("", "")

    quest_status = (
        "[bold green]▶ Квест активен (время учитывается)[/bold green]"
        if active
        else "[bold green]✓ Квест успешно завершён![/bold green]"
    )
    table.add_row("", quest_status)
    table.add_row("", "")
    table.add_row("", progress)
    table.add_row("", "")
    table.add_row("", f"Осталось: [bold]{format_time(remaining)}[/bold]")

    # Компактная подсказка только если статус не был выставлен автоматически
    if not ipc_connected:
        table.add_row("", "")
        table.add_row("", "[dim]💡 Если таймер в Discord не идёт: Настройки ⚙️ → Зарегистрированные игры → Добавьте её![/dim]")

    return Panel(
        table,
        title="[bold]DISCORD QUEST COMPLETER[/bold]",
        border_style="bright_blue",
        padding=(1, 2)
    )


def run_timer(process, game_name, executable_name, duration, exe_source, ipc_connected):
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

            set_console_title(
                f"[{format_time(remaining)}] Discord Quest Completer — {game_name}"
            )

            panel = build_status_panel(
                game_name=game_name,
                executable_name=executable_name,
                pid=process.pid,
                progress=progress,
                status_api="[green]✓[/green]",
                status_game="[green]✓[/green]",
                status_process="[green]✓[/green]",
                ipc_connected=ipc_connected,
                exe_source=exe_source,
                active=remaining > 0,
                remaining=remaining
            )

            live.update(panel)

            if remaining <= 0:
                break

            if process.poll() is not None:
                raise RuntimeError(
                    "Окно игры было закрыто до завершения таймера.\n"
                    "Перезапустите программу и не закрывайте окно игры."
                )

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


def show_banner():
    banner = Text()
    banner.append("DISCORD QUEST COMPLETER", style="bold bright_blue")
    banner.append(" v2.1.0", style="bold green")

    console.print()
    console.print(
        Panel(
            banner,
            subtitle="[dim]Безопасное выполнение квестов без модификации клиента Discord[/dim]",
            border_style="bright_blue",
            padding=(0, 2)
        )
    )
    console.print()


# ============================================================
# Точка входа
# ============================================================

def main():
    set_console_title("Discord Quest Completer")
    show_banner()

    parser = argparse.ArgumentParser(description="Discord Quest Completer")
    parser.add_argument("game", nargs="?", help="Название игры")
    parser.add_argument("--game", dest="game_option", help="Название игры")
    parser.add_argument("--exe", dest="exe_override", help="Принудительное имя исполняемого файла (например, AION2.exe)")
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Длительность в секундах (по умолчанию: {DEFAULT_DURATION})"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Показать список доступных игр"
    )
    parser.add_argument(
        "--no-ipc",
        action="store_true",
        help="Отключить Discord IPC"
    )

    args = parser.parse_args()

    if args.list:
        console.print("[cyan]Загрузка базы игр...[/cyan]")
        games = get_detectable_games()
        with_exe = [g for g in games if g.get("executables")]
        console.print(f"\n[green]Игр с Windows EXE: {len(with_exe)}[/green]")
        console.print(f"[yellow]Игр с расширенным определением (SKU/Франшиза): {len(games) - len(with_exe)}[/yellow]\n")
        for g in with_exe[:50]:
            console.print(f"  • {g.get('name')}")
        if len(with_exe) > 50:
            console.print(f"  [dim]...и ещё {len(with_exe) - 50}[/dim]")
        return 0

    game_name = args.game_option or args.game

    if not game_name:
        game_name = console.input(
            "[bold]Введите название игры[/bold] (например, Marvel Rivals): "
        ).strip()

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
    ipc = None

    try:
        console.print("[cyan]Загрузка базы поддерживаемых игр Discord...[/cyan]")
        games = get_detectable_games()
        console.print(f"[dim]Загружено {len(games)} игр[/dim]\n")

        selected_game = select_game(games, game_name)
        selected_name = selected_game.get("name", game_name)
        game_id = str(selected_game.get("id", ""))

        executable_name, exe_source = get_windows_executable(
            selected_game,
            all_games=games,
            custom_exe=args.exe_override
        )

        console.print(f"[green]✓[/green] Выбрана игра: [bold]{selected_name}[/bold]")
        console.print(f"[green]✓[/green] Executable: [bold]{executable_name}[/bold] [dim]({exe_source})[/dim]")
        console.print(f"[green]✓[/green] Длительность: [bold]{format_time(args.duration)}[/bold]\n")

        # ── Подготовка dummy-файла ──
        game_dir, full_exe_path, is_steam = prepare_dummy_executable(
            selected_game, executable_name
        )

        # ── Запуск процесса эмуляции ──
        process = start_dummy(full_exe_path, selected_name)
        time.sleep(0.5)

        if process.poll() is not None:
            raise RuntimeError("Процесс эмуляции игры не смог запуститься.")

        # ── Подключение к Discord IPC ──
        ipc_connected = False
        if not args.no_ipc and game_id:
            try:
                ipc = DiscordIPC(client_id=game_id)
                if ipc.connect():
                    ipc.set_activity(pid=process.pid, game_name=selected_name)
                    ipc_connected = True
            except Exception:
                ipc_connected = False

        # ── Подтверждение статуса ──
        if ipc_connected:
            console.print(f"[green]✓ Discord обнаружил игру:[/green] [bold green]🎮 Играет в {selected_name}[/bold green]\n")
        elif exe_source != "api" and not is_steam:
            console.print(f"[yellow]💡 Если Discord не отобразит статус: Настройки ⚙️ → Зарегистрированные игры → Добавьте её![/yellow]\n")

        # ── Запуск таймера ──
        run_timer(
            process=process,
            game_name=selected_name,
            executable_name=executable_name,
            duration=args.duration,
            exe_source=exe_source,
            ipc_connected=ipc_connected
        )

        console.print()
        console.print(
            Panel(
                "[bold green]✓ Квест успешно завершён![/bold green]\n\n"
                "Откройте Discord → Настройки → Инвентарь подарков\n"
                "и заберите вашу награду.",
                title="Готово",
                border_style="green",
                padding=(1, 2)
            )
        )

        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]Квест прерван пользователем.[/yellow]")
        return 130
    except Exception as e:
        show_error(str(e))
        return 1
    finally:
        if ipc:
            ipc.close()
        stop_process(process)
        cleanup(game_dir)
        instance.release()
        set_console_title("Discord Quest Completer")


if __name__ == "__main__":
    if "--dummy" in sys.argv:
        try:
            dummy_idx = sys.argv.index("--dummy")
            name = sys.argv[dummy_idx + 1] if len(sys.argv) > dummy_idx + 1 else "Discord Quest Game"
        except Exception:
            name = "Discord Quest Game"
        dummy_mode(name)
        sys.exit(0)

    sys.exit(main())
