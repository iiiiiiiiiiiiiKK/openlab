# Testing

OPENLAB tests must produce **real** evidence. Mocking Docker, the browser or FFmpeg to claim success
is explicitly forbidden — environment-dependent tests skip with a reason instead.

## Running the suites

```bash
pytest tests/unit -q            # pure logic, no external dependencies (~1s)
pytest tests/integration -q     # real FastAPI app + real SQLite (~10s)
pytest tests/e2e -q             # real browser, real subprocesses, Docker when present (minutes)
pytest -q                       # everything
```

Useful switches:

```bash
OPENLAB_SKIP_BROWSER=1 pytest tests/e2e -q          # skip browser tests
OPENLAB_DATA_DIR=/tmp/openlab pytest -q             # isolated data directory
pytest tests/e2e/test_real_loop.py::test_full_loop_import_deploy_preview_explore_report -v
```

## What each suite covers

### `tests/unit`

| File | Covers |
| --- | --- |
| `test_repo_url.py` | GitHub URL/SSH/shorthand parsing, reserved paths, invalid input, slugging |
| `test_archive_and_paths.py` | ZIP extraction, traversal/absolute/entry-count/size rejection, `DataLayout` isolation and metadata |
| `test_analyzer.py` | tree scan, manifest facts, Node/Python/compose/GPU/unknown plans, `.env.example` parsing, port detection, confidence |
| `test_docker_generation.py` | generated Dockerfile (non-root, healthcheck), compose limits and env rendering, policy guard, container/network names, port mapping parsing, port allocation, aspect-ratio cropping |
| `test_ai_adapters.py` | the three provider wire formats, JSON extraction from messy model output, budget guard rails, usage summary |
| `test_security_states.py` | secret box round-trip/persistence, redaction patterns, deploy and task state machines |
| `test_scheduler.py` | end-to-end of the persistent queue: success, failure, cancel, pause/resume, retry, requeue-after-restart |
| `test_explorer_report.py` | quick/deep/custom plan generation, risky-action blocking, model-plan validation, report status/verdict/redaction |
| `test_host_runtime.py` | reserved-port probing, port-flag injection, real process start/stop, state recovery from disk |
| `test_media_capabilities.py` | ffmpeg listing parsing, GIF strategy ladder (palette → frames+Pillow → 409), MP4 capability gating |
| `test_lifecycle_helpers.py` | preservation manifests, report-only/asset-only bundles, restore/delete error paths |

### `apps/web` (Vitest)

| File | Covers |
| --- | --- |
| `src/lib/i18n/i18n.test.ts` | every key has both languages, placeholder sets match, no Chinese leaked into the English catalogue, brand-name exceptions are explicitly whitelisted, `translate()` defaults to Chinese, interpolation and unknown-key fallback |

```bash
cd apps/web && npm test          # or: npm run test:web from the repo root
```

### `tests/integration`

`test_api.py` drives the real app through `TestClient` (lifespan included) against a throwaway database:

- health, overview, host probing, security headers and SPA fallback;
- GitHub/ZIP/local import, project tree and file reads, traversal rejection;
- provider CRUD, key masking and a **plaintext-key scan of the SQLite files**;
- settings round-trip including GitHub token storage and clearing;
- deployment without Docker ending in `WAITING_FOR_USER` (skipped when a daemon exists);
- report generation (v1 → v2), patches and snapshots, re-analysis, project deletion;
- explore refusing to run for a non-running project.

`test_lifecycle.py` covers the destructive operations end to end:

| Test | Proves |
| --- | --- |
| `test_delete_preserves_assets_and_reports` | real files are copied to `preserved/<stamp>/…`, a manifest is written, and the project folder plus row are gone |
| `test_delete_without_preservation_leaves_nothing` | opting out creates no `preserved/` folder at all |
| `test_delete_unknown_project_returns_404` | unknown ids are rejected, not silently ignored |
| `test_snapshot_restore_writes_compose_and_environment` | the compose file is rewritten and the project is marked “restored from snapshot …” |
| `test_restore_unknown_snapshot_returns_404` / `test_restore_rejects_snapshot_of_another_project` | snapshots cannot be applied across projects |

### `tests/e2e`

Real processes and real browsers:

| Test | What really happens |
| --- | --- |
| `test_real_browser_preview_and_screenshot` | starts the Node fixture, loads it in Chromium, asserts the real title/DOM/PNG size |
| `test_real_browser_multi_viewport_capture` | desktop/mobile/square captures with verified pixel dimensions |
| `test_real_exploration_records_steps_and_video` | deep plan executed against the fixture, screenshots on disk, `/about` visited |
| `test_video_to_gif_with_real_ffmpeg` | records a session, then creates a GIF and checks the `GIF89a` magic bytes |
| `test_mp4_transcode` | records a session, transcodes to MP4, probes duration and width |
| `test_full_loop_import_deploy_preview_explore_report` | import → deploy → preview → explore → assets → report over HTTP |
| `test_host_runtime_refuses_unsafe_state` | a multi-service stack must be refused (not silently half-run) without Docker |
| `test_docker_compose_stack_when_available` | real compose build+run of the multi-service fixture |

