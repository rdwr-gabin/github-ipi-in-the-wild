"""Run the full pipeline over a stream of items and write the results to one folder.

  1. detect     find hidden regions; drop known bot markers           -> hidden_text.jsonl
  2. stage 1    fast LLM screen of every item with hidden text        -> stage1.jsonl
  3. stage 2    second LLM pass over the stage-1 positives            -> stage2.jsonl
  4. templates  mark regions copied from the repo's issue/PR template
  5. label      one category per finding                              -> findings.jsonl, summary.md

READMEs take a separate path in steps 2 to 5: the README prompt judges every hidden
snippet on its own and its intent gives the category (readme.jsonl).

Every step writes as it goes and skips work already on disk, so re-running the same
command resumes an interrupted scan.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Callable, Iterable

from . import detect
from .classify import hidden_text, run_readme, run_stage1, run_stage2
from .filters import apply_noise_filter, authorship
from .github import GitHub
from .items import PROJECT_ROLES, Item
from .llm import LLM
from .templates import TemplateCatalog, mark_template_regions

CATEGORIES = {
    "confirmed_attack": "attack",
    "confirmed_defensive_ipi": "defensive",
    "confirmed_content_override": "content_override",
    "uncertain": "uncertain",
}
REVIEW_HINT_PROJECT = ("Written by the project itself. Check whether the project's own agents or agent tooling "
                       "read this text; if so it is first-party orchestration, not an injection aimed at someone else.")


def collect(items: Iterable[Item], out: Path, techniques_for: Callable[[Item], tuple], *,
            progress_every: int = 10000) -> list[Item]:
    """Step 1. Scans every item with the techniques `techniques_for(item)` returns, keeps
    those with hidden text, appends them to hidden_text.jsonl and counts what was scanned
    in scanned.json."""
    path = out / "hidden_text.jsonl"
    stats_path = out / "scanned.json"
    stats: collections.Counter = collections.Counter()   # counts this run's whole source
    written = set()
    if path.exists():
        written = {json.loads(line)["url"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    with path.open("a", encoding="utf-8") as fh:
        for item in items:
            if not item.url:
                continue
            stats["items_scanned"] += 1
            stats[f"scanned_{item.surface}"] += 1
            item.regions = detect.scan(item.text, techniques_for(item))
            if item.regions and apply_noise_filter(item):
                stats["items_with_hidden_text"] += 1
                if item.url not in written:
                    written.add(item.url)
                    fh.write(json.dumps(item.to_dict(), ensure_ascii=False) + "\n")
            if stats["items_scanned"] % progress_every == 0:
                print(f"scanned {stats['items_scanned']:,} items, {stats['items_with_hidden_text']:,} with hidden text", flush=True)
                stats_path.write_text(json.dumps(stats, indent=1))
    stats_path.write_text(json.dumps(stats, indent=1))
    return load_items(path)


def load_items(path: Path) -> list[Item]:
    if not path.exists():
        return []
    return [Item.from_dict(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify(items: list[Item], out: Path, llm: LLM, gh: GitHub | None, *, workers: int, template_since: str) -> list[dict]:
    """Steps 2 to 5."""
    readmes = [it for it in items if it.surface == "readme"]
    items = [it for it in items if it.surface != "readme"]

    s1 = run_stage1(llm, items, out / "stage1.jsonl", workers) if items else {}
    positives = [it for it in items if s1.get(it.url, {}).get("is_injection")]
    s2 = run_stage2(llm, positives, s1, out / "stage2.jsonl", workers) if positives else {}
    kept = [it for it in positives if s2.get(it.url, {}).get("keep_finding")]

    catalog = TemplateCatalog(gh, out / "template_cache", template_since) if gh else None
    findings = []
    for it in kept:
        if catalog:
            try:
                mark_template_regions(it.regions, catalog.for_repo(it.repo))
            except Exception as e:  # template check failed: keep every region, say so
                print(f"template check failed for {it.repo}: {e}", flush=True)
        findings.append(_finding(it, s1[it.url], s2[it.url]))

    rd = run_readme(llm, readmes, out / "readme.jsonl", workers) if readmes else {}
    findings += [_readme_finding(it, rd[it.url]) for it in readmes if rd.get(it.url, {}).get("is_injection")]

    _write(out, findings, len(items) + len(readmes), len(positives) + len(findings) - len(kept))
    return findings


# README intent -> category; when one README has several, the first in this order wins.
README_INTENTS = [("malicious", "attack"), ("anti_ai", "defensive"), ("self_serving", "content_override"),
                  ("operational", "content_override")]


def _readme_finding(it: Item, verdict: dict) -> dict:
    pos = [v for v in verdict["snippets"] if v.get("is_injection")]
    intents = {v.get("intent") for v in pos}
    category = next((c for i, c in README_INTENTS if i in intents), "uncertain")
    return {
        "url": it.url, "repo": it.repo, "surface": "readme", "created_at": "", "author": "", "author_association": "",
        "title": "", "category": category, "objective": ", ".join(sorted(i for i in intents if i)), "target": None,
        "confidence": max((v.get("confidence") or 0) for v in pos), "reason": " / ".join(v.get("reason") or "" for v in pos),
        "techniques": sorted({v["technique"] for v in pos}),
        "hidden_text": "\n\n---\n\n".join(v["hidden_text"] for v in pos),
        "template_regions": [], "stage1": {}, "review_hint": "",
    }


def _finding(it: Item, s1: dict, s2: dict) -> dict:
    authored = [r for r in it.regions if not r.get("template_match")]
    template = [r for r in it.regions if r.get("template_match")]
    category = "template_text" if not authored else CATEGORIES.get(s2.get("verdict"), "uncertain")
    project = it.author_association in PROJECT_ROLES or authorship(it, hidden_text(it)) in ("owner", "bot", "trusted_tool")
    return {
        "url": it.url, "repo": it.repo, "surface": it.surface, "created_at": it.created_at,
        "author": it.author, "author_association": it.author_association, "title": it.title,
        "category": category,
        "objective": s2.get("objective"), "target": s2.get("target"), "confidence": s2.get("confidence"),
        "reason": s2.get("reason"),
        "techniques": sorted({r["technique"] for r in authored or template}),
        "hidden_text": "\n\n---\n\n".join(r["hidden_text"] for r in authored or template),
        "template_regions": [{"file": r["template_match"]["file"], "ref": r["template_match"]["ref"],
                              "method": r["template_match"]["method"]} for r in template],
        "stage1": {k: s1.get(k) for k in ("objective", "target", "confidence", "reason")},
        "review_hint": REVIEW_HINT_PROJECT if project and category not in ("template_text",) else "",
    }


def _write(out: Path, findings: list[dict], n_hidden: int, n_stage1: int) -> None:
    with (out / "findings.jsonl").open("w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    stats = json.loads((out / "scanned.json").read_text()) if (out / "scanned.json").exists() else {}
    cats = collections.Counter(f["category"] for f in findings)
    lines = [
        "# Scan summary", "",
        "| Step | Items |", "|---|---:|",
        f"| Scanned | {stats.get('items_scanned', 0):,} |",
        f"| With hidden text | {n_hidden:,} |",
        f"| Flagged by the first model | {n_stage1:,} |",
        f"| Findings (kept by stage 2, or README snippets judged AI-directed) | {len(findings):,} |",
        f"| ... of which template text only | {cats.get('template_text', 0):,} |", "",
        "| Category | Findings |", "|---|---:|",
        *[f"| {c} | {n:,} |" for c, n in cats.most_common()], "",
        "Every finding below still needs a person to read it. Items marked *project* were written by",
        "the project's owner, a member, a collaborator or its bot: check whether the project's own",
        "agents read that text before calling it an injection.", "",
        "| Category | Item | Project | Hidden text |", "|---|---|---|---|",
    ]
    order = {"attack": 0, "uncertain": 1, "content_override": 2, "defensive": 3, "template_text": 4}
    for f in sorted(findings, key=lambda f: (order.get(f["category"], 9), f["repo"])):
        snippet = f["hidden_text"].replace("\n", " ").replace("|", "\\|")[:160]
        lines.append(f"| {f['category']} | {f['url']} | {'project' if f['review_hint'] else ''} | {snippet} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{len(findings)} findings: " + ", ".join(f"{c} {n}" for c, n in cats.most_common()))
    print(f"results in {out / 'findings.jsonl'} and {out / 'summary.md'}")
