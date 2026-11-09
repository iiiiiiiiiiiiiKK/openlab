# Architecture

## Overview

OPENLAB is a monorepo with four cooperating layers:

```
┌──────────────────────────────────────────────────────────────────────┐
│ apps/web (React 18 + TS + Vite + Tailwind)      apps/desktop (Tauri) │
│  · 9 routes, multi-panel project workspace      · starts the backend │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTP + WebSocket (127.0.0.1:3210)
┌───────────────────────────▼──────────────────────────────────────────┐
│ apps/backend (FastAPI)                                               │
│  routers/   thin HTTP surface                                        │
│  services/  orchestration: importer, deploy, explore, reporting      │
│  db.py      SQLAlchemy + SQL migrations (SQLite, WAL)                │
│  events.py  in-process event bus → /ws/events                        │
│  handlers.py task handlers registered on the scheduler               │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ plain Python imports (no HTTP)
┌───────────────────────────▼──────────────────────────────────────────┐
│ engine/ (deterministic, unit-testable)                               │
│  core · project_importer · project_analyzer · docker_manager         │
│  task_scheduler · ai_agent · browser_automation · media_processor    │
│  asset_manager · report_generator · runtime (opt-in host execution)  │
└──────────────────────────────────────────────────────────────────────┘
```

`engine/` has no FastAPI dependency, so the interesting logic can be tested without a server.

## The deployment pipeline

`apps/backend/openlab/services/deploy.py` implements the state machine defined in
`engine/core/states.py`:

```
PENDING → ANALYZING → PLANNING → PREPARING → INSTALLING → BUILDING
        → STARTING → HEALTH_CHECK → RUNNING
                          └─ on failure → repair loop (≤3 attempts) → FAILED / WAITING_FOR_USER
```

Guarantees:

1. **Evidence-based success.** `RUNNING` is only set after `engine.docker_manager.health.http_ready`
   receives a real HTTP response from `http://127.0.0.1:<port><health_path>`.
2. **Blocked means blocked.** Missing Docker, a GPU-only project, an unavailable start command or a
   missing provider end in `WAITING_FOR_USER` with the exact reason recorded on the project row.
3. **Bounded repair.** Each attempt diagnoses from real logs, patches the isolated workspace, and
   retries only the failed step. Diffs are stored in `patches` and on disk.
4. **Auditability.** Every shell command is streamed to the Logs tab and appended to
   `projects/<id>/logs/deploy.log`.

### Why a generated compose file?

A repository may or may not ship Docker configuration:

| Repository state | Strategy | Behaviour |
| --- | --- | --- |
| `compose.yaml` present | `compose` | Validate against the policy guard, then run it as-is (project dir mapped into the container as the build context is never mounted). |
| `Dockerfile` present | `dockerfile` | Copy it, append a non-root `USER` if missing, generate the compose wrapper. |
| Neither | `generated` | Build a Dockerfile from the run plan (`engine/docker_manager/generator.py`). |

In all three cases the wrapper compose file supplies the resource limits, the security baseline and
the `127.0.0.1` port binding.

### The host runtime (explicit, unsandboxed opt-in)

When Docker is missing and the user enabled `allow_host_runtime`, `deploy_on_host` runs the same
pipeline without container artefacts:

1. the run plan is derived straight from the workspace (no Dockerfile is generated),
2. the install command runs as a real subprocess with a transcript in `logs/host-install.log`,
3. the start command runs in the reserved port range with `PORT` exported, plus
   `-- --port <n>` for npm-style scripts,
4. if the framework ignores both and binds its own port, `find_service_port(pid)` reads the real
   listening port (netstat/lsof) and the health check is retried there,
5. the process is recorded in `containers` as `host-process (no isolation)` and the strategy is
   stored as `host`, which is what the UI badge and the report use to warn about it,
6. the PID and log path are persisted to `OPENLAB_DATA/tmp/host-processes/<id>.json` so a later
   request — or the backend after a restart — can still stop it.

Host mode never reports a success it did not verify, and never silently replaces Docker.

## Run plans

`engine/project_analyzer/planner.py` produces a `RunPlan` where every field carries
`(value, source, confidence)`. Sources are real files (`package-lock.json`, `package.json scripts.start`,
`.nvmrc`, `requirements.txt`, …). The planner never invents a build step the repository does not
define; when it is unsure it lowers `confidence` and adds a warning or a blocking requirement.

The model is only consulted to refine an ambiguous plan (`analyzer.plan_prompt`) and its answer is
treated as a proposal that must remain consistent with the observed facts.

## AI layer

`engine/ai_agent/` is split into:

