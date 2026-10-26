"""Recompute every published number from the released data files.

Reads data/issues_prs/findings.jsonl, data/readmes/findings.jsonl and the item counts
(denominators) in data/tables/, then prints the categories, rates, monthly trend,
attack and first-party-orchestration statistics, and README counts.

    python scripts/summarize.py           # print everything
    python scripts/summarize.py --check   # also verify the tables; exit 1 on any mismatch

Standard library only.
"""
from __future__ import annotations

import collections
import csv
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
CATEGORIES = ["first_party_orchestration", "content_override", "defensive", "attack"]
COMMENT_SURFACES = {"issue_comment", "pr_comment", "review", "review_comment"}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def share(n: int, d: int) -> str:
    return f"{n:,} of {d:,} ({100 * n / d:.0f}%)" if d else f"{n:,} of 0"


def main() -> int:
    check = "--check" in sys.argv[1:]
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rows = load_jsonl(DATA / "issues_prs/findings.jsonl")
    readmes = load_jsonl(DATA / "readmes/findings.jsonl")
    funnel = json.loads((DATA / "tables/funnel.json").read_text(encoding="utf-8"))
    monthly = list(csv.DictReader((DATA / "tables/monthly.csv").open(encoding="utf-8")))
    ip = funnel["issues_prs_comments"]
    problems: list[str] = []

    def expect(label: str, got, want) -> None:
        if got != want:
            problems.append(f"{label}: data gives {got}, table says {want}")

    # -- headline -------------------------------------------------------------------
    cats = collections.Counter(r["category"] for r in rows)
    items = ip["items_scanned"]
    print("== Issues, pull requests and comments")
    print(f"findings                {len(rows):,}  ({1e6 * len(rows) / items:.1f} per million of {items:,} items)")
    for c in CATEGORIES:
        print(f"  {c:26s}{cats[c]:>7,}")
        expect(f"category {c}", cats[c], ip["by_category"][c])
    expect("findings", len(rows), ip["findings"])
    for ds, key in (("crawl", "crawl"), ("gh_archive", "gh_archive")):
        n = sum(r["dataset"] == ds for r in rows)
        d = ip["by_dataset"][key]["items"]
        print(f"  dataset {ds:18s}{n:>7,}  ({1e6 * n / d:.1f} per million)")
        expect(f"dataset {ds}", n, ip["by_dataset"][key]["findings"])

    # -- monthly ----------------------------------------------------------------------
    print("\n== Monthly (crawl rows: issues and PRs; gh_archive rows: see data/README.md)")
    per_month = collections.defaultdict(collections.Counter)
    for r in rows:
        per_month[(r["dataset"], r["created_at"][:7])][r["category"]] += 1
    crawl_rates = []
    for m in monthly:
        c = per_month[(m["dataset"], m["month"])]
        total = sum(c[x] for x in CATEGORIES)
        rate = 1e6 * total / int(m["items_scanned"])
        if m["dataset"] == "crawl":
            crawl_rates.append((m["month"], rate, total))
            print(f"  {m['month']} {m['dataset']:10s} items {int(m['items_scanned']):>11,}  findings {total:>4}  "
                  f"{rate:5.1f} per million  " + " ".join(f"{x.split('_')[0]}={c[x]}" for x in CATEGORIES))
        expect(f"monthly {m['dataset']} {m['month']} findings", total, int(m["findings"]))
        for x in CATEGORIES:
            expect(f"monthly {m['dataset']} {m['month']} {x}", c[x], int(m[x]))
    (m0, r0, n0), (m1, r1, n1) = crawl_rates[0], crawl_rates[-1]
    print(f"  {m0} -> {m1}: rate x{r1 / r0:.1f}, count x{n1 / n0:.1f}")

    # -- attacks ----------------------------------------------------------------------
    att = [r for r in rows if r["category"] == "attack"]
    readme_attack_repos = {repo for r in readmes if r["category"] == "attack" for repo in r["repos"]}
    print("\n== Attacks")
    print(f"issues, PRs and comments {len(att)} (1 in {items / len(att) / 1e6:.1f} million items); "
          f"README repositories {len(readme_attack_repos)}; total {len(att) + len(readme_attack_repos)}")
    targets = collections.Counter(r["attack"]["target_agent"] for r in att)
    bots = sum(v for k, v in targets.items() if k != "other")
    print(f"aim at a triage, review or CI agent   {share(bots, len(att))}   {dict(targets)}")
    print(f"hit a well-known project              {share(sum(r['attack']['well_known_project'] for r in att), len(att))}")
    print(f"name the target bot's own tools       {share(sum(r['attack']['names_bot_internals'] for r in att), len(att))}")

    # -- first-party orchestration -------------------------------------------------------
    orch = [r for r in rows if r["category"] == "first_party_orchestration"]
    o = [r["orchestration"] for r in orch]
    print("\n== First-party orchestration")
    print(f"findings {len(orch):,} in {len({r['repo'] for r in orch})} projects")
    readers = collections.Counter((x["reader"], x["reader_basis"]) for x in o)
    for (reader, basis), n in sorted(readers.items()):
        print(f"  reader {reader:14s} {basis:15s}{n:>6,}")
    for pattern, n in collections.Counter(x["pattern"] for x in o).most_common():
        print(f"  pattern {pattern:40s}{n:>6,}")
    for assoc, n in collections.Counter(r["author_association"] for r in orch).most_common():
        print(f"  author {assoc:20s}{share(n, len(orch))}")
    print(f"  in comments                {share(sum(r['surface'] in COMMENT_SURFACES for r in orch), len(orch))}")
    months = [m for m, _, _ in crawl_rates]
    print("  projects using markers (crawl): " + ", ".join(
        f"{m} {len({r['repo'] for r in orch if r['dataset'] == 'crawl' and r['created_at'][:7] == m})}" for m in months))
    ab = [r for r in orch if r["orchestration"]["approves_or_bypasses"]]
    print(f"  markers that approve or bypass a check: {len(ab)} in {len({r['repo'] for r in ab})} projects")

    # -- READMEs ----------------------------------------------------------------------
    by_cat = collections.defaultdict(set)
    for r in readmes:
        by_cat[r["category"]].update(r["repos"])
    union = set().union(*by_cat.values())
    print("\n== READMEs (counted by repository)")
    for c in ("content_override", "defensive", "attack"):
        print(f"  {c:20s}{len(by_cat[c]):>4}")
        expect(f"README {c}", len(by_cat[c]), funnel["readmes"]["repositories_by_category"][c])
    print(f"  repositories with findings {len(union)}")
    expect("README repositories", len(union), funnel["readmes"]["repositories_with_findings"])

    if check:
        print("\n== Check: " + ("all tables agree with the data" if not problems else f"{len(problems)} mismatch(es)"))
        for p in problems:
            print("  " + p)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
