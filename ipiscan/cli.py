"""Command line: ipi-scan <source> [options]

Sources
  repo OWNER/NAME     issues, pull requests, comments and the README of one repository
  readme OWNER/NAME.. READMEs of one or more repositories (or --file with one per line)
  search              every public issue and PR created in a time window (GitHub Search API)
  archive             issues, PRs, comments and reviews from GH Archive hourly files
  text FILE           a local Markdown file, detection only (no network, no keys)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import detect, gharchive, pipeline
from .github import GitHub
from .llm import LLM, default_provider


def _date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", type=Path, help="results folder (default: results/<source>-<name>)")
    p.add_argument("--detect-only", action="store_true", help="stop after finding hidden text; no model calls")
    p.add_argument("--provider", choices=["openai", "anthropic"], help="model provider (default: whichever key is set, OpenAI first)")
    p.add_argument("--stage1-model", help="override the stage-1 model")
    p.add_argument("--stage2-model", help="override the stage-2 model")
    p.add_argument("--workers", type=int, default=8, help="parallel model calls (default 8)")
    p.add_argument("--all-techniques", action="store_true",
                   help="also look for collapsed <details>, invisible Unicode, white LaTeX and CSS hiding in issues and PRs")
    p.add_argument("--no-templates", action="store_true", help="skip the template-provenance check")
    p.add_argument("--template-since", default="2025-08-01",
                   help="also compare against template versions committed since this date (default 2025-08-01)")
    p.add_argument("-v", "--verbose", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ipi-scan", description="Find hidden, AI-directed text on GitHub.")
    sub = ap.add_subparsers(dest="source", required=True)

    p = sub.add_parser("repo", help="one repository: issues, PRs, comments and README")
    p.add_argument("repo", help="OWNER/NAME")
    p.add_argument("--since", help="only items created on or after this date (YYYY-MM-DD)")
    p.add_argument("--no-comments", action="store_true", help="skip comments and review comments")
    p.add_argument("--no-readme", action="store_true")
    _common(p)

    p = sub.add_parser("readme", help="READMEs of one or more repositories")
    p.add_argument("repos", nargs="*", help="OWNER/NAME ...")
    p.add_argument("--file", type=Path, help="text file with one OWNER/NAME per line")
    _common(p)

    p = sub.add_parser("search", help="every public issue and PR created in a time window")
    p.add_argument("--from", dest="start", required=True, help="start, e.g. 2026-10-01 or 2026-10-01T13:00")
    p.add_argument("--to", dest="end", required=True, help="end (exclusive)")
    _common(p)

    p = sub.add_parser("archive", help="GH Archive hourly files")
    p.add_argument("--from", dest="start", required=True, help="first hour, e.g. 2026-05-01 or 2026-05-01T13")
    p.add_argument("--to", dest="end", required=True, help="end (exclusive)")
    p.add_argument("--hours", help="only these hours of each day, e.g. 0,6,12,18 (default: every hour)")
    _common(p)

    p = sub.add_parser("text", help="a local Markdown file, detection only")
    p.add_argument("file", type=Path)
    p.add_argument("--all-techniques", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)

    if args.source == "text":
        techniques = detect.ALL_TECHNIQUES if args.all_techniques else detect.README_TECHNIQUES
        regions = detect.scan(args.file.read_text(encoding="utf-8", errors="replace"), techniques)
        print(json.dumps(regions, ensure_ascii=False, indent=1))
        return 0

    gh = GitHub(verbose=args.verbose)
    issue_techniques = detect.ALL_TECHNIQUES if args.all_techniques else detect.ISSUE_TECHNIQUES

    if args.source == "repo":
        name = args.repo.replace("/", "__")
        since = f"{args.since}T00:00:00Z" if args.since else None
        def items():
            yield from gh.repo_items(args.repo, since=since, comments=not args.no_comments)
            if not args.no_readme and (readme := gh.readme(args.repo)):
                yield readme
        source = items()
    elif args.source == "readme":
        repos = list(args.repos) + ([l.strip() for l in args.file.read_text().splitlines() if l.strip()] if args.file else [])
        if not repos:
            raise SystemExit("give at least one OWNER/NAME or --file")
        name = repos[0].replace("/", "__") if len(repos) == 1 else f"{len(repos)}-repos"
        source = (r for r in (gh.readme(x) for x in repos) if r)
    elif args.source == "search":
        start, end = _date(args.start), _date(args.end)
        name = f"{start:%Y%m%dT%H}-{end:%Y%m%dT%H}"
        source = gh.search_new_items(start, end)
    else:
        start, end = _date(args.start), _date(args.end)
        only = [int(h) for h in args.hours.split(",")] if args.hours else None
        name = f"{start:%Y%m%dT%H}-{end:%Y%m%dT%H}"
        source = (it for h in gharchive.hours(start, end, only) for it in gharchive.hour_items(h))

    out = args.out or Path("results") / f"{args.source}-{name}"
    out.mkdir(parents=True, exist_ok=True)

    # READMEs get every README technique; issue, PR and comment text gets the chosen set.
    def techniques_for(item):
        return detect.README_TECHNIQUES if item.surface == "readme" else issue_techniques

    collected = pipeline.collect(source, out, techniques_for)
    print(f"{len(collected)} items with hidden text -> {out / 'hidden_text.jsonl'}")
    if args.detect_only or not collected:
        return 0

    llm = LLM(args.provider or default_provider(), args.stage1_model, args.stage2_model)
    pipeline.classify(collected, out, llm, None if args.no_templates else gh,
                      workers=args.workers, template_since=f"{args.template_since}T00:00:00Z")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