- `adapters.py` — one adapter per wire format (`openai-chat`, `anthropic-messages`,
  `gemini-generate`) with a shared `CompletionResult`; `extract_json` tolerates prose, code fences and
  trailing commas.
- `client.py` — provider resolution, `Budget` guard rails (requests/tokens/USD) and usage accounting.
  `usage_is_known` is stored per record, so "estimated" is never confused with "measured".
- `patch.py` — our own unified-diff writer/applier plus backup/rollback, because OPENLAB cannot assume
  a `git` binary is present.
- `repair.py` — the capped repair loop with error extraction and relevant-file discovery.

Cost control in practice: deterministic steps never call a model, planning uses the high-capability
model, summaries use the cheap model, and repair is capped by `OPENLAB_MAX_REPAIR_ATTEMPTS`.

## Browser automation

`engine/browser_automation/` separates concerns:

- `launcher.py` — Chromium launch with fallbacks (bundled → system Chrome → system Edge), and
  `ensure_artifact_temp` because locked-down Windows accounts cannot always write to `%TEMP%`.
- `inspector.py` — one DOM script collects nav, buttons, inputs, forms, headings and a text excerpt;
  `ConsoleCollector` records console and network errors.
- `capture.py` — viewport presets, full-page/section screenshots and aspect-ratio cropping.
- `explorer.py` — plan generation (quick/deep/custom) **and validation**: unknown actions, foreign
  origins, missing selectors and risky labels are rejected before anything is executed.
- `executor.py` — runs the plan, keeps one browser session, captures per-step screenshots and records
  the video for the whole session.

Risk policy: payment, deletion, publishing, sending, logout and similar destructive labels are never
clicked automatically, in English or Chinese.

## Media pipeline

`engine/media_processor/ffmpeg.py` locates a system FFmpeg and otherwise falls back to the build
bundled with Playwright. GIF encoding uses the two-pass palette method and shrinks fps/width until the
requested size budget is met. All conversions report the real ffmpeg result; failures surface as HTTP
503/500 with the ffmpeg tail rather than an empty success.

## Reports

`engine/report_generator/report.py` renders Markdown, HTML and JSON from a `ReportInput` that is
assembled from the database (`services/reporting.py`). The verdict is derived from recorded
observations only:

- deployment success + HTTP status,
- per-step exploration status,
- captured assets,
- applied patches.

README text is quoted as repository metadata and never promoted to a verification result.

## Persistence and restart behaviour

All state is in SQLite (`openlab.db`, WAL mode, foreign keys on). The scheduler re-queues tasks that
were `queued` or `running` during a restart and clears their cancel flags, so an interrupted
deployment resumes instead of disappearing.

### Snapshots and deletion (`services/lifecycle.py`)

Two destructive-adjacent operations are implemented as pure helpers so they can be unit tested
without a server:

- **Snapshot** — `POST /api/projects/{id}/snapshots` stores the generated compose text, the run plan,
  the start commands, the port mapping and the environment variable **names**. Values are never
  written, so a snapshot cannot leak a secret.
- **Restore** — `POST /api/projects/{id}/snapshots/{sid}/restore` rewrites
  `projects/<id>/docker/compose.openlab.yml`, refreshes the `project_environments` row from the stored
  plan and marks the project with “restored from snapshot …”, so the UI shows where the setup came
  from. It deliberately does **not** auto-deploy; the user re-runs the pipeline.
- **Deletion** — `DELETE /api/projects/{id}?keep_assets=&keep_reports=` copies the requested artefact
  folders into `OPENLAB_DATA/preserved/<timestamp>-<name>-<id>/` together with a `manifest.json`
  before removing the project folder and its rows. Only folders that actually contain files are
  copied, so an empty pre-created directory never produces a misleading bundle.

## Security model

| Threat | Mitigation |
| --- | --- |
| Malicious archive | traversal/link/entry-count/size checks before extraction |
| Malicious project | container isolation, non-root, `cap_drop: ALL`, resource limits, no host mounts, no socket, blocked egress by default |
| Credential leak | encrypted at rest with a machine-bound key; redaction on logs, events, reports and API responses |
| Port/host takeover | reserved port range bound to `127.0.0.1` only |
| Unsafe automation | plan validation against the live DOM + risky-action deny list |
| Silent downgrade | the unsandboxed host runtime is opt-in and labelled everywhere |

## Extension points

- **New provider format** → add an adapter in `engine/ai_agent/adapters.py` and register it in
  `ADAPTERS` / `KIND_TO_FORMAT`.
- **New task kind** → implement a handler in `apps/backend/openlab/handlers.py` and register it.
- **New language/runtime** → extend `LANGUAGE_BY_SUFFIX`, `FRAMEWORK_HINTS` and the planner branches.
- **New export format** → extend `engine/report_generator/report.py`.
