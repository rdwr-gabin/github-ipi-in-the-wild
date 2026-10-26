"""Minimal GitHub REST client: issues, pull requests, comments, READMEs, template files,
and the Search API for "every new issue and PR in a time window".

Reads a token from GITHUB_TOKEN (or GH_TOKEN). Without one, GitHub allows 60 requests
an hour, enough only for a very small repository.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .items import Item

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str | None = None, *, verbose: bool = False):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.verbose = verbose

    # -- transport ------------------------------------------------------------------
    def _request(self, url: str, accept: str = "application/vnd.github+json") -> tuple[object, dict]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ipiscan"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(6):
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read()
                    body = json.loads(raw) if "json" in (resp.headers.get("Content-Type") or "") else raw.decode("utf-8", "replace")
                    return body, dict(resp.headers)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None, {}
                if e.code in (403, 429) and (e.headers.get("X-RateLimit-Remaining") == "0" or e.headers.get("Retry-After")):
                    reset = e.headers.get("Retry-After") or str(max(1, int(e.headers.get("X-RateLimit-Reset", "0")) - int(time.time())))
                    wait = min(int(reset) + 2, 3600)
                    self._log(f"rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if e.code >= 500 and attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise GitHubError(f"{e.code} {url}: {e.read()[:200]!r}") from e
            except OSError:
                if attempt < 5:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise GitHubError(f"giving up on {url}")

    def get(self, path: str, **params) -> object:
        url = path if path.startswith("http") else f"{API}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return self._request(url)[0]

    def paginate(self, endpoint: str, **params) -> Iterator[dict]:
        params.setdefault("per_page", 100)
        url = f"{API}{endpoint}?{urllib.parse.urlencode(params)}"
        while url:
            body, headers = self._request(url)
            if not body:
                return
            yield from (body["items"] if isinstance(body, dict) and "items" in body else body)
            url = _next_link(headers.get("Link") or headers.get("link") or "")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[github] {msg}", flush=True)

    # -- one repository -------------------------------------------------------------
    def repo_items(self, repo: str, since: str | None = None, comments: bool = True) -> Iterator[Item]:
        """Every issue, pull request and (optionally) comment in `repo`, optionally only
        those created or updated since an ISO date."""
        extra = {"since": since} if since else {}
        for it in self.paginate(f"/repos/{repo}/issues", state="all", **extra):
            if since and (it.get("created_at") or "") < since:
                continue
            yield _issue_item(repo, it)
        if not comments:
            return
        for c in self.paginate(f"/repos/{repo}/issues/comments", **extra):
            surface = "pr_comment" if "/pull/" in (c.get("html_url") or "") else "issue_comment"
            yield _comment_item(repo, c, surface)
        for c in self.paginate(f"/repos/{repo}/pulls/comments", **extra):
            yield _comment_item(repo, c, "review_comment")

    def readme(self, repo: str) -> Item | None:
        body = self.get(f"/repos/{repo}/readme")
        if not body or "content" not in body:
            return None
        text = base64.b64decode(body["content"]).decode("utf-8", "replace")
        return Item(url=body.get("html_url") or f"https://github.com/{repo}", repo=repo, surface="readme", body=text)

    def file_text(self, repo: str, path: str, ref: str | None = None) -> str | None:
        params = {"ref": ref} if ref else {}
        body = self.get(f"/repos/{repo}/contents/{urllib.parse.quote(path)}", **params)
        if not body or not isinstance(body, dict) or "content" not in body:
            return None
        return base64.b64decode(body["content"]).decode("utf-8", "replace")

    def tree_paths(self, repo: str) -> list[str]:
        meta = self.get(f"/repos/{repo}")
        if not meta:
            return []
        tree = self.get(f"/repos/{repo}/git/trees/{meta['default_branch']}", recursive=1)
        return [t["path"] for t in (tree or {}).get("tree", []) if t.get("type") == "blob"]

    def file_commits(self, repo: str, path: str, since: str, limit: int) -> list[str]:
        shas = []
        for c in self.paginate(f"/repos/{repo}/commits", path=path, since=since):
            shas.append(c["sha"])
            if len(shas) >= limit:
                break
        return shas

    # -- all of GitHub, by creation time ----------------------------------------------
    def search_new_items(self, start: datetime, end: datetime, kinds=("issue", "pr")) -> Iterator[Item]:
        """Every public issue and PR created in [start, end). The Search API returns at
        most 1,000 results per query, so windows that are too full are split in half."""
        for kind in kinds:
            stack = [(start, end)]
            while stack:
                a, b = stack.pop()
                q = f"is:{kind} is:public created:{_iso(a)}..{_iso(b - timedelta(seconds=1))}"
                first = self.get("/search/issues", q=q, per_page=100, sort="created", order="asc")
                total = (first or {}).get("total_count", 0)
                if total > 1000 and b - a > timedelta(seconds=2):
                    mid = a + (b - a) / 2
                    stack += [(mid, b), (a, mid)]
                    continue
                self._log(f"{kind} {_iso(a)} .. {_iso(b)}: {total}")
                for it in self.paginate("/search/issues", q=q, sort="created", order="asc"):
                    repo = "/".join(it["repository_url"].split("/")[-2:])
                    yield _issue_item(repo, it)


def _next_link(link: str) -> str | None:
    for part in link.split(","):
        if 'rel="next"' in part:
            return part[part.find("<") + 1: part.find(">")]
    return None


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(d: dict) -> tuple[str, bool]:
    u = d.get("user") or {}
    login = u.get("login") or ""
    return login, u.get("type") == "Bot" or login.endswith("[bot]")


def _issue_item(repo: str, it: dict) -> Item:
    login, bot = _user(it)
    return Item(
        url=it.get("html_url") or "", repo=repo,
        surface="pull_request" if it.get("pull_request") else "issue",
        created_at=it.get("created_at") or "", title=it.get("title") or "", body=it.get("body") or "",
        author=login, author_association=it.get("author_association") or "", author_is_bot=bot,
    )


def _comment_item(repo: str, c: dict, surface: str) -> Item:
    login, bot = _user(c)
    return Item(
        url=c.get("html_url") or "", repo=repo, surface=surface, created_at=c.get("created_at") or "",
        body=c.get("body") or "", author=login, author_association=c.get("author_association") or "",
        author_is_bot=bot,
    )
