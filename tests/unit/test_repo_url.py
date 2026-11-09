"""Unit tests for repository URL parsing and slugging."""

from __future__ import annotations

import pytest

from engine.project_importer.repo_url import (
    InvalidRepoUrl,
    RepoRef,
    default_project_name,
    parse_repo_url,
    slugify,
)


@pytest.mark.parametrize(
    "value,owner,repo",
    [
        ("https://github.com/expressjs/express", "expressjs", "express"),
        ("http://github.com/expressjs/express/", "expressjs", "express"),
        ("github.com/sindresorhus/slugify", "sindresorhus", "slugify"),
        ("https://github.com/django/django.git", "django", "django"),
        ("expressjs/express", "expressjs", "express"),
        ("git@github.com:vercel/next.js.git", "vercel", "next.js"),
        ("https://github.com/facebook/react/tree/main/packages", "facebook", "react"),
        ("https://www.github.com/torvalds/linux?tab=readme", "torvalds", "linux"),
    ],
)
def test_parse_valid_urls(value: str, owner: str, repo: str) -> None:
    ref = parse_repo_url(value)
    assert isinstance(ref, RepoRef)
    assert ref.owner == owner
    assert ref.repo == repo
    assert ref.host == "github.com"


def test_parse_branch_reference() -> None:
    ref = parse_repo_url("https://github.com/owner/repo/tree/develop")
    assert ref.ref == "develop"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "https://gitlab.com/owner/repo",
        "https://github.com/orgs/expressjs",
        "https://github.com/",
        "https://github.com/owner",
        "not a url at all",
    ],
)
def test_parse_invalid_urls(value: str) -> None:
    with pytest.raises(InvalidRepoUrl):
        parse_repo_url(value)


def test_url_helpers() -> None:
    ref = parse_repo_url("expressjs/express")
    assert ref.html_url == "https://github.com/expressjs/express"
    assert ref.clone_url.endswith(".git")
    assert ref.api_url == "https://api.github.com/repos/expressjs/express"
    assert ref.slug == "expressjs/express"


def test_slugify_and_name() -> None:
    ref = parse_repo_url("vercel/next.js")
    assert default_project_name(ref) == "next.js"
    assert slugify("My Project!!") == "my-project"
    assert slugify("") == "project"
    assert slugify("//") == "project"
    assert len(slugify("a" * 200)) <= 64
