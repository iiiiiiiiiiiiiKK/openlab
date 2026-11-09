# `@openlab/ui`

The component library behind the OPENLAB workbench.

The live implementation lives in [`apps/web/src/components/ui`](../../apps/web/src/components/ui)
so the Vite alias `@/components/ui/*` keeps working without a build step. This package
documents the primitives and is the place for components that must also be importable by
other frontends (for example a future marketing site or the Tauri shell's own pages).

## Primitives

| Component | File | Notes |
| --- | --- | --- |
| `Button` | `button.tsx` | variants `primary` / `secondary` / `ghost` / `danger` / `outline`, sizes `sm` / `md` / `icon`, built-in loading spinner |
| `Input`, `Textarea`, `Select`, `Field`, `Label` | `input.tsx` | shared focus ring, dark surfaces, optional hint text |
| `Card`, `PanelHeader`, `EmptyState`, `Metric`, `Divider` | `card.tsx` | the panel chrome used across every page |
| `Badge`, `StatusBadge`, `StatusDot` | `badge.tsx` | one status→tone map for the whole app |
| `Modal` | `modal.tsx` | Escape/backdrop close, focus-friendly markup |
| `Progress`, `Skeleton`, `Spinner` | `progress.tsx` | determinate queue progress and loading states |
| `ToastProvider`, `useToast` | `toast.tsx` | bottom-right stack, auto-dismiss, error toasts linger |

## Design tokens

Defined in `apps/web/tailwind.config.js`:

| Token | Value | Use |
| --- | --- | --- |
| `canvas` | `#08090b` | page background |
| `surface` | `#0d0f12` | app chrome (sidebar, toolbars) |
| `panel` | `#111317` | cards and panels |
| `elevated` | `#15181d` | inputs, hovered rows |
| `hairline` | `#1e2126` | 1px separators |
| `hairline-strong` | `#282c33` | control borders |
| `ink` / `muted` / `faint` | `#e8eaed` / `#9aa3ae` / `#6b7480` | text hierarchy |
| `accent` | `#5b8cff` | primary action, active tab underline |
| `ok` / `warn` / `danger` / `info` | `#3ecf8e` / `#e0b341` / `#e5654f` / `#57a5f5` | status colours |

Type scale is compact by design (`text-2xs` 10.5px → `text-2xl` 23px) so a 1440×900 window
shows real density instead of oversized marketing headings.

## Rules

- Every interactive element needs a visible focus ring (provided by the base layer).
- Status colour comes **only** from `StatusBadge` / `StatusDot`, never hard-coded per page.
- No decorative gradients or glows: the product looks like a developer tool, not a landing page.
- Components stay presentational; data fetching belongs to `apps/web/src/features/**`.
