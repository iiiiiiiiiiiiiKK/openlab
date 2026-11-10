# OPENLAB — runtime status

```
version    : 0.1.1
backend    : http://127.0.0.1:3210 (FastAPI, SQLite/WAL)
data dir   : <repo>/OPENLAB_DATA
```

## Verified on this machine

| Check | Result |
| --- | --- |
| `pytest tests/unit tests/integration -q` | **191 passed, 1 skipped** |
| `pytest -q` (everything, including e2e) | **206 passed, 5 skipped** |
| `pytest tests/e2e -q -rs` | **6 passed, 2 skipped** (MP4 needs full FFmpeg; Docker compose needs a daemon) |
| `npx tsc --noEmit` (apps/web) | clean |
| `npx vite build` (apps/web) | 4 chunks (187 KB app + 208 KB React + 27 KB icons) |
| UI screenshots | 9 captured by `scripts/capture-ui-screenshots.py` into `docs/screenshots/` (Chinese UI) |
| `npm test` (apps/web) | **15 passed** — translation catalogue + warning + hint mapping |
| Live end-to-end | import → deploy → preview → explore → assets → report = **VERIFIED** |

## Host capabilities detected here

| Component | Status |
| --- | --- |
| Python | 3.11.3 (the code targets 3.12+, 3.11 works too) |
| Node | 24.22.2 |
| Docker CLI / daemon | **not installed** — container deployments end in `WAITING_FOR_USER` with that reason |
| Playwright Chromium | installed (`chromium-1254`) |
| Playwright FFmpeg | installed (`ffmpeg-1010`) — required for video recording |
| FFmpeg | Playwright's bundled build (png + libvpx only). GIF works via the frames+Pillow path; MP4 returns 409 naming the missing encoder |
| Rust / cargo | **not installed** — desktop installers cannot be built on this host |
| git | available, but imports still use the GitHub codeload ZIP API |

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

From a live run on this host (import → deploy → preview → explore → assets → report):

```
RUNNING on http://127.0.0.1:41000 (HTTP 200, probed at report time)
title='OPENLAB Node Fixture'   exploration=VERIFIED (2 steps)
assets: 8 screenshots   report: VERIFIED
```

The generated project folder contained real artefacts — `screenshots/preview-desktop.png` (1440×900),
`screenshots/step-00-goto.png`, `reports/report-v1.{md,html,json}` — and the report rendered them with
the observed HTTP status, the step table and the console/network error summary.

## Fixed in 0.1.1 — evidence-integrity and correctness pass

This release closes the defects that made OPENLAB unsafe to trust or use as a real tool.

### Would have corrupted or lost real data

| Defect | Fix |
| --- | --- |
| Generated compose used `build.context: ..`, which pointed Docker at the project folder (`source/`, `screenshots/`, previous build output) instead of `workspace/`, so `COPY . /app` never received the application | `generate_compose(..., build_context=...)` now receives the compose-relative path to `workspace/`; `deploy._build_context_relpath()` computes it |
| AI repair sent a 12 000-character excerpt while asking for "the COMPLETE new file content"; applying it silently truncated real source files | files larger than `MAX_EDITABLE_FILE_BYTES` are skipped instead of truncated |
| Unified-diff context lines were overwritten instead of compared, so an unmatching hunk "applied successfully" and corrupted the file | context lines are compared and a mismatch raises `PatchError` |
| `apply_patch` wrote files one by one; a mid-bundle failure left the workspace half-patched and the next attempt stacked on it | the write is all-or-nothing and rolls back through `rollback_patch()` |
| `delete_project` ran `rmtree` on the `data_dir` column with no containment check | only paths inside `OPENLAB_DATA/projects/` may be removed |
| Two projects with the same name shared container/network names, so deploying B tore down A | container/network names include the project id |

### Lied about results (the product's own red line)

