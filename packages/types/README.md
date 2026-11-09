# `@openlab/types`

Single source of truth for the JSON payloads exchanged between the FastAPI backend
and the React frontend.

The package is TypeScript-source-only (no build step): both `apps/web` and any future
frontend import it directly through a path alias, so a backend schema change shows up
as a type error in the UI rather than as a runtime surprise.

## Usage

```ts
import type { ProjectSummary, TaskRow, RunPlan } from "@openlab/types";
```

`apps/web/vite.config.ts` and `apps/web/tsconfig.json` map the specifier to
`packages/types/src/index.ts`.

## Keeping it honest

- Every interface here mirrors a router response in `apps/backend/openlab/routers/`.
- Optional fields are marked optional; anything the backend always sends is required.
- Do not add helper functions — this package is types only so it never lands in the bundle.
