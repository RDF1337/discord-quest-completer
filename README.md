# Discord Quest Сompleter

A lightweight PowerShell script designed to safely complete Discord Quests without downloading full game files or injecting into the Discord client.

---

### Preview
<p align="center">
  <img src="preview.png" alt="Preview" width="85%">
</p>

---

### Why is this method safer than DevTools console scripts?

Old JavaScript snippets executed via DevTools (`Ctrl + Shift + I`) sent spoofed heartbeat and API requests directly through the user's Discord session. This frequently triggered Discord's automated abuse detection, resulting in *"Quest Activity Notice"* warnings or account-level quest suspensions.

> **How this tool works:**  
> This script does **not** touch Discord's memory, client files, or internal APIs. Instead, it queries Discord's official public list of detectable applications (`detectable`), compiles a minimal dummy window on the fly using the built-in Windows C# compiler (`csc.exe`), and runs it locally. To Discord's native desktop game scanner, it appears as a 100% legitimate running game.

---

### Requirements

* **Discord Desktop Client** for Windows.
* **Windows 10 or Windows 11** (built-in PowerShell and .NET Framework are sufficient out of the box).

---

### How to Use

1. **Accept the Quest:**  
   Open Discord, navigate to **User Settings → Quests** (or the **Discover → Quests** tab), and click **Accept Quest**.

1. [**Download the Script**](https://github.com/RDF1337/discord-quest-completer/releases/download/v1.0.0/discord-quest.ps1)

3. **Run the Script:**  
   Open PowerShell in the folder containing the script and run:
   ```powershell
   .\discord-quest.ps1
   
4. **Select the Game:**  
   Enter the game name when prompted (e.g., `Aniimo` or `Marvel Rivals`). The script will fetch the application details from Discord's database and launch a small dummy window.

5. **Complete the Quest:**
   * **Play on Desktop:** Leave the dummy game window open for 15 minutes. Discord will track your activity automatically.
   * **Stream on Desktop:** Join a voice channel with at least one friend or alt account. Start screen sharing and select the **dummy game window** specifically (do not stream the entire screen).

6. **Clean Up:**  
   Once Discord notifies you that the quest is complete (100% progress), return to the PowerShell window and press **Enter**. The script will automatically terminate the dummy process and remove temporary files from `%TEMP%`.

7. **Claim Reward:**  
   Go to **User Settings → Gift Inventory** (or the Quests tab) and claim your reward.

---

### Disclaimer of Liability

This project is intended strictly for educational and personal utility purposes.

* **"AS IS" Warranty:** The software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement.
* **No Affiliation:** This project is not affiliated with, maintained, authorized, sponsored, or endorsed by Discord Inc. or any of its affiliates.
* **Limitation of Liability:** In no event shall the authors or copyright holders be liable for any claim, damages, account penalties, restrictions, or other liabilities arising from, out of, or in connection with the software or the use or other dealings in the software. You use this tool at your own discretion and risk.
