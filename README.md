# NOTAMv1 — NOTAM Briefing Tool

A local web application for pilots and air traffic controllers to fetch, filter, and review NOTAMs for a planned route. NOTAMs are retrieved from [autorouter.aero](https://www.autorouter.aero) and presented as a structured, printable briefing with map plotting, snapshot diff tracking, smart filtering, AI-generated summaries, AZBA/RTBA activation status, and GNSS RAIM outage predictions.

---

## Table of contents

- [Features](#features)
- [Platform status](#platform-status)
- [Setup by platform](#setup-by-platform)
  - [Linux](#linux)
  - [macOS](#macos)
  - [Windows](#windows)
  - [Android (Termux)](#android-termux)
  - [iPad / iPhone — LAN access](#ipad-iphone-lan-access)
  - [iPad / iPhone — standalone with a-Shell](#ipad-iphone-standalone-with-a-shell)
- [Daily use](#daily-use)
  - [Updating to the latest version](#updating-to-the-latest-version)
- [The briefing interface](#the-briefing-interface)
  - [Route — ICAO codes](#route-icao-codes)
  - [Route variants](#route-variants)
  - [Date/time lock](#datetime-lock)
  - [AI NOTAM summary](#ai-notam-summary)
  - [Fetch NOTAMs](#fetch-notams)
  - [Never-show list](#never-show-list)
  - [Flush snapshot](#flush-snapshot)
  - [Approach equipment filter](#approach-equipment-filter)
- [Cross-device snapshot sync](#cross-device-snapshot-sync)
  - [Advanced note — sync lifecycle](#advanced-note-sync-lifecycle)
- [Setting up AI NOTAM summaries](#setting-up-ai-notam-summaries)
- [Setting up AZBA/RTBA zone data](#setting-up-azbartba-zone-data)
- [Setting up AZBA activation schedule](#setting-up-azba-activation-schedule)
- [Setting up GNSS RAIM predictions](#setting-up-gnss-raim-predictions)
  - [Per-airport mask angle overrides](#per-airport-mask-angle-overrides)
- [Reference](#reference)
  - [Prerequisites](#prerequisites)
  - [Folder structure](#folder-structure)
  - [Security note](#security-note)
- [Acknowledgements](#acknowledgements)
- [Contact](#contact)

---

## Features

- **Route-based NOTAM fetch** — departure, destination, alternates, waypoints, and additional airfields
- **Smart filtering** — by flight rules (IFR/VFR), aircraft category, cruise FL, approach equipment (LPV / LNAV/VNAV / LNAV), obstacle threshold, and altitude band
- **Route variants** — maintain independent Left, Straight, and Right routing options for the same DEP/DEST pair, each with its own NOTAM snapshot and waypoint list
- **Snapshot diff system** — NEW and GONE markers highlight changes since your last briefing; acknowledgeable per NOTAM
- **Never-show list** — permanently suppress NOTAMs that are irrelevant to your operation
- **Map plotting** — obstacles (with per-point height labels for multi-point surveys), restricted zones (including unpublished-dimension zones, shown dashed), circles, PJE/parachute drop zones, arc polygons, and cable car lines on an interactive map; fullscreen map tab for mobile
- **Map styles** — Standard (CartoCDN), Relief (Esri shaded relief), Satellite (Esri), and openAIP (OSM base + aviation overlay showing airspaces, airports and navaids)
- **Per-NOTAM map view** — click the 📍 map badge on any NOTAM to open the fullscreen map centred and zoomed on that NOTAM's geometry
- **AZBA/RTBA integration** — French military low-altitude training zone polygons plotted on the map with activation status from the SIA SOFIA API; active zones shown in red, inactive in blue; click any zone for its exact activation time slots; a dedicated status card in the briefing lists all zones active during the planned flight window, with a 📍 map badge opening the fullscreen map centred on active zones
- **GNSS RAIM outage predictions** — for IFR flights, checks the EUROCONTROL AUGUR API for predicted RAIM outages at all route airports within the planned flight window; algorithm FDE, procedure LNAV/VNAV (RNP 0.3), baro aiding linked to the LNAV/VNAV equipment checkbox; per-airport mask angle overrides for mountainous terrain (requires AUGUR account — free for pilots)
- **AI NOTAM summary** — concise per-ICAO plain-language summaries in pilot shorthand, aware of your approach equipment, time-limited NOTAMs, and acknowledged NEW/GONE changes, generated via the Anthropic API (optional, configurable directly in the app)
- **PDF export** — printable briefing with map rasterisation (including AZBA zone labels), working on all platforms including iPad/iPhone
- **Cross-device sync** — snapshot files synced automatically to a private GitHub repository; preferences synced separately
- **LAN access** — use the briefing from any device on your network (iPad, iPhone, another computer) via Safari/Chrome, no installation needed on the client device
- **Standalone on iPhone/iPad** — run the entire app on-device with a-Shell, no separate computer needed (experimental, see below)

---

## Platform status

| Platform | Status |
|---|---|
| Linux | ✅ Fully working |
| macOS (Big Sur 11+) | ✅ Fully working |
| Windows | ✅ Fully working |
| Android (Termux) | ✅ Fully working |
| iPad / iPhone — LAN access (iOS 15.8–16.7+) | ✅ Fully working |
| iPad / iPhone — standalone (a-Shell) | ✅ Working well — one-tap Shortcuts launch confirmed; requires keeping a-Shell visible on screen |

---

## Setup by platform

Pick your platform below. Each one ends with the same result: a server running at `http://localhost:8766`, ready to fetch NOTAMs.

### Linux

1. Clone the repository and enter it:

   ```bash
   git clone https://github.com/amelingu/NOTAMv1.git
   cd NOTAMv1
   ```

2. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Launch:

   ```bash
   ./bin/linux/Notam_linux.sh
   ```

On first launch, you'll be asked for your autorouter.aero email and password — these are written obfuscated to `config.py`, which stays on your machine and is never committed to git. The browser opens automatically at `http://localhost:8766`.

**Optional — run from the project root:** if typing `./bin/linux/Notam_linux.sh` each time feels long, create a symlink once:

```bash
ln -s bin/linux/Notam_linux.sh Notam_linux.sh
ln -s bin/linux/stop_notam_linux.sh stop_notam_linux.sh
```

Afterwards, `./Notam_linux.sh` and `./stop_notam_linux.sh` work directly from `NOTAMv1/`. These symlinks are listed in `.gitignore` and are never committed.

### macOS

Requires Big Sur 11+.

1. Install Python 3 (via Homebrew, if you don't already have it):

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   brew install python3
   ```

2. Clone the repository:

   ```bash
   git clone https://github.com/amelingu/NOTAMv1.git ~/Documents/NOTAMv1
   cd ~/Documents/NOTAMv1
   ```

3. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Launch:

   ```bash
   ./bin/mac/Notam_mac.sh
   ```

> **Gatekeeper note:** on first launch, macOS may warn that the script is from an unidentified developer. Right-click `Notam_mac.sh` in Finder and choose **Open**, then confirm. Subsequent launches work normally.

### Windows

1. Clone the repository (or download and extract the ZIP from GitHub):

   ```bash
   git clone https://github.com/amelingu/NOTAMv1.git
   cd NOTAMv1
   ```

2. Open **`cmd.exe`** (not PowerShell — see note below) and create a virtual environment:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Launch:

   ```bash
   bin\windows\Notam_windows.bat
   ```

> **Use `cmd.exe`, not PowerShell:** PowerShell blocks `.ps1` scripts by default and requires a one-time policy change before `.venv\Scripts\activate` works. `cmd.exe` avoids this entirely.

### Android (Termux)

1. Install **Termux** and **Termux:API** from [F-Droid](https://f-droid.org) (recommended over the outdated Play Store version).

2. Open Termux and run:

   ```bash
   termux-setup-storage
   pkg install python termux-api git
   ```

3. Get the project files. **Option A — clone from GitHub (recommended):**

   ```bash
   cd ~
   git clone https://github.com/amelingu/NOTAMv1.git
   ```

   **Option B — copy from Downloads:**

   > **Important:** place the project in the **Termux home directory**, not in Downloads. Android shared storage does not support symlinks and the Python virtual environment will fail.

   ```bash
   cp -r ~/storage/downloads/NOTAMv1 ~/NOTAMv1
   ```

4. Create a virtual environment:

   ```bash
   cd ~/NOTAMv1
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

5. Launch:

   ```bash
   bash bin/android/Notam_android.sh
   ```

6. **Stopping the server:**

   ```bash
   bash ~/NOTAMv1/bin/android/stop_notam_android.sh
   ```

> **Android notes:**
>
> - Keep Termux running in the background — use the home button rather than swiping Termux away.
> - Grant Termux **notification permission** in Android Settings → Apps → Termux → Notifications before first launch.
> - On the **very first launch**, Android will show a one-time dialog asking to approve the wake lock. Tap Allow.

### iPad / iPhone — LAN access

iOS and iPadOS cannot run the Python server directly. Run the server on a computer (Linux, macOS, or Android via Termux) and connect from Safari over your local network.

1. Start the server on your computer as normal. The terminal prints both a local and a LAN address.
2. Make sure your iPad/iPhone is on the **same WiFi network**, then go to the LAN address (e.g. `http://192.168.1.24:8766`).
3. **Add to Home Screen** (Safari Share → Add to Home Screen) for one-tap access.

> The LAN IP may change between sessions if your router reassigns it — always check the terminal output for the current address.

### iPad / iPhone — standalone with a-Shell

For pilots who want NOTAMv1 to work without any other computer. Runs the Python server directly on the iPhone/iPad via [a-Shell](https://apps.apple.com/app/a-shell/id1473805438).

**Setup:**

1. Install **a-Shell** from the App Store (free).

2. Download and extract the repository:

   ```bash
   curl -L -o notamv1.zip https://github.com/amelingu/NOTAMv1/archive/refs/heads/main.zip
   python3 -c "import zipfile; zipfile.ZipFile('notamv1.zip').extractall()"
   mv NOTAMv1-main NOTAMv1
   cd NOTAMv1
   rm ../notamv1.zip
   ```

3. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

4. Run the server:

   ```bash
   python3 bin/ios/run_notam_ios.py
   ```

5. Once `READY` appears, switch to Safari **without closing a-Shell** (Slide Over or Split View) and go to `http://localhost:8766`.

**One-tap Shortcuts launch:** build a four-step Shortcut with: Execute `cd ~/Documents/NOTAMv1`, Execute `python3 bin/ios/run_notam_ios.py`, Split Screen between a-Shell and Safari, Wait 5 seconds, Open URL `http://localhost:8766`.

> Keep a-Shell visible on screen at all times — iOS suspends backgrounded apps. This is a fundamental iOS limitation.

---

## Daily use

| Action | Linux | macOS | Windows | Android |
|---|---|---|---|---|
| Start | `./bin/linux/Notam_linux.sh` | `./bin/mac/Notam_mac.sh` | `bin\windows\Notam_windows.bat` | `bash bin/android/Notam_android.sh` |
| Stop | `./bin/linux/stop_notam_linux.sh` | `./bin/mac/stop_notam_mac.sh` | `bin\windows\stop_notam_windows.bat` | `bash bin/android/stop_notam_android.sh` |
| Reset credentials | Delete `config.py`, then relaunch | ← same | ← same | ← same |

### Updating to the latest version

```bash
git pull
```

Restart the server afterward to pick up the changes. `config.py`, `snapshots/`, and log files are excluded via `.gitignore` and are never overwritten.

---

## The briefing interface

### Route — ICAO codes

Fill in departure, destination, alternates, and waypoints. **⭐ Set as default** saves your usual settings to `_prefs.json` and syncs them to GitHub immediately.

All airports in this section are checked for NOTAMs and, for IFR flights, for GNSS RAIM outage predictions.

### Route variants

Three radio buttons — **Left**, **Straight**, **Right** — maintain independent NOTAM snapshots and waypoint lists for different lateral routing strategies on the same DEP/DEST pair.

### Date/time lock

**🔓 Unlocked / 🔒 Locked** — controls whether departure date and time are set to *now + 30 minutes* on each launch (unlocked) or restored from the last session (locked).

### AI NOTAM summary

When an Anthropic API key is configured, a concise plain-language summary is generated for each ICAO after fetching NOTAMs. The summary is tailored to your flight: aircraft category, flight rules, departure date/time, maximum flight duration, and ticked approach equipment are all considered.

### Fetch NOTAMs

Retrieves NOTAMs from autorouter.aero. On subsequent fetches, NEW and GONE markers highlight changes. Also triggers:
- AZBA/RTBA activation schedule refresh (for IFR and VFR flights)
- GNSS RAIM outage fetch in the background (IFR only)

### Never-show list

Click 🚫 on any NOTAM to permanently suppress it. Stored in `_never.json` and synced to GitHub.

### Flush snapshot

- **Click** — flush the snapshot for the current route
- **Ctrl+click** — flush all route snapshots for your account

### Approach equipment filter

Hides procedure NOTAMs irrelevant to your aircraft's approach capabilities. Tick each approach type your aircraft is certified and equipped to fly.

> **Note on LNAV/VNAV and RAIM:** ticking LNAV/VNAV also enables barometric aiding in the GNSS RAIM prediction (see [Setting up GNSS RAIM predictions](#setting-up-gnss-raim-predictions)), since baro aiding is a realistic assumption when the aircraft has LNAV/VNAV capability.

---

## Cross-device snapshot sync

**Option A — GitHub automatic sync (recommended):**

1. Create a **private** GitHub repository (e.g. `username/notam-snapshots`)
2. Create a Personal Access Token with **repo** scope
3. Edit `config.py`:

   ```python
   GITHUB_REPO   = 'username/notam-snapshots'
   GITHUB_TOKEN  = 'github_pat_xxxxxxxxxxxxxxxxxxxx'
   GITHUB_BRANCH = 'main'
   ```

4. Restart the server — a **🔄 Pull** button appears in the briefing toolbar

**Option B — Manual git sync:** `git pull` before a session, `git add snapshots/ && git commit && git push` after.

**Option C — Local only:** leave `GITHUB_REPO` empty in `config.py`.

### Advanced note — sync lifecycle

| Trigger | Push | Pull |
|---|---|---|
| **Page load** | — | `_prefs.json` then all snapshots + `_never.json` |
| **Fetch NOTAMs** | push-all if dirty | pull all |
| **Ack NEW or GONE** | schedules 30s debounce push | — |
| **Never-show a NOTAM** | `_never.json` immediately | — |
| **⭐ Set as Default** | `_prefs.json` immediately | — |
| **🔄 Pull button** | — | pull all |
| **Print / PDF** | push-all | — |
| **Tab / browser close** | push-all via `sendBeacon` | — |
| **Flush snapshot** | delete single route file from GitHub | — |

---

## Setting up AI NOTAM summaries

Requires an Anthropic API key. Cost is negligible for personal use (~$0.003 per full briefing).

1. Create an account at [console.anthropic.com](https://console.anthropic.com) and add a small credit
2. Generate an API key
3. Enter it directly in the app — click the **⭐ AI summary ✨** label, paste the key into the field that appears, and press **Enter**

   Or edit `config.py` manually:

   ```python
   ANTHROPIC_API_KEY = 'sk-ant-xxxxxxxxxxxxxxxxxxxx'
   ```

4. Restart the server

Set lines or chars to `0` to disable summaries temporarily without removing the key.

---

## Setting up AZBA/RTBA zone data

AZBA (Activation des zones basses altitudes) is the French military very-low-altitude training network (RTBA). NOTAMv1 plots these zones on the map and shows their real-time activation status.

**Zone geometry (required):** a zone geometry CSV exported from [openAIP](https://www.openaip.net) must be placed at:

```
NOTAMv1/data/zones_RTBA_openAIP.csv
```

This file is tracked in the repository and is already present after cloning.

On first request, the server automatically parses this CSV into `data/azba_zones_cache.json`.

**Optional — automatic refresh from openAIP:** add your openAIP API key to `config.py`:

```python
OPENAIP_API_KEY = 'your_openaip_api_key_here'
```

With a key configured, the server checks once on startup whether a refresh is due (~every 28 days) and runs it in the background. You can also trigger a refresh manually:

```bash
curl -X POST http://localhost:8766/azba/refresh
```

---

## Setting up AZBA activation schedule

The AZBA activation schedule is fetched automatically from the SIA SOFIA API — no additional setup required. When you fetch a briefing, the app queries the official French AIS publication for the current RTBA activation schedule and cross-references it with your planned flight window.

**What is shown:** a status card appears in the briefing results:
- **Green** — no active RTBA during the planned flight
- **Amber** — no active RTBA, but a caveat that the publication window doesn't cover your full flight time
- **Red** — active RTBA zones listed chronologically by activation slot, with a 📍 map badge that opens the fullscreen map centred on all active zones

Clicking any RTBA zone polygon on the map opens a popup with its exact activation slots.

Active zones are shown **red** on the map; inactive zones are shown **blue** (darker for SFC-based zones). Zone labels show the official SIA name (e.g. R145B) even when the underlying openAIP geometry uses a different name (e.g. R145).

The schedule is cached for 15 minutes. The publication window typically covers a 48-hour horizon from the last SIA update.

> **Availability:** the AZBA activation schedule is specific to French airspace (RTBA zones). This feature has no effect on briefings that do not include French airports.

---

## Setting up GNSS RAIM predictions

GNSS RAIM outage predictions are fetched from the [EUROCONTROL AUGUR](https://augur.eurocontrol.int) API for IFR flights. This predicts periods during which GNSS RAIM may not be available at route airports for the approach procedure (LNAV/VNAV — RNP 0.3).

**Requirements:** a free AUGUR API account. Register via the Connectivity page at [augur.eurocontrol.int/connectivity/](https://augur.eurocontrol.int/connectivity/).

**Configuration:** add your credentials to `config.py`:

```python
AUGUR_USERNAME = 'your@email.com'
AUGUR_PASSWORD = 'yourpassword'
```

Restart the server after editing `config.py`.

**What is checked:** all airports listed in the Route — ICAO codes section — ADEP, ADEST, take-off alternate, both destination alternates, ICAO waypoints, and additional airports.

**Algorithm parameters:**
- Algorithm: FDE (Fault Detection and Exclusion — required for LNAV/VNAV approaches under EASA AMC 20-4)
- Procedure: RNP APCH 0.3 (LNAV/VNAV)
- Mask angle: 5° by default (adjustable per airport — see below)
- Barometric aiding: ON when LNAV/VNAV is ticked in the Approach equipment filter, OFF otherwise
- Selective availability: OFF (SA has been permanently disabled since 2000)

**What is shown:** a status card in the briefing (IFR flights only):
- **Green** — no RAIM outages predicted at any route airport during the flight window
- **Orange** — outages predicted at one or more airports, listed with start/end times and duration
- **Amber caveat** — the AUGUR prediction horizon doesn't cover the full flight window
- **Grey** — AUGUR credentials not configured, or data unavailable

The card always shows the date for which predictions were computed and a link to the AUGUR web tool for manual verification.

**Caching:** results are cached for 30 minutes per (airport set, flight window, baro aiding) combination. The cache is cleared on each new NOTAM fetch.

**Performance note:** AUGUR's computation typically takes 15–30 seconds per request. The fetch runs in the background — the RAIM card appears once the result is ready without blocking the rest of the briefing.

> **Scope:** RAIM prediction is only meaningful for IFR flights using GNSS approaches. The RAIM card is not shown for VFR flights. For RNAV en-route use, GPS NOTAMs (KGPS/KNMH) from the NOTAM feed remain the primary reference.

### Per-airport mask angle overrides

AUGUR computes satellite geometry using a uniform horizon mask angle. The default of 5° is appropriate for flat terrain, but airports surrounded by mountains have a significantly higher effective horizon — satellites below 10–15° elevation may be completely invisible behind terrain, making a 5° mask optimistic and predictions unreliable.

You can specify a higher mask angle (up to 12.5°, the API maximum) for individual airports in `config.py`:

```python
AUGUR_MASK_OVERRIDES = {
    # 'LFMN': 10.0,  # Nice Côte d'Azur — Alpes-Maritimes to the N/NE
    # 'LFLB': 12.5,  # Chambéry-Savoie — surrounded by Alps on three sides
    # 'LFKJ': 10.0,  # Ajaccio — Monte Rotondo massif to the NE
    # 'LFKB': 10.0,  # Bastia — terrain rising steeply to the W
    # 'LFLI': 10.0,  # Annecy — surrounded by pre-Alps
    # 'LFHU': 10.0,  # Albertville — deep Alpine valley
    # 'LFLS': 10.0,  # Grenoble — Chartreuse and Belledonne massifs
}
```

All entries are commented out by default. Uncomment and adjust based on your own operational experience at each airport. Airports with an override get a separate AUGUR API call; results are merged transparently before display.

---

## Reference

### Prerequisites

- Python 3.10 or later
- A modern web browser (Firefox, Chrome, Edge)
- An [autorouter.aero](https://www.autorouter.aero) account (free registration)

### Folder structure

```
NOTAMv1/
├── config.py                    ← your credentials and personal settings
│                                   (create once, never share, never commit)
├── requirements.txt             ← Python dependencies (certifi only)
├── .gitignore
├── README.md
├── notam_briefing_v1.html       ← the application (served by the local server)
├── src/
│   ├── notam_server.py          ← local HTTP server, API proxy, tile proxy, GitHub sync
│   ├── setup_config.py          ← first-run credential setup
│   ├── update_maprender.py      ← regenerates maprender.js (runs on every start)
│   ├── maprender.js             ← map rendering JS bundle (auto-generated)
│   ├── azba.py                  ← AZBA/RTBA zone data: CSV parsing, cache, openAIP
│   │                               refresh, SIA SOFIA activation schedule
│   └── raim.py                  ← GNSS RAIM outage predictions via AUGUR API
├── data/
│   ├── zones_RTBA_openAIP.csv   ← AZBA/RTBA zone geometry (tracked in git)
│   └── azba_zones_cache.json    ← generated cache (gitignored)
├── bin/
│   ├── linux/
│   │   ├── Notam_linux.sh       ← one-click launcher (also usable via symlink at root)
│   │   ├── start_notam_linux.sh
│   │   └── stop_notam_linux.sh
│   ├── mac/
│   │   ├── Notam_mac.sh
│   │   ├── start_notam_mac.sh
│   │   └── stop_notam_mac.sh
│   ├── windows/
│   │   ├── Notam_windows.bat
│   │   ├── start_notam_windows.bat
│   │   └── stop_notam_windows.bat
│   ├── android/
│   │   ├── Notam_android.sh
│   │   ├── start_notam_android.sh
│   │   └── stop_notam_android.sh
│   └── ios/
│       ├── run_notam_ios.py     ← recommended launcher (foreground, no subprocess)
│       ├── start_notam_ios.py   ← legacy, not recommended
│       └── stop_notam_ios.py
├── logs/
│   ├── .gitkeep
│   └── notam_server.log         ← runtime log (gitignored)
├── snapshots/                   ← JSON snapshot files — gitignored here, synced
│                                   separately via a private GitHub repo (see above)
└── test/                        ← local dev/test scripts — gitignored
```

### Security note

`config.py` contains your credentials in base64 obfuscation (autorouter.aero) or plain text (all other keys). This is not encryption — it only prevents casual shoulder-surfing. The file is listed in `.gitignore` and must never be committed, emailed, or shared. Each device running NOTAMv1 has its own `config.py`, created locally on first launch.

In particular: the AUGUR password, openAIP API key, Anthropic API key, and GitHub personal access token are all stored in plain text within `config.py`. Keep this file on-device only.

---

## Acknowledgements

NOTAMs are fetched via the [autorouter.aero](https://www.autorouter.aero) API. Airport data is sourced from [OurAirports](https://ourairports.com). Map tiles are provided by [CartoCDN](https://carto.com), [Esri](https://www.esri.com), and [openAIP](https://www.openaip.net). AI summaries use the [Anthropic API](https://www.anthropic.com). AZBA activation data from [SIA SOFIA](https://sofia-briefing.aviation-civile.gouv.fr). GNSS RAIM predictions from [EUROCONTROL AUGUR](https://augur.eurocontrol.int).

---

## Contact

For requests, suggestions, or bug reports, contact: **guillaume.ameline@gmail.com**
