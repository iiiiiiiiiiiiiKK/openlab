# OPENLAB — runtime status

```
version    : 0.1.0
backend    : http://127.0.0.1:3210 (FastAPI, SQLite/WAL)
data dir   : <repo>/OPENLAB_DATA
```

## Verified on this machine

| Check | Result |
| --- | --- |
| `pytest tests/unit tests/integration -q` | **171 passed, 2 skipped** (skips: no system FFmpeg for the GIF e2e test) |
| `pytest tests/e2e -q -rs` | **6 passed, 2 skipped** (MP4 needs full FFmpeg; Docker compose needs a daemon) |
| `scripts/verify-core-loop.py` | import → deploy → preview → explore → assets → report = **VERIFIED** |
| `npx tsc --noEmit` (apps/web) | clean |
| `npx vite build` (apps/web) | 4 chunks (141 KB app + 208 KB React + 26 KB icons) |
| UI screenshots | 9 captured by `scripts/capture-ui-screenshots.py` into `docs/screenshots/` (Chinese UI) |
| `npm test` (apps/web) | **15 passed** — translation catalogue + warning + hint mapping |

## Host capabilities detected here

| Component | Status |
| --- | --- |
| Python | 3.12.14 |
| Node | 24.22.1 |
| Docker CLI / daemon | **not installed** — container deployments end in `WAITING_FOR_USER` with that reason |
| Playwright Chromium | installed in `.browsers/chromium-1148` |
| FFmpeg | Playwright's bundled build (png + libvpx only). GIF works via the frames+Pillow path; MP4 returns 409 naming the missing encoder |
| Rust / cargo | **not installed** — desktop installers cannot be built on this host |
| git | **not installed** — imports use the GitHub codeload ZIP API, which is why no git binary is required |

## Optional host-runtime mode

Because Docker is absent, `OPENLAB_ALLOW_HOST_RUNTIME=true` (Settings → *Allow host runtime*) was used
to prove the pipeline end to end. In that mode:

- every project is labelled `host` / `host-process (no isolation)` in the database and the UI;
- the project status detail and the report both state explicitly that there is no isolation;
- multi-service stacks (`compose` strategy or detected backing services) are **refused** rather than
  half-started;
- the child process is tracked in `OPENLAB_DATA/tmp/host-processes/<id>.json`, so a later request or a
  restarted backend can still stop it.

With Docker present, the same code path uses `docker compose up --build` with `cap_drop: ALL`,
`no-new-privileges`, capped CPU/memory/PIDs and a `127.0.0.1`-bound port; nothing else in the product
changes.

## Observed end-to-end evidence

From a live run on this host (`scripts/verify-core-loop.py`):

```
RUNNING on http://127.0.0.1:41003 (HTTP 200)
title='OPENLAB Node Fixture'   exploration=VERIFIED (3 steps)
assets: 4 screenshots, 1 video  report: VERIFIED
```

The generated project folder contained real artefacts — `screenshots/preview-desktop.png` (1440×900),
`videos/exploration-*.webm`, `reports/report-v1.{md,html,json}` — and the report rendered them with the
observed HTTP status, step table and console/network error summary.

## Known limitations (by design, all surfaced to the user)

| Situation | OPENLAB behaviour |
| --- | --- |
| Docker missing | `WAITING_FOR_USER` + install hint; never a fake `RUNNING` |
| GPU-only project | `WAITING_FOR_USER`, GPU evidence listed in the AI Agent panel and the report |
| Missing API keys | flagged per variable from `.env.example`, marked `BLOCKED` in the report |
| Target app refuses framing | preview shows a notice; *Open in a new tab* still works, screenshots unaffected |
| Minimal FFmpeg | WebM ✅, GIF via Pillow ✅, MP4 → `409` with the exact missing pieces |
| No Rust toolchain | `apps/desktop` is complete but cannot be packaged here; documented in its README |

## Restart behaviour

A reboot leaves the database saying `RUNNING` while the containers and host processes are gone.
On startup `services/lifecycle.reconcile_runtime_state()` re-checks every `RUNNING` project against
its recorded port and demotes the dead ones to `STOPPED` with the explanation
“The previous session ended (reboot or shutdown) and the service is no longer listening on its port.”
so the dashboard never shows a service that is not actually answering. Covered by
`tests/integration/test_restart_reconcile.py`.

## Non-runnable project types

OPENLAB verifies a project by running it behind HTTP in a container and driving a browser.
Repositories that cannot expose an HTTP interface are detected up front rather than failing
obscurely:

| Signal | Classification |
| --- | --- |
| `.sln` / `.vcxproj` / `.csproj` (Windows desktop, shell extensions, DWM hooks) | `native` (`windows-desktop`) |
| `CMakeLists.txt`, `meson.build`, Bazel `BUILD`/`WORKSPACE` | `native` |
| `build.gradle`, `pom.xml` | `native` (`jvm`) |
| `global.json`, `Directory.Build.props` | `native` (`dotnet`) |
| C/C++/C#/Assembly sources only + README mentioning DLL / shell extension / driver | `native` |

Such a project is stored, analysed and reported, but `deploy` returns
`WAITING_FOR_USER` with the **specific** reason (e.g. “Visual Studio solution/project file”) and the
AI Agent panel shows a notice that preview/exploration/screenshots do not apply. It is never marked
`RUNNING` and never produces a fake experience report.

Verified on a real repository (`Maplespe/DWMBlurGlass`, a C++ DWM shell extension):
detected as `native`, `language=C++`, `confidence=0.0`, deployment blocked with the explanation above.
