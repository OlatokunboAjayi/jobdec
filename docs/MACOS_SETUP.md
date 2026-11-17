# macOS Environment & Execution Guide for Job Deck

This guide covers setting up, isolating, and running Job Deck in a dedicated macOS environment on any MacBook or Mac desktop (Apple Silicon M1/M2/M3/M4 or Intel).

---

## 1. Why a Separate Environment for macOS?
When working across operating systems or sharing repositories via cloud storage (OneDrive, iCloud, Git), Python virtual environments (`.venv`) contain platform-specific binaries, C-extensions, and compiled wheels (e.g. Windows `.exe`/`.pyd` vs macOS `.dylib`/Mach-O binaries).

Job Deck isolates the macOS environment inside **`.venv-mac`** with dedicated dependencies specified in **`requirements-mac.txt`**, ensuring:
- **Zero OS collision**: Windows and Mac virtual environments live side-by-side without corrupting each other.
- **Apple Silicon Acceleration**: Automatically configures PyTorch and neural models to take advantage of Apple Metal Performance Shaders (MPS).
- **Native macOS Bridges**: Unlocks direct AppleScript (`osascript`) integration with the macOS Messages app.

---

## 2. Quick Start: One-Command Setup

Prerequisites: Python 3.10+ (`brew install python`) and git (`xcode-select --install`).

In Terminal:

```bash
git clone https://github.com/OlatokunboAjayi/jobdec.git
cd jobdec
./setup_mac.sh                       # if macOS says "permission denied": bash setup_mac.sh
source .venv-mac/bin/activate
python setup_wizard.py --new <your-name>   # creates profiles/<your-name>/ (.env, profile.json, resumes/)
export JOBDEC_HOME=profiles/<your-name>    # add this line to ~/.zshrc so every run uses your account
```

Then put your resume PDF in `profiles/<your-name>/resumes/`, and your Telegram bot token, chat ID and AI key
in `profiles/<your-name>/.env` (never commit that file).

### What `setup_mac.sh` Does:
1. Detects your CPU architecture (`arm64` Apple Silicon or `x86_64` Intel).
2. Verifies Python 3.10+ is installed.
3. Creates a dedicated `.venv-mac/` virtual environment.
4. Installs the exact macOS dependencies from `requirements-mac.txt`.
5. Installs the Playwright Chromium browser binary for macOS (`playwright install chromium`).
6. Configures executable permissions on all Mac helper scripts in `scripts/mac/`.
7. Checks native `osascript` availability for the Apple iMessage bridge.

---

## 3. Dedicated macOS Helper Scripts (`scripts/mac/`)

All scripts automatically activate `.venv-mac` on launch:

| Script | Command | Purpose |
| :--- | :--- | :--- |
| **Interactive Onboarding** | `python setup_wizard.py` | Step-by-step wizard to configure profile, .env, and ATS rules |
| **Telegram & iMessage Bot** | `./scripts/mac/bot.sh` | 24/7 command center, AI assistant, watchdog supervisor |
| **Parallel Batch Runner** | `./scripts/mac/batch_apply.sh` | Launches multi-worker parallel application engine |
| **Daily Pipeline Run** | `./scripts/mac/daily_run.sh` | Inbox scan -> Scout -> Apply -> Fix -> Briefing |
| **Continuous Target Run** | `./scripts/mac/run_pipeline.sh`| Keeps applying until target submission count is reached |
| **Single Job Apply** | `./scripts/mac/apply.sh <URL>` | Direct application test for a single job posting |
| **Emergency Stop** | `./scripts/mac/stop_all.sh` | Gracefully terminates all active Job Deck processes |

---

## 4. Native Apple iMessage Integration

When running on macOS, Job Deck's `agents/imessage_bridge.py` leverages macOS's native `osascript` interface to communicate with Apple Messages:

1. **Permissions**: When running for the first time, macOS may display a system prompt asking to allow **Terminal** (or iTerm2) to control **Messages**. Click **Allow**.
2. **Configuration** (`.env`):
   ```ini
   IMESSAGE_ENABLED=true
   IMESSAGE_TARGET_HANDLE=+1XXXXXXXXXX   # Or your Apple ID email
   ```
3. Critical updates (interviews, recruiter replies, offers) will automatically be delivered as native blue-bubble iMessages.

---

## 5. Running 24/7 in Background on macOS

To keep the Telegram control bot or continuous runner active when you close your MacBook terminal window:

### Option A: Using `tmux` or `screen` (Recommended)
```bash
# Start a new tmux session
tmux new -s jobdeck

# Start the bot
./scripts/mac/bot.sh

# Detach with: Ctrl+B then D
# Reattach anytime with: tmux attach -t jobdeck
```

`bot.sh` and `run_pipeline.sh` run under `caffeinate -i`, so the Mac won't idle-sleep while they run.
Closing the lid still sleeps a MacBook on battery: keep it open, or plugged in with an external display.

### Option B: macOS Background Process (`nohup`)
```bash
nohup ./scripts/mac/bot.sh > logs/bot.log 2>&1 &
```
