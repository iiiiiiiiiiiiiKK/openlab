"""Report endpoints: generate, list, fetch, download, delete."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import select

from openlab.db import session_scope
from openlab.models import Project, Report
from openlab.services.reporting import generate_report, report_row_summary

router = APIRouter(prefix="/api", tags=["reports"])


@router.get("/reports")
async def list_reports(project_id: str | None = None, limit: int = 50) -> dict:
    with session_scope() as session:
        query = select(Report).order_by(Report.created_at.desc()).limit(limit)
        if project_id:
            query = query.where(Report.project_id == project_id)
        rows = list(session.scalars(query))
        return {"items": [report_row_summary(r) for r in rows]}


@router.post("/projects/{project_id}/reports")
async def create_report(request: Request, project_id: str) -> dict:
    settings = request.app.state.settings
    layout = settings.resolve_data()
    with session_scope() as session:
        if session.get(Project, project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")
    return generate_report(project_id, layout, request.app.state.box)


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict:
    with session_scope() as session:
        row = session.get(Report, report_id)
        if row is None:
            raise HTTPException(status_code=404, detail="report not found")
        return report_row_summary(row)


@router.get("/reports/{report_id}/markdown", response_class=PlainTextResponse)
async def report_markdown(report_id: str) -> PlainTextResponse:
    with session_scope() as session:
        row = session.get(Report, report_id)
        if row is None or not row.markdown_path:
            raise HTTPException(status_code=404, detail="report markdown not found")
        path = Path(row.markdown_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="report file missing on disk")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@router.get("/reports/{report_id}/html")
async def report_html(report_id: str) -> FileResponse:
    with session_scope() as session:
        row = session.get(Report, report_id)
        if row is None or not row.html_path:
            raise HTTPException(status_code=404, detail="report html not found")
        path = Path(row.html_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="report file missing on disk")
    return FileResponse(path, media_type="text/html", content_disposition_type="inline")


@router.get("/reports/{report_id}/download")
async def download_report(report_id: str, fmt: str = "md") -> FileResponse:
    with session_scope() as session:
        row = session.get(Report, report_id)
        if row is None:
            raise HTTPException(status_code=404, detail="report not found")
        mapping = {"md": row.markdown_path, "html": row.html_path, "json": row.json_path, "pdf": row.pdf_path}
        target = mapping.get(fmt)
        if not target:
            raise HTTPException(status_code=400, detail=f"unsupported format: {fmt}")
        path = Path(target)
        title = row.title or "report"
    if not path.exists():
        raise HTTPException(status_code=410, detail="report file missing on disk")
    return FileResponse(path, filename=f"{title}.{fmt}", media_type="application/octet-stream")


@router.delete("/reports/{report_id}")
async def delete_report(report_id: str, remove_files: bool = False) -> dict:
    with session_scope() as session:
        row = session.get(Report, report_id)
        if row is None:
            raise HTTPException(status_code=404, detail="report not found")
        paths = [row.markdown_path, row.html_path, row.json_path, row.pdf_path]
        session.delete(row)
    if remove_files:
        for raw in paths:
            if raw:
                Path(raw).unlink(missing_ok=True)
    return {"deleted": True}
