# Installation

OPENLAB runs in two equivalent modes that share one backend, one database and one data directory:

1. **Local web mode** (default) — the backend serves the built UI at <http://127.0.0.1:3210>.
2. **Desktop mode** — a Tauri 2 shell that starts the same backend and shows it in a native window.

## 1. Prerequisites

| Component | Version | Needed for |
| --- | --- | --- |
| Python | 3.12+ | backend (required) |
| Node.js | 18+ | building/developing the UI, desktop shell |
| Docker Desktop / Engine | recent | isolated project deployments (strongly recommended) |
| Playwright Chromium | installed by the script | preview, screenshots, exploration |
| FFmpeg | any recent build | MP4/GIF export (Playwright ships a usable build) |
| Rust | 1.77+ | desktop installers only |

Check what you have:

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File .\scripts\check-deps.ps1
```

```bash
# macOS / Linux
./scripts/check-deps.sh
```

Both scripts print a per-component verdict and the exact command to install anything missing.

## 2. Windows

```powershell
git clone <repo> OPENLAB
cd OPENLAB
.\scripts\openlab-start.ps1
```

The script creates `.venv`, installs `requirements.txt`, installs Chromium into `.browsers/`,
builds `apps/web/dist`, prints environment warnings and starts the backend.

Options:

```powershell
.\scripts\openlab-start.ps1 -Port 3300 -NoBrowser   # custom port, don't open a browser
.\scripts\openlab-start.ps1 -SkipWeb                # backend only (API work)
.\scripts\openlab-start.ps1 -Lan                    # bind 0.0.0.0 (opt-in)
```

Stop everything (backend + containers):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\openlab-stop.ps1
```

Notes for Windows:

- Supported on Windows 10 1809+ and Windows Server 2022 (verified on build 10.0.20348).
- Docker Desktop with the WSL2 backend is detected automatically; WSL status is on the Settings page.
- If your account cannot write to `%TEMP%`, the start script redirects `TEMP`/`TMP` into the
  repository's `.tmp/` folder, which is what Playwright needs.
- PowerShell may block unsigned scripts; either use `-ExecutionPolicy Bypass` as shown above or
  `Set-ExecutionPolicy -Scope Process RemoteSigned`.

## 3. macOS

```bash
git clone <repo> OPENLAB
cd OPENLAB
chmod +x scripts/*.sh
./scripts/openlab-start.sh
./scripts/openlab-stop.sh          # or --keep-containers
```

- Apple Silicon and Intel are both supported; the script reports the architecture.
- `scripts/docker-arch.sh` prints the host architecture, useful when a project only publishes
  `amd64` images (Docker Desktop will emulate them, slowly).

## 4. Manual installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium

cd apps/web && npm install && npm run build && cd ../..

# Windows
set PYTHONPATH=%CD%;%CD%\apps\backend
python -m openlab --port 3210 --open
```

```bash
# macOS
export PYTHONPATH="$PWD:$PWD/apps/backend"
python -m openlab --port 3210 --open
```

## 5. First run checklist

1. Open <http://127.0.0.1:3210> → **Settings**.
2. Confirm Docker, Chromium and FFmpeg show as available.
3. Add a model provider under **Model Providers** (optional — everything except AI plan refinement and
   automatic repair works without a model).
4. Import a repository from **Overview → Import from GitHub**.
5. Press **Deploy** on the project page and watch the Logs tab.

## 6. Where things are stored

Everything lives under `OPENLAB_DATA` (default: `<repo>/OPENLAB_DATA`). Set `OPENLAB_DATA_DIR` to put
it somewhere else. See the “Data layout” section of [`../README.md`](../README.md).

## 7. Uninstalling

1. Stop the app (`openlab-stop` script).
2. Delete the repository folder.
3. Delete `OPENLAB_DATA` (contains projects, media and the encrypted secrets).
4. Remove any `openlab-*` containers and `openlab-net-*` networks if Docker is still running
   (the stop script does this for you).
