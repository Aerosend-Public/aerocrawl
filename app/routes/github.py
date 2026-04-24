"""GitHub route — REST API for repos, issues, PRs.

Authenticates with GITHUB_PAT if available (bumps unauth 60/hr → authed 5k/hr).
Unauthed calls still work, just rate-limited.
"""
from __future__ import annotations

import json
import re
from typing import Optional

import httpx
import structlog

from app.config import settings
from app.routes.base import RouteResult

logger = structlog.get_logger()

_BASE = "https://api.github.com"

# Precompiled URL patterns
_RE_REPO = re.compile(r"^/([^/]+)/([^/]+)/?$")
_RE_ISSUE = re.compile(r"^/([^/]+)/([^/]+)/issues/(\d+)/?$")
_RE_PR = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)/?$")
_RE_RELEASE = re.compile(r"^/([^/]+)/([^/]+)/releases/tag/([^/]+)/?$")


class GitHubRoute:
    name = "github"
    description = "GitHub repos, issues, PRs, releases via REST API v3"

    def matches(self, url: str) -> bool:
        return "github.com/" in url and "://" in url

    async def fetch(self, url: str, only_main_content: bool = True) -> Optional[RouteResult]:
        path = self._url_path(url)
        if path is None:
            return None

        m = _RE_ISSUE.match(path)
        if m:
            return await self._fetch_issue(*m.groups(), url=url)
        m = _RE_PR.match(path)
        if m:
            return await self._fetch_pr(*m.groups(), url=url)
        m = _RE_RELEASE.match(path)
        if m:
            return await self._fetch_release(*m.groups(), url=url)
        m = _RE_REPO.match(path)
        if m:
            return await self._fetch_repo(*m.groups(), url=url)

        return None

    @staticmethod
    def _url_path(url: str) -> Optional[str]:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(url)
        except Exception:
            return None
        if "github.com" not in parsed.netloc:
            return None
        return parsed.path

    async def _gh_get(self, api_path: str) -> Optional[dict]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if settings.GITHUB_PAT:
            headers["Authorization"] = f"Bearer {settings.GITHUB_PAT}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{_BASE}{api_path}", headers=headers)
        except Exception as exc:
            logger.debug("github: request failed", path=api_path, error=str(exc))
            return None
        if resp.status_code != 200:
            logger.debug("github: non-200", path=api_path, status=resp.status_code)
            return None
        try:
            return resp.json()
        except Exception:
            return None

    async def _fetch_repo(self, owner: str, repo: str, url: str) -> Optional[RouteResult]:
        repo = repo.split("#")[0].split("?")[0]
        data = await self._gh_get(f"/repos/{owner}/{repo}")
        if not data:
            return None
        readme = await self._gh_get(f"/repos/{owner}/{repo}/readme")
        readme_md = ""
        if readme and readme.get("content"):
            import base64
            try:
                readme_md = base64.b64decode(readme["content"]).decode("utf-8", errors="replace")
            except Exception:
                pass

        md = (
            f"# {data.get('full_name', f'{owner}/{repo}')}\n\n"
            f"{data.get('description') or ''}\n\n"
            f"- **Stars:** {data.get('stargazers_count', 0)}\n"
            f"- **Forks:** {data.get('forks_count', 0)}\n"
            f"- **Open issues:** {data.get('open_issues_count', 0)}\n"
            f"- **Language:** {data.get('language') or 'unknown'}\n"
            f"- **License:** {(data.get('license') or {}).get('spdx_id') or 'none'}\n"
            f"- **Homepage:** {data.get('homepage') or 'n/a'}\n"
            f"- **Topics:** {', '.join(data.get('topics') or []) or 'none'}\n\n"
        )
        if readme_md:
            md += "## README\n\n" + readme_md

        return RouteResult(
            markdown=md,
            html="",
            final_url=url,
            status_code=200,
            metadata={
                "title": data.get("full_name", ""),
                "description": data.get("description") or "",
                "source_url": url,
                "status_code": 200,
            },
            route_name="github:repo",
            raw_data={"repo": data},
        )

    async def _fetch_issue(self, owner: str, repo: str, num: str, url: str) -> Optional[RouteResult]:
        issue = await self._gh_get(f"/repos/{owner}/{repo}/issues/{num}")
        if not issue:
            return None
        comments = await self._gh_get(f"/repos/{owner}/{repo}/issues/{num}/comments") or []

        md = f"# {issue.get('title', '')}\n\n"
        md += f"**State:** {issue.get('state')} · **Author:** @{(issue.get('user') or {}).get('login')}\n\n"
        md += (issue.get("body") or "") + "\n\n"
        if comments:
            md += "## Comments\n\n"
            for c in comments:
                md += f"### @{(c.get('user') or {}).get('login')}\n{c.get('body', '')}\n\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={
                "title": issue.get("title", ""),
                "description": (issue.get("body") or "")[:200],
                "source_url": url, "status_code": 200,
            },
            route_name="github:issue",
            raw_data={"issue": issue, "comments": comments},
        )

    async def _fetch_pr(self, owner: str, repo: str, num: str, url: str) -> Optional[RouteResult]:
        pr = await self._gh_get(f"/repos/{owner}/{repo}/pulls/{num}")
        if not pr:
            return None
        comments = await self._gh_get(f"/repos/{owner}/{repo}/issues/{num}/comments") or []

        md = f"# {pr.get('title', '')}\n\n"
        md += f"**State:** {pr.get('state')} · **Author:** @{(pr.get('user') or {}).get('login')} · "
        md += f"**Merged:** {pr.get('merged')}\n\n"
        md += f"- Base: `{(pr.get('base') or {}).get('ref')}` ← Head: `{(pr.get('head') or {}).get('ref')}`\n"
        md += f"- Commits: {pr.get('commits')} · Additions: +{pr.get('additions')} · Deletions: -{pr.get('deletions')}\n\n"
        md += (pr.get("body") or "") + "\n\n"
        if comments:
            md += "## Comments\n\n"
            for c in comments:
                md += f"### @{(c.get('user') or {}).get('login')}\n{c.get('body', '')}\n\n"

        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={
                "title": pr.get("title", ""),
                "description": (pr.get("body") or "")[:200],
                "source_url": url, "status_code": 200,
            },
            route_name="github:pr",
            raw_data={"pr": pr, "comments": comments},
        )

    async def _fetch_release(self, owner: str, repo: str, tag: str, url: str) -> Optional[RouteResult]:
        rel = await self._gh_get(f"/repos/{owner}/{repo}/releases/tags/{tag}")
        if not rel:
            return None
        md = f"# {rel.get('name') or rel.get('tag_name')}\n\n"
        md += f"**Tag:** `{rel.get('tag_name')}` · **Published:** {rel.get('published_at')}\n\n"
        md += (rel.get("body") or "") + "\n"
        return RouteResult(
            markdown=md, html="", final_url=url, status_code=200,
            metadata={"title": rel.get("name") or rel.get("tag_name"), "source_url": url, "status_code": 200},
            route_name="github:release",
            raw_data={"release": rel},
        )
