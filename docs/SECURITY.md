# Security model

Third-party repositories are **untrusted code**. OPENLAB assumes every imported project may be
hostile and constrains it accordingly.

## Isolation

| Control | Implementation |
| --- | --- |
| Container per project | `docker compose -p openlab-<project> ...` with its own network and container names |
| Non-root execution | generated Dockerfiles create uid 10001 and set `USER openlab`; repository Dockerfiles get an appended non-root `USER` when they define none |
| No privilege escalation | `security_opt: ["no-new-privileges:true"]`, `cap_drop: ["ALL"]`, `privileged: false` |
| No Docker socket | never generated, and any compose file that mounts it is rejected by the policy guard |
| No host mounts | generated compose files mount nothing from the host; the build context is the project folder only |
| Resource caps | CPU, memory, PIDs, tmpfs `/tmp` and a run timeout per project |
| Network | outbound blocked by default; enable per-installation in Settings |
| Ports | `127.0.0.1` only, from a reserved range (default 41000–41999) |

### Policy guard

`engine/docker_manager/generator.guard_compose_text` scans any repository-provided compose file and
**refuses to run it** when it finds:

- `privileged: true`
- a `docker.sock` mount
- host mounts of `/var/run`, `/etc`, `/root`, `C:\Users`
- `network_mode: host`
- `pid: host` / `ipc: host`

The refusal is reported with the specific violation — OPENLAB never silently lowers the bar.

### Opt-in host runtime

If Docker is unavailable, deployment ends in `WAITING_FOR_USER`. The alternative — running the project
directly on the machine — exists only when the user enables
*Settings → Allow host runtime (no isolation)*:

- the setting defaults to `false`,
- the warning is printed into the task log and stored as the project's status detail,
- the container record is stored with `image = "host-process (no isolation)"`,
- the report marks the run as unsandboxed.

## Secrets

| Item | Storage |
| --- | --- |
| Provider API keys | `ai_providers.api_key_enc`, Fernet-encrypted with a key derived from `OPENLAB_DATA/secrets/master.key` (HKDF over a random 48-byte file + machine/user salt) |
| GitHub token | `app_settings` row, same encryption |
| Master key | `OPENLAB_DATA/secrets/master.key`, created mode 0600 where the platform allows |

Consequences:

- The plaintext key never reaches the browser: the API returns only a masked hint (`sk-****abcd`).
- Deleting `master.key` invalidates every stored secret (they cannot be decrypted afterwards).
- `tests/integration/test_api.py::test_provider_key_never_persisted_in_plaintext` scans the SQLite
  files (including `-wal`/`-shm`) for the literal key to prove this.

### Redaction

`engine/core/security.redact` masks `sk-…`, `ghp_…`, `AIza…`, bearer tokens and
`key/token/secret/password = …` pairs. It is applied to:

- every log line before it is written or streamed (`TaskScheduler.append_log`),
- every WebSocket event payload (`EventBus.publish`),
- report output (`report_generator.render_markdown`),
- file previews served to the UI (`/api/projects/{id}/file`).

## Transport and headers

- The backend binds to `127.0.0.1` by default; LAN binding requires an explicit flag/switch.
- All responses carry `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: SAMEORIGIN` and a restrictive `Content-Security-Policy`.
- `/api/**` responses are `Cache-Control: no-store`.
- CORS allows only the local dev origins.

## Untrusted input handling

| Vector | Defence |
| --- | --- |
| Malicious ZIP/TAR | absolute paths, `..`, symlinks and hard links are rejected; entry count and unpacked size are capped |
| Huge download | streaming download aborts past `OPENLAB_MAX_DOWNLOAD_MB` |
| Path traversal in the API | every path is resolved and checked against the project root (`UnsafePath`) |
| Model-written patch | paths are normalised, `..` rejected, binaries refused, 2 MB per-file limit, workspace-only writes, backup before write |
| Malicious web app during exploration | plans validated against the live DOM; foreign origins rejected; risky actions (pay/delete/publish/send/logout, EN + zh) never clicked |
| Infinite repair | attempt cap (default 3), then `WAITING_FOR_USER` |

## What OPENLAB will not do

- Run a container as root or privileged.
- Mount the Docker socket or your home directory.
- Modify host configuration, install system packages or edit PATH.
- Bypass authentication on a target app (login is manual, in a real browser, and the session is stored
  separately).
- Present a guessed success: no health check response means no `RUNNING` status.

## Reporting a vulnerability

Please open a private security advisory (or email the maintainer listed in `LICENSE`) with:

- affected version/commit,
- reproduction steps,
- impact assessment,
- any suggested fix.

Do not publish a public issue for an exploitable problem before a fix is available.
