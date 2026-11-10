"""GitHub metadata fetch and source archive download.

The public REST API is queried for repository facts (stars, license, default
branch, commit SHA). Source code is fetched as a codeload ZIP archive, which
avoids requiring a local ``git`` install. Private repositories use the user's own
token; the token never leaves the backend.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from engine.core.http import build_async_client
from engine.core.paths import UnsafePath
from engine.project_importer.archive import ArchiveRejected, ExtractStats, extract_zip
from engine.project_importer.repo_url import RepoRef


class ImportFailed(RuntimeError):
    """Raised when a repository cannot be imported."""


@dataclass
class RepoMetadata:
    ref: RepoRef
    name: str
    full_name: str
    description: str | None = None
    author: str | None = None
    stars: int = 0
    forks: int = 0
    license: str | None = None
    default_branch: str = "main"
    commit_sha: str | None = None
    last_commit_at: str | None = None
    language: str | None = None
    topics: list[str] = field(default_factory=list)
    is_private: bool = False
    readme: str | None = None
    html_url: str | None = None
    clone_url: str | None = None
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "full_name": self.full_name,
            "description": self.description,
            "author": self.author,
            "stars": self.stars,
            "forks": self.forks,
            "license": self.license,
            "default_branch": self.default_branch,
            "commit_sha": self.commit_sha,
            "last_commit_at": self.last_commit_at,
            "language": self.language,
            "topics": self.topics,
            "is_private": self.is_private,
            "html_url": self.html_url,
            "clone_url": self.clone_url,
        }


def _headers(token: str | None) -> dict[str, str]:
    headers = {
        "User-Agent": "OPENLAB/0.1 (+https://github.com)",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_metadata(ref: RepoRef, token: str | None = None, timeout: float = 30.0) -> RepoMetadata:
    """Look up repository facts. Falls back to a minimal record on API failure."""
    async with build_async_client(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.get(ref.api_url, headers=_headers(token))
        except httpx.HTTPError as exc:
            raise ImportFailed(f"GitHub API unreachable: {exc}") from exc

        if resp.status_code == 404:
            raise ImportFailed(
                f"repository {ref.slug} not found (it may be private, renamed or deleted -- "
                "connect a token in Model Providers / Settings for private repos)"
            )
        if resp.status_code == 401:
            raise ImportFailed("GitHub rejected the configured token (401)")
        if resp.status_code == 403:
            raise ImportFailed("GitHub API rate limit or permission error (403)")
        if resp.status_code >= 400:
            raise ImportFailed(f"GitHub API error {resp.status_code}")

        data = resp.json()
        license_info = data.get("license") or {}
        meta = RepoMetadata(
            ref=ref,
            name=data.get("name") or ref.repo,
            full_name=data.get("full_name") or ref.slug,
            description=data.get("description"),
            author=(data.get("owner") or {}).get("login"),
            stars=int(data.get("stargazers_count") or 0),
            forks=int(data.get("forks_count") or 0),
            license=license_info.get("spdx_id") or license_info.get("name"),
            default_branch=data.get("default_branch") or "main",
            last_commit_at=data.get("pushed_at") or data.get("updated_at"),
            language=data.get("language"),
            topics=list(data.get("topics") or []),
            is_private=bool(data.get("private")),
            html_url=data.get("html_url") or ref.html_url,
            clone_url=data.get("clone_url") or ref.clone_url,
            raw={k: v for k, v in data.items() if k in _KEEP_FIELDS},
        )
        if ref.ref:
            meta.default_branch = ref.ref

        commit = await _latest_commit(client, ref, meta.default_branch, token)
        if commit:
            meta.commit_sha = commit.get("sha")
            meta.last_commit_at = (commit.get("commit") or {}).get("committer", {}).get("date") or meta.last_commit_at
        meta.readme = await fetch_readme(client, ref, meta.default_branch, token)
        return meta


_KEEP_FIELDS = {
    "id",
    "node_id",
    "size",
    "open_issues_count",
    "watchers_count",
    "archived",
    "disabled",
    "homepage",
    "created_at",
    "updated_at",
    "pushed_at",
    "has_wiki",
    "has_issues",
    "subscribers_count",
    "default_branch",
    "visibility",
}


async def _latest_commit(
    client: httpx.AsyncClient, ref: RepoRef, branch: str, token: str | None
) -> dict | None:
    url = f"https://api.github.com/repos/{ref.owner}/{ref.repo}/commits/{branch}"
    try:
        resp = await client.get(url, headers=_headers(token))
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def fetch_readme(
    client: httpx.AsyncClient, ref: RepoRef, branch: str, token: str | None, limit: int = 20000
) -> str | None:
    for name in ("README.md", "readme.md", "README.rst", "README.txt"):
        url = f"https://raw.githubusercontent.com/{ref.owner}/{ref.repo}/{branch}/{name}"
        try:
            resp = await client.get(url, headers={"User-Agent": "OPENLAB/0.1"})
        except httpx.HTTPError:
            continue
        if resp.status_code == 200 and resp.text.strip():
            return resp.text[:limit]
    return None


@dataclass
class SourceBundle:
    path: Path
    commit_sha: str | None
    stats: ExtractStats


async def download_source(
    ref: RepoRef,
    target_dir: Path,
    branch: str,
    commit_sha: str | None = None,
    token: str | None = None,
    max_download_mb: int = 512,
    max_unpacked_mb: int = 2048,
    max_files: int = 60000,
    progress=None,
) -> SourceBundle:
    """Download the branch (or exact commit) as a ZIP and extract it safely."""
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    if commit_sha:
        url = f"https://codeload.github.com/{ref.owner}/{ref.repo}/zip/{commit_sha}"
    else:
        url = f"https://codeload.github.com/{ref.owner}/{ref.repo}/zip/refs/heads/{branch}"
    archive_path = target_dir.parent / f".{target_dir.name}-{commit_sha or branch}.zip"
    max_bytes = max_download_mb * 1024 * 1024
    total = 0
    digest = hashlib.sha256()
    try:
        async with build_async_client(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=_headers(token)) as resp:
                if resp.status_code == 404:
                    raise ImportFailed(f"source archive not found for {ref.slug}@{branch}")
                if resp.status_code >= 400:
                    raise ImportFailed(f"download failed with HTTP {resp.status_code}")
                with archive_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise ImportFailed(
                                f"repository archive exceeds the {max_download_mb}MB download limit"
                            )
                        digest.update(chunk)
                        fh.write(chunk)
                        if progress is not None:
                            await progress(total)
    except httpx.HTTPError as exc:
        raise ImportFailed(f"download failed: {exc}") from exc

    if not archive_path.exists() or archive_path.stat().st_size == 0:
        raise ImportFailed("downloaded archive is empty")
    if not zipfile.is_zipfile(archive_path):
        raise ImportFailed("downloaded file is not a valid ZIP archive")
    try:
        stats = extract_zip(
            archive_path,
            target_dir,
            max_files=max_files,
            max_unpacked_mb=max_unpacked_mb,
        )
    except ArchiveRejected as exc:
        raise ImportFailed(str(exc)) from exc
    finally:
        archive_path.unlink(missing_ok=True)
    return SourceBundle(path=target_dir, commit_sha=commit_sha, stats=stats)


def archive_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_readme(source_dir: Path) -> str | None:
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise UnsafePath(f"source directory missing: {source_dir}")
    for candidate in sorted(source_dir.iterdir()):
        if candidate.is_file() and re.fullmatch(r"readme(\.(md|rst|txt|markdown))?", candidate.name, re.I):
            try:
                return candidate.read_text(encoding="utf-8", errors="replace")[:20000]
            except OSError:
                continue
    return None
