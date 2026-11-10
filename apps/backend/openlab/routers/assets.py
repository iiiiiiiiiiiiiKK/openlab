"""Asset endpoints: listing, streaming, media conversion, export."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from engine.asset_manager.manager import AssetManager
from engine.media_processor.ffmpeg import (
    FFmpegUnavailable,
    MediaCapabilityError,
    MediaProcessor,
)

from openlab.db import session_scope
from openlab.models import Asset
from openlab.schemas import AssetExportRequest, AssetRename, GifRequest, VideoConvertRequest
from openlab.services.explore import asset_summary

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("")
async def list_assets(
    request: Request,
    project_id: str | None = None,
    kind: str | None = None,
    limit: int = 500,
) -> dict:
    with session_scope() as session:
        manager = AssetManager(session, request.app.state.settings.resolve_data())
        rows = manager.list_assets(project_id=project_id, kind=kind, limit=limit)
        stats = manager.storage_stats(project_id=project_id)
        return {"items": [asset_summary(r) for r in rows], "stats": stats}


@router.get("/{asset_id}")
async def get_asset(request: Request, asset_id: str) -> dict:
    layout = request.app.state.settings.resolve_data()

    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        payload = asset_summary(asset)
        payload["absolute_path"] = str(manager.absolute_path(asset))
        return payload


@router.get("/{asset_id}/raw")
async def raw_asset(request: Request, asset_id: str) -> FileResponse:
    layout = request.app.state.settings.resolve_data()

    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = session.get(Asset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        path = manager.absolute_path(asset)
        mime = asset.mime_type or "application/octet-stream"
        filename = asset.filename
    if not path.exists():
        # A queued task can reference a file that a later run replaced or removed.
        # Fall back to any other asset of the same project with the same filename so
        # the workbench never shows a broken image for a stale id.
        with session_scope() as session:
            replacement = session.scalar(
                select(Asset)
                .where(Asset.filename == filename, Asset.id != asset_id)
                .order_by(Asset.created_at.desc())
            )
            if replacement is not None:
                candidate = AssetManager(session, layout).absolute_path(replacement)
                if candidate.exists():
                    path = candidate
                    mime = replacement.mime_type or mime
                else:
                    replacement = None
        if replacement is None:
            raise HTTPException(
                status_code=410,
                detail=(
                    "asset file is missing on disk; it may have been produced by an earlier run "
                    "of this project"
                ),
            )
    return FileResponse(path, media_type=mime, filename=filename, content_disposition_type="inline")


@router.patch("/{asset_id}")
async def rename_asset(request: Request, asset_id: str, body: AssetRename) -> dict:
    layout = request.app.state.settings.resolve_data()

    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = manager.rename_asset(asset_id, body.label)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        return asset_summary(asset)


@router.delete("/{asset_id}")
async def delete_asset(request: Request, asset_id: str, remove_file: bool = True) -> dict:
    layout = request.app.state.settings.resolve_data()

    with session_scope() as session:
        manager = AssetManager(session, layout)
        ok = manager.delete_asset(asset_id, remove_file=remove_file)
        if not ok:
            raise HTTPException(status_code=404, detail="asset not found")
        return {"deleted": True, "warning": manager.last_delete_warning}


@router.post("/export")
async def export_assets(request: Request, body: AssetExportRequest) -> dict:
    layout = request.app.state.settings.resolve_data()
    target = Path(body.target_dir).expanduser()
    if not target.is_absolute():
        raise HTTPException(status_code=400, detail="target_dir must be an absolute path")
    resolved = target.resolve()
    # Never let an export write back into the OPENLAB data tree: that would let one
    # asset overwrite another project evidence file or the database beside it.
    root = layout.root.resolve()
    if resolved == root or root in resolved.parents:
        raise HTTPException(
            status_code=400,
            detail="target_dir must be outside the OPENLAB data directory",
        )

    with session_scope() as session:
        manager = AssetManager(session, layout)
        written = manager.bulk_export(body.asset_ids, resolved)
        return {"exported": [str(p) for p in written], "count": len(written)}


@router.post("/gif")
async def make_gif(request: Request, body: GifRequest) -> dict:
    layout = request.app.state.settings.resolve_data()
    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = session.get(Asset, body.asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        source = manager.absolute_path(asset)
        project_id = asset.project_id
    if not source.exists():
        raise HTTPException(status_code=410, detail="source video is missing on disk")
    processor = MediaProcessor()
    gif_dir = layout.project_subdir(project_id, "gifs")
    target = gif_dir / f"{source.stem}-{int(body.duration_s)}s.gif"
    try:
        result = await processor.to_gif(
            source,
            target,
            start_s=body.start_s,
            duration_s=body.duration_s,
            fps=body.fps,
            width=body.width,
            max_size_kb=body.max_size_kb,
        )
    except FFmpegUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MediaCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.ok or not target.exists():
        raise HTTPException(status_code=500, detail=result.tail(40) or "ffmpeg failed")
    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = manager.register_file(
            project_id,
            target,
            kind="gif",
            label=f"GIF from {source.name}",
            duration_ms=int(body.duration_s * 1000),
        )
        return asset_summary(asset)


@router.post("/video/convert")
async def convert_video(request: Request, body: VideoConvertRequest) -> dict:
    layout = request.app.state.settings.resolve_data()
    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = session.get(Asset, body.asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        source = manager.absolute_path(asset)
        project_id = asset.project_id
    if not source.exists():
        raise HTTPException(status_code=410, detail="source video is missing on disk")
    processor = MediaProcessor()
    video_dir = layout.project_subdir(project_id, "videos")
    target = video_dir / f"{source.stem}.{body.format}"
    try:
        if body.format == "mp4":
            result = await processor.to_mp4(source, target, max_width=body.max_width)
        else:
            result = await processor.to_webm(source, target)
    except FFmpegUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MediaCapabilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result.ok or not target.exists():
        raise HTTPException(status_code=500, detail=result.tail(40) or "ffmpeg failed")
    info = await processor.probe(target)
    with session_scope() as session:
        manager = AssetManager(session, layout)
        asset = manager.register_file(
            project_id,
            target,
            kind="video",
            label=f"Converted {source.name}",
            width=info.width or None,
            height=info.height or None,
            duration_ms=info.duration_ms or None,
        )
        return asset_summary(asset)


@router.post("/capture/{project_id}")
async def capture_assets(request: Request, project_id: str) -> dict:
    from openlab.services.explore import resolve_project_url

    url = resolve_project_url(project_id)
    if not url:
        raise HTTPException(status_code=409, detail="project is not running")
    task_id = await request.app.state.scheduler.submit(
        "assets", project_id=project_id, payload={"url": url}
    )
    return {"task_id": task_id, "kind": "assets", "url": url}
