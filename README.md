# Discord Quest Completer

A lightweight, standalone Windows CLI utility designed to safely complete Discord Quests without downloading full games or injecting into the Discord client.

<p align="center">
  <img src="preview.png" alt="Discord Quest Completer Preview" width="100%">
</p>

---

### Why is this method safer than DevTools console scripts?

Old JavaScript snippets pasted into DevTools (`Ctrl + Shift + I`) sent spoofed heartbeat and API requests directly through the user's active session. This regularly triggered Discord's automated abuse detection, leading to *"Quest Activity Notice"* warnings or account quest restrictions.

> **How this tool works:**  
> This application does **not** touch Discord's memory, client files, or internal private APIs. It queries Discord's official public detectable games directory (`/api/v9/applications/detectable`), replicates the verified directory structure, and spawns a native GUI dummy window. Discord's desktop client detects it as a genuine running game, enabling both **Play** and **Stream** quest progression without risk.

---

### Features

* **Zero Dependencies:** Fully portable standalone `.exe` — no Python, .NET SDK, or PowerShell script execution rights required.
* **Stream & Play Support:** Generates an authentic, centered GUI game window with taskbar presence that can be streamed directly into voice channels.
* **Discord IPC & Steam Integration:** Dispatches native `SET_ACTIVITY` Rich Presence payloads and writes Steam library manifests (`appmanifest_<id>.acf`) for games cataloged on Steam.
* **Intelligent EXE Resolution:** Automatically infers franchise paths (e.g., predicting `fc27.exe` from prior *EA Sports FC* releases), filters runtime wrappers (`javaw.exe`, `python.exe`), and synthesizes clean fallback binaries.
* **Token-Based Fuzzy Search:** Fast search matching ordered keywords (e.g., typing `ea fc 27` instantly resolves `EA Sports FC 27`).
* **1-Hour API Cache:** Caches Discord's ~24,000 detectable titles locally, making subsequent launches instantaneous.
* **Rich Dashboard:** Interactive terminal UI featuring real-time progress, PID monitoring, and API badges.
* **Dynamic Window Title:** Displays the remaining time directly in the taskbar / terminal tab title.
* **Auto-Termination & Grace Buffer:** Automatically shuts down the dummy process and cleans up temporary files after 15 minutes and 15 seconds (including a latency buffer).
* **Process Tree Cleanup:** Multi-stage termination prevents orphan background processes and file lock errors (`[WinError 32]`).

---

### Requirements

* **Windows 10 or Windows 11** (64-bit).
* **Discord Desktop Client** for Windows.

---

### How to Use

1. **Accept the Quest:**  
   Open Discord, go to **User Settings → Quests** (or the **Discover → Quests** tab), and click **Accept Quest**.

2. **Download & Run:**  
   Grab the latest `DiscordQuestCompleter.exe` from the **[Releases](../../releases)** section and run it (either double-click or launch via terminal).

3. **Select the Game:**  
   Type the game name when prompted (e.g., `Apex Legends`, `Marvel Rivals`, or `Aniimo`). The tool will resolve the game ID and executable name from Discord's directory and launch the dummy window.

4. **Complete the Quest:**
   * **Play on Desktop:** Leave the spawned game window open. Discord will automatically track your playtime.
   * **Stream on Desktop:** Join a voice channel with at least one other participant. Start screen sharing and choose the **game window** specifically (do not share the entire desktop).

5. **Finish:**  
   Once the timer runs down, an audio chime will notify you. The application automatically closes the dummy window and wipes temporary data from `%TEMP%`.

6. **Claim Reward:**  
   Head over to **User Settings → Gift Inventory** (or the Quests tab) in Discord and claim your reward.

---

### Troubleshooting & Manual Game Detection

If Discord does not attach to the game within the first 10 seconds:
1. Open Discord **User Settings ⚙️** → **Registered Games** (or **Game Activity**).
2. Click **"Not seeing your game? Add it!"**.
3. Select the opened game window from the dropdown menu and click **Add Game**.
4. Discord will bind the window to the official entry and progress the quest timer immediately.

---

### CLI Options

You can also pass arguments directly (useful for desktop shortcuts):

```cmd
:: Search and launch by title
DiscordQuestCompleter.exe "Apex Legends"

:: Custom duration (in seconds)
DiscordQuestCompleter.exe --game "Marvel Rivals" --duration 915

:: Override executable name manually
DiscordQuestCompleter.exe "AION 2" --exe AION2.exe

:: List matching games without launching
DiscordQuestCompleter.exe --list "EA Sports"
```

---
### Disclaimer of Liability

This project is intended strictly for educational and personal utility purposes.

* **"AS IS" Warranty:** The software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement.
* **No Affiliation:** This project is not affiliated with, maintained, authorized, sponsored, or endorsed by Discord Inc. or any of its affiliates.
* **Limitation of Liability:** In no event shall the authors or copyright holders be liable for any claim, damages, account penalties, restrictions, or other liabilities arising from, out of, or in connection with the software or the use or other dealings in the software. You use this tool at your own discretion and risk.
