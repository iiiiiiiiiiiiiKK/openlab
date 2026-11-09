# Contributing to OPENLAB

Thanks for helping. OPENLAB is a local-first tool that must stay **honest**: never fake a deployment,
a screenshot or a report — and never weaken the isolation defaults to make something "work".

## Ground rules

1. **Deterministic work stays in code.** Downloads, extraction, installs, health checks, screenshots,
   encoding and file management must not call a model.
2. **Failures are data.** Return `FAILED` / `WAITING_FOR_USER` / `BLOCKED` with the real error instead
   of retrying forever or swallowing the problem.
3. **No silent security downgrade.** If a feature needs `privileged`, a host mount or the Docker
   socket, it must be refused with an explanation — not enabled quietly.
4. **Original sources are never modified.** The AI writes only to `workspace/`; keep `source/` pristine.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium
cd apps/web && npm install
```

## Workflow

1. Make sure the tests you are about to touch pass before your change:
   `pytest tests/unit -q`.
2. Implement your change in the smallest module that owns the concern
   (`engine/*` for pure logic, `apps/backend/openlab/services/*` for orchestration,
   `apps/backend/openlab/routers/*` for HTTP, `apps/web/src/*` for UI).
3. Add or update tests:
   - new engine behaviour → `tests/unit`
   - new endpoint or service wiring → `tests/integration`
   - anything that touches Docker, the browser or FFmpeg → `tests/e2e` (skips are fine, mocks are not)
4. Run everything:
   ```bash
   pytest -q
   cd apps/web && npx tsc --noEmit && npm run build
   ```
5. Update documentation: `README.md`, `docs/ARCHITECTURE.md` and `docs/TESTING.md` where relevant.

## Coding standards

### Python

- Python 3.12 syntax, `from __future__ import annotations` at the top of every module.
- Type hints on public functions; dataclasses for structured results.
- Run external commands through `engine.core.process.run_command` / `run_shell` so transcripts and
  redaction are consistent.
- Never log a raw credential: pass strings through `engine.core.security.redact`.
- New database tables: add a `migrations/000N_*.sql` file **and** the matching SQLAlchemy model.

### TypeScript / React

- Strict TypeScript; no `any` in committed code.
- Components live in `src/components/ui` (primitives) or `src/features/**` (feature panels).
- All backend calls go through `src/lib/api.ts` so error handling stays uniform.
- Match the existing visual language: dark graphite surfaces, hairline borders, compact type scale.
  Do not add large gradients, giant hero headings or template dashboards.

## Testing expectations

| Change | Required evidence |
| --- | --- |
| Engine logic | unit test with real files in `tmp_path` |
| API route | integration test using `TestClient` |
| Docker/compose | unit test for the generated file + e2e when a daemon is available |
| Browser automation | e2e test against the local fixture apps |
| Report rendering | unit test asserting the evidence is present and secrets are redacted |

Environment-dependent tests must **skip with a reason**, never pass by mocking.

## Reporting bugs

Include: OS, Python/Node versions, Docker availability, the project you tried to import, the failing
stage, and the relevant excerpt from `OPENLAB_DATA/projects/<id>/logs/deploy.log`.
Redact tokens before pasting logs.
