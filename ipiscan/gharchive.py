"""Stream GH Archive (https://www.gharchive.org): one gzipped file of public GitHub
events per hour. Each file is decompressed on the fly and never stored.

Note: GH Archive no longer captures all GitHub activity. In 2026 it holds a small
fraction of the issue volume that the GitHub API reports, and pull-request bodies are
missing from its events since October 2025. Rates measured on it are usable; counts
are lower bounds. For complete coverage of a time window use `ipi-scan search`.
"""
from __future__ import annotations

import gzip
import io
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Iterator

from .items import Item

URL = "https://data.gharchive.org/{:%Y-%m-%d}-{}.json.gz"


def hours(start: datetime, end: datetime, only_hours: list[int] | None = None) -> Iterator[datetime]:
    t = start.replace(minute=0, second=0, microsecond=0)
    while t < end:
        if only_hours is None or t.hour in only_hours:
            yield t
        t += timedelta(hours=1)


def _open(hour: datetime):
    url = URL.format(hour, hour.hour)
    for attempt in range(5):
        try:
            resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ipiscan"}), timeout=120)
            return gzip.GzipFile(fileobj=io.BufferedReader(resp))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 ** attempt)
        except OSError:
            time.sleep(2 ** attempt)
    return None


def hour_items(hour: datetime) -> Iterator[Item]:
    """Every newly opened issue or PR, and every new comment or review, in one hour."""
    stream = _open(hour)
    if stream is None:
        return
    with stream:
        for line in stream:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            item = _event_item(ev)
            if item is not None:
                yield item


def _event_item(ev: dict) -> Item | None:
    t, p = ev.get("type"), ev.get("payload") or {}
    repo = (ev.get("repo") or {}).get("name") or ""
    if t == "IssuesEvent" and p.get("action") == "opened":
        obj, surface, title = p.get("issue") or {}, "issue", True
    elif t == "PullRequestEvent" and p.get("action") == "opened":
        obj, surface, title = p.get("pull_request") or {}, "pull_request", True
    elif t == "IssueCommentEvent" and p.get("action") == "created":
        obj, title = p.get("comment") or {}, False
        surface = "pr_comment" if (p.get("issue") or {}).get("pull_request") else "issue_comment"
    elif t == "PullRequestReviewCommentEvent" and p.get("action") == "created":
        obj, surface, title = p.get("comment") or {}, "review_comment", False
    elif t == "PullRequestReviewEvent":
        obj, surface, title = p.get("review") or {}, "review", False
    else:
        return None
    user = obj.get("user") or {}
    login = user.get("login") or (ev.get("actor") or {}).get("login") or ""
    return Item(
        url=obj.get("html_url") or "", repo=repo, surface=surface,
        created_at=obj.get("created_at") or obj.get("submitted_at") or ev.get("created_at") or "",
        title=(obj.get("title") or "") if title else "", body=obj.get("body") or "",
        author=login, author_association=obj.get("author_association") or "",
        author_is_bot=user.get("type") == "Bot" or login.endswith("[bot]"),
    )