### How the browser tests avoid fragile timings

- The fixture servers are started by the test and polled until they answer.
- Chromium is launched through `BrowserSession`, which already falls back from the bundled
  browser to a system Chrome/Edge and redirects the artifact temp dir.
- Screenshots are asserted by real PNG dimensions, not by "the file exists".
- Video is only asserted after the browser context has fully closed, which is when Playwright
  finalises the recording.

## Media capability handling (why GIF/MP4 tests can skip)

The FFmpeg binary bundled with Playwright is intentionally minimal: on this machine it exposes only
`png` + `libvpx` encoders, the `image2`/`webm` muxers and a small filter set — **no GIF encoder, no
`palettegen`, no H.264**.

`engine/media_processor/ffmpeg.py` therefore probes the real listings and selects a strategy:

| Capability | GIF | MP4 | WebM |
| --- | --- | --- | --- |
| Full build (`libx264`, `gif`, `palettegen`) | ffmpeg palette pipeline | ffmpeg | ffmpeg |
| Minimal Playwright build | **ffmpeg PNG frame extraction + Pillow assembly** | `409` with the missing pieces named | ffmpeg |
| Nothing usable | `409` explaining what to install | `409` | `409` |

The GIF fallback is a real encoder path, not a mock: frames are extracted by ffmpeg and assembled by
Pillow, and the result is verified by its `GIF89a` header in the test suite. MP4 is impossible with the
bundled build, so the e2e test skips with that exact reason instead of pretending to pass.

Install a full FFmpeg to enable MP4:

```powershell
winget install Gyan.FFmpeg          # Windows
```
```bash
brew install ffmpeg                 # macOS
```

## The three required scenarios

| Scenario | Fixture | How it is exercised |
| --- | --- | --- |
| Simple Node.js web project | `tests/fixtures/node-web` | plan detection (unit), import/preview (integration), deploy + explore (e2e) |
| Python web project | `tests/fixtures/python-web` | plan detection (unit), import/report (integration), browser load (e2e harness) |
| Multi-service compose + database | `tests/fixtures/compose-stack` | compose strategy detection, policy guard, `postgres` service inference (unit + integration), real `docker compose up` (e2e, skipped without Docker) |

## Skips are meaningful

Every skip prints why. Typical messages on a machine without Docker:

```
SKIPPED [1] tests/e2e/test_real_loop.py: ... Docker daemon is not available on this host
SKIPPED [1] tests/e2e/test_real_loop.py: deployment could not reach RUNNING on this host
    (docker available=False, status=WAITING_FOR_USER): Docker is required to run projects...
```

A skipped Docker test is **not** a passing deployment test. The host-runtime test still proves the
pipeline (install → start → health check → evidence) end to end without pretending to be isolated.

## Verified results

Baseline on this development machine (Windows Server 2022, Python 3.12.14, Node 24.22,
Playwright Chromium 131 + its bundled FFmpeg, no Docker daemon, no Rust toolchain):

```
$ pytest tests/unit tests/integration -q
160 passed, 1 skipped        # the skip is "no system ffmpeg" for the GIF end-to-end test

$ pytest tests/e2e -q -rs
6 passed, 2 skipped
  SKIPPED  this ffmpeg build has no H.264/mp4 support (missing: an H.264 encoder (libx264),
           the mp4 muxer); install a full FFmpeg to transcode
  SKIPPED  Docker daemon is not available on this host
```

What the passing e2e run actually proved:

- a real Node process was started, answered HTTP 200 and produced a 1440×900 PNG preview;
- Playwright executed a plan against the live app, followed `/about` and wrote per-step screenshots;
- a screen recording was finalised and converted into a real GIF (`GIF89a`);
- the HTTP API completed import → deploy → preview → explore → assets → report and reported
  `VERIFIED` with 4 screenshots and 1 video;
- the multi-service compose fixture was **refused** in host mode with the reason
  “needs Docker to run faithfully”, instead of a false success.

The same loop is available as a one-command smoke test against a live server:

```bash
python scripts/verify-core-loop.py                      # local fixture project
python scripts/verify-core-loop.py --github sindresorhus/slugify --json
```

## Adding tests

- Prefer a real fixture directory under `tests/fixtures/` over monkeypatching.
- For engine logic, always use `tmp_path` and the `layout` / `data_dir` fixtures.
- For API work, use the `client` fixture (fresh data dir per test).
- Never assert on timing alone; poll with a deadline helper.
