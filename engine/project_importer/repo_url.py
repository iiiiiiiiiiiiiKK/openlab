"""Repository identity parsing for GitHub (and generic) URLs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

GITHUB_HOSTS = {"github.com", "www.github.com"}

_GITHUB_PATH = re.compile(
    r"^/(?P<owner>[A-Za-z0-9][A-Za-z0-9._-]{0,98})/(?P<repo>[A-Za-z0-9._-]{1,99}?)(?:\.git)?"
    r"(?:/(?:tree|blob)/(?P<ref>[^/]+))?(?:/.*)?$"
)
_SSH_GITHUB = re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")

RESERVED_GITHUB_PATHS = {
    "orgs",
    "users",
    "explore",
    "topics",
    "marketplace",
    "pricing",
    "features",
    "sponsors",
    "collections",
    "trending",
    "settings",
    "notifications",
    "login",
    "about",
}


class InvalidRepoUrl(ValueError):
    """Raised when a string is not a recognisable repository address."""


@dataclass(frozen=True)
class RepoRef:
    host: str
    owner: str
    repo: str
    ref: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def html_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://{self.host}/{self.owner}/{self.repo}.git"

    @property
    def api_url(self) -> str:
        return f"https://api.{self.host}/repos/{self.owner}/{self.repo}"

    @property
    def codeload_zip(self) -> str:
        if self.ref:
            return f"https://codeload.{self.host}/{self.owner}/{self.repo}/zip/refs/heads/{self.ref}"
        return f"https://codeload.{self.host}/{self.owner}/{self.repo}/zip/refs/heads/HEAD"


def parse_repo_url(value: str) -> RepoRef:
    """Parse a GitHub URL, ``owner/repo`` shorthand or SSH remote into a RepoRef."""
    if not value or not value.strip():
        raise InvalidRepoUrl("repository address is empty")
    text = value.strip()
    ssh = _SSH_GITHUB.match(text)
    if ssh:
        return _build(ssh.group("owner"), ssh.group("repo"))

    if "://" not in text:
        shorthand = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9._-]+?)(?:\.git)?/?", text)
        if shorthand and shorthand.group(1).lower() not in RESERVED_GITHUB_PATHS | {"github.com"}:
            return _build(shorthand.group(1), shorthand.group(2))
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.netloc or "").lower()
    if host not in GITHUB_HOSTS:
        if host == "raw.githubusercontent.com":
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2:
                return _build(parts[0], parts[1])
        raise InvalidRepoUrl(f"unsupported repository host: {host or text}")

    match = _GITHUB_PATH.match(parsed.path or "")
    if not match:
        raise InvalidRepoUrl(f"cannot parse repository path: {parsed.path}")
    owner = match.group("owner")
    if owner.lower() in RESERVED_GITHUB_PATHS:
        raise InvalidRepoUrl(f"{owner} is a GitHub site path, not a user or organisation")
    ref = match.group("ref")
    return _build(owner, match.group("repo"), ref)


def _build(owner: str, repo: str, ref: str | None = None) -> RepoRef:
    repo = repo.strip().rstrip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        raise InvalidRepoUrl("repository name is empty")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repo):
        raise InvalidRepoUrl(f"invalid repository name: {repo}")
    return RepoRef(host="github.com", owner=owner, repo=repo, ref=ref)


def default_project_name(ref: RepoRef) -> str:
    return ref.repo.replace("_", "-")


def slugify(value: str, fallback: str = "project") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return cleaned[:64] or fallback
