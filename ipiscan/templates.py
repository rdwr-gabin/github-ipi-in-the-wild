"""Template provenance: was a hidden region written by the item's author, or inserted
by GitHub from the repository's issue / pull-request template?

Template authors put guidance in HTML comments so that it stays invisible, and GitHub
copies the template into every new issue or PR. The detector cannot tell that text
from text the author hid. In our data, template text made up the majority of what the
classifiers confirmed, so this step is essential.

For each repository the step fetches every GitHub issue / PR template file (in the
repository and in the owner's `.github` repository), at its current version and at
each version committed since --since, collects the HTML comments they contain, and
compares every hidden region against them: exact match after normalisation, one text
containing the other (at least half the length), or word-set (Jaccard) similarity of
0.85 or more.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .github import GitHub

try:
    import yaml
except ImportError:  # issue forms are still scanned as raw text
    yaml = None

# Paths GitHub treats as issue / PR templates (repository root, .github/ or docs/).
TEMPLATE_PATH_RE = re.compile(
    r"^(?:\.github/|docs/)?(?:pull_request_template|issue_template)(?:/[^/]+)?(?:\.(?:md|txt|yml|yaml|markdown))?$",
    re.IGNORECASE)
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
MIN_FUZZY_CHARS = 20
JACCARD_MIN = 0.85
CONTAIN_MIN_RATIO = 0.5
MAX_FILES = 40


def norm(text: str) -> str:
    return _WS.sub(" ", _NONALNUM.sub(" ", (text or "").lower())).strip()


def _yaml_strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _yaml_strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _yaml_strings(v)]
    return []


def template_comments(path: str, text: str) -> list[str]:
    """HTML comments a template contributes. Issue forms (YAML) insert their strings into
    the new issue, so comments inside those strings count too."""
    sources = [text]
    if path.lower().endswith((".yml", ".yaml")) and yaml is not None:
        try:
            sources += _yaml_strings(yaml.safe_load(text))
        except Exception:
            pass
    return [m.group(1).strip() for s in sources for m in _COMMENT_RE.finditer(s or "") if len(m.group(1).strip()) >= 4]


def match(region: str, catalog: list[dict]) -> dict | None:
    """Best catalog entry matching `region`, or None."""
    nr = norm(region)
    if not nr:
        return None
    tr = set(nr.split())
    best = None
    for c in catalog:
        nt = c["norm"]
        if nr == nt:
            cand = (1.0, "exact")
        elif len(nr) >= MIN_FUZZY_CHARS and len(nt) >= MIN_FUZZY_CHARS and (nr in nt or nt in nr):
            ratio = min(len(nr), len(nt)) / max(len(nr), len(nt))
            if ratio < CONTAIN_MIN_RATIO:
                continue
            cand = (ratio, "containment")
        elif len(nr) >= MIN_FUZZY_CHARS:
            j = len(tr & c["tokens"]) / (len(tr | c["tokens"]) or 1)
            if j < JACCARD_MIN:
                continue
            cand = (j, "jaccard")
        else:
            continue
        if best is None or cand[0] > best[0]:
            best = (*cand, c)
            if cand[0] >= 1.0:
                break
    if best is None:
        return None
    return {"similarity": round(best[0], 3), "method": best[1], "file": best[2]["file"], "ref": best[2]["ref"]}


class TemplateCatalog:
    """Template comments per repository, fetched once and cached on disk."""

    def __init__(self, gh: GitHub, cache_dir: Path, since: str, max_versions: int = 30):
        self.gh, self.since, self.max_versions = gh, since, max_versions
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, list[dict]] = {}

    def _fetch(self, repo: str) -> list[dict]:
        entries = []
        paths = [p for p in self.gh.tree_paths(repo) if TEMPLATE_PATH_RE.match(p)][:MAX_FILES]
        for path in paths:
            refs = [None] + self.gh.file_commits(repo, path, self.since, self.max_versions)
            seen = set()
            for ref in refs:
                text = self.gh.file_text(repo, path, ref)
                if not text or text in seen:
                    continue
                seen.add(text)
                for c in template_comments(path, text):
                    n = norm(c)
                    if n:
                        entries.append({"norm": n, "file": f"{repo}:{path}", "ref": (ref or "HEAD")[:12]})
        return entries

    def for_repo(self, repo: str) -> list[dict]:
        if repo in self._mem:
            return self._mem[repo]
        owner = repo.split("/")[0]
        out = []
        for r in (repo, f"{owner}/.github"):
            path = self.cache_dir / (r.replace("/", "__") + ".json")
            if path.exists():
                entries = json.loads(path.read_text(encoding="utf-8"))
            else:
                entries = self._fetch(r)
                path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            out += entries
        for e in out:
            e["tokens"] = set(e["norm"].split())
        self._mem[repo] = out
        return out


def mark_template_regions(regions: list[dict], catalog: list[dict]) -> list[dict]:
    """Annotate each region with `template_match` (a dict, or None when the author wrote it)."""
    for r in regions:
        r["template_match"] = match(r.get("hidden_text") or "", catalog) if catalog else None
    return regions
