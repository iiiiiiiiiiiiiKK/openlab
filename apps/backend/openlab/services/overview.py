"""Dashboard aggregation for the Overview page."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from openlab.db import session_scope
from openlab.models import (
    AIUsageRecord,
    Asset,
    Project,
    ProjectTask,
    Report,
)


def build_overview() -> dict:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        total_projects = session.scalar(select(func.count(Project.id))) or 0
        running = session.scalar(
            select(func.count(Project.id)).where(Project.deploy_status == "RUNNING")
        ) or 0
        failed = session.scalar(
            select(func.count(Project.id)).where(
                Project.deploy_status.in_(["FAILED", "WAITING_FOR_USER"])
            )
        ) or 0
        total_assets = session.scalar(select(func.count(Asset.id))) or 0
        total_reports = session.scalar(select(func.count(Report.id))) or 0
        week_ago = now - timedelta(days=7)
        recent_projects = list(
            session.scalars(
                select(Project).order_by(Project.updated_at.desc()).limit(8)
            )
        )
        active_tasks = list(
            session.scalars(
                select(ProjectTask)
                .where(ProjectTask.status.in_(["queued", "running", "paused"]))
                .order_by(ProjectTask.created_at.desc())
                .limit(10)
            )
        )
        recent_tasks = list(
            session.scalars(select(ProjectTask).order_by(ProjectTask.created_at.desc()).limit(10))
        )
        usage_rows = list(
            session.scalars(select(AIUsageRecord).where(AIUsageRecord.created_at >= week_ago))
        )
        asset_kinds = {
            kind: count
            for kind, count in session.execute(
                select(Asset.kind, func.count(Asset.id)).group_by(Asset.kind)
            ).all()
        }
        project_status = {
            status: count
            for status, count in session.execute(
                select(Project.deploy_status, func.count(Project.id)).group_by(Project.deploy_status)
            ).all()
        }
        return {
            "stats": {
                "projects": int(total_projects),
                "running": int(running),
                "failed": int(failed),
                "assets": int(total_assets),
                "reports": int(total_reports),
                "asset_kinds": asset_kinds,
                "project_status": project_status,
            },
            "ai_usage_7d": {
                "requests": sum(r.requests for r in usage_rows),
                "tokens": sum(r.total_tokens for r in usage_rows),
                "cost_usd": round(sum(r.cost_usd for r in usage_rows), 6),
                "unknown_usage_records": sum(1 for r in usage_rows if not r.usage_is_known),
            },
            "recent_projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "display_name": p.display_name,
                    "description": p.description,
                    "source_type": p.source_type,
                    "source_url": p.source_url,
                    "deploy_status": p.deploy_status,
                    "language": p.language,
                    "stars": p.stars,
                    "favorite": p.favorite,
                    "asset_count": p.asset_count,
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }
                for p in recent_projects
            ],
            "active_tasks": [
                {
                    "id": t.id,
                    "kind": t.kind,
                    "project_id": t.project_id,
                    "status": t.status,
                    "stage": t.stage,
                    "progress": t.progress,
                    "message": t.message,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in active_tasks
            ],
            "recent_tasks": [
                {
                    "id": t.id,
                    "kind": t.kind,
                    "project_id": t.project_id,
                    "status": t.status,
                    "message": t.message,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in recent_tasks
            ],
        }