| Defect | Fix |
| --- | --- |
| An exploration step started as `VERIFIED`; `goto` discarded the HTTP response, so a 404 page and a bare `wait` were both reported as verified experience | status starts `UNVERIFIED`; only asserting actions can be verified; `goto` fails on ≥400 |
| A run made only of passive steps could still be `VERIFIED` | `ExplorationReport.status()` requires at least one truly verified step |
| Console/network errors accumulated across steps, so step N inherited step N−1's errors | `console_errors_since()` attributes them per step |
| The report asserted "answered HTTP health checks (status 200)" from the deploy flag alone | the status is probed live; when nothing answers the report says so |
| `.md`, `.html` and `.json` recomputed the payload independently and could disagree | one payload is shared by all three renderers |

### Would have crashed or hung the app

| Defect | Fix |
| --- | --- |
| A bracketed IPv6 entry in `NO_PROXY` (`…,::1,[::1]`) made every `httpx.AsyncClient` raise `InvalidURL: Invalid port: ':1]'` at construction — GitHub import and every model call failed | `engine/core/http.py` normalises the variable and is the only place clients are built |
| Playwright validated a recording ffmpeg build it could not find and dropped the whole browser session | availability is detected up front; recording is dropped (with the reason logged) instead of the session |
| `PLAYWRIGHT_BROWSERS_PATH` was forced to the data directory even when empty, hiding a working global install | the variable is only overridden when a Chromium build actually exists there |
| A failed browser launch leaked a Chromium + driver process (`__aenter__` raised, `__aexit__` never ran) | `__aenter__` tears down on failure and `__aexit__` is idempotent |
| `AIClient` sessions were never closed on usage/settings endpoints; the pool ran dry and the API hung | every caller closes its session |
| `Budget.consume()` was never called, so the request/token/USD caps did nothing | `client.complete()` and `complete_json()` accept and consume a budget |
| A malformed model reply raised `ValueError` from `extract_json` and killed the deploy task | it is wrapped in `ProviderError` and handled as a normal repair failure |
| Every output line triggered its own commit; a long build blocked the event loop on the SQLite write lock | log lines are coalesced into batches |

### Scheduler semantics

| Defect | Fix |
| --- | --- |
| A task paused while queued still executed when a worker popped it | `_run` skips `paused` rows |
| `cancel()` on a paused task only set the flag, leaving it paused for ever | queued/paused/waiting tasks are cancelled immediately |
| Cancelling a task cancelled the *worker* itself (it was the entry in `_running`), so every later job stalled | each task runs in its own child task; the worker keeps consuming |
| The cancellation handler overwrote the row after `resume()` had requeued it, leaving it stuck | it only demotes rows still marked `running` |
| `resume()` could enqueue the same task twice | enqueue is de-duplicated |
| `max_parallel_deployments` changed only the config, never the worker pool | `scheduler.set_concurrency()` resizes the pool |

### Settings and lifecycle

| Defect | Fix |
| --- | --- |
| UI settings were written to `app_settings` and never read back, so every restart silently restored defaults | `_apply_persisted_settings()` hydrates them at startup |
| `STARTING → RUNNING` and `PENDING → PREPARING` were rejected by the state machine and silently forced | both transitions are legal; illegal ones are logged instead of swallowed |

## Known limitations (by design, all surfaced to the user)

| Situation | OPENLAB behaviour |
| --- | --- |
| Docker missing | `WAITING_FOR_USER` + install hint; never a fake `RUNNING` |
| GPU-only project | `WAITING_FOR_USER`, GPU evidence listed in the AI Agent panel and the report |
| Missing API keys | flagged per variable from `.env.example`, marked `BLOCKED` in the report |
| Target app refuses framing | preview shows a notice; *Open in a new tab* still works, screenshots unaffected |
| Minimal FFmpeg | WebM ✅, GIF via Pillow ✅, MP4 → `409` with the exact missing pieces |
| No Rust toolchain | `apps/desktop` is complete but cannot be packaged here; documented in its README |
| No terminal authentication | OPENLAB binds `127.0.0.1` only. With `allow_lan=true` the terminal endpoint becomes reachable by anyone on the LAN — enable LAN mode only behind your own reverse proxy with auth |

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
