# Screenshots

These images are **not** mockups. They are captured by OPENLAB's own Playwright
pipeline from the live workbench:

```bash
# 1. start the app
python -m openlab --port 3210

# 2. capture every page (the project workspace is discovered from the live API)
python scripts/capture-ui-screenshots.py
```

| File | View |
| --- | --- |
| `overview.png` | Overview — stats strip, recent projects, active task queue, quick actions, environment panel |
| `projects.png` | Projects — searchable table with status, stack badges and per-row deploy |
| `workspace.png` | Project workspace — resizable live preview above the Terminal / Logs / Explorer / AI Agent / Assets / Report tabs |
| `running.png` | Running — live containers, persistent task queue with pause/cancel, task history |
| `explorer.png` | AI Explorer — mode picker, validated plan steps, run history, safety rules |
| `assets.png` | Assets — filterable grid of real screenshots/videos/GIFs with a detail drawer |
| `reports.png` | Reports — version list and rendered Markdown evidence report |
| `providers.png` | Model Providers — BYO keys, connectivity test, usage/cost summary |
| `settings.png` | Settings — container resources, network isolation, credentials, host readiness |

Captured on Windows Server 2022 with Playwright Chromium 131 at 1440×900.

The workspace screenshot contains real state from a completed loop (a deployed Node fixture with
captured screenshots, an exploration plan and a generated report), so it documents the panels with
actual data instead of empty states.

To refresh them after a UI change:

```bash
python scripts/capture-ui-screenshots.py --width 1600 --height 1000
python scripts/capture-ui-screenshots.py --project <project-id>   # pin a specific project
python scripts/capture-ui-screenshots.py --no-workspace           # skip the workspace page
```
