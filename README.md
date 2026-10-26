# Prompt injection on GitHub, in the wild

AI coding agents now work on GitHub every day: they sort new issues, review pull requests and pick up tasks on their own. To do that they read text that anyone can write. Some of that text is **hidden**: invisible on GitHub's rendered page, but delivered in full to any agent that reads the raw Markdown.

This repository holds the results of a census of that hidden, AI-directed text across **145 million GitHub items**, and **`ipi-scan`**, the tool that finds it. Use the tool to check your own repositories, or to extend the measurement to new data.

Research by Gabi Nakibly and Zvika Babo, Radware.

* [What we found](#what-we-found)
* [What is in this repository](#what-is-in-this-repository)
* [The data](#the-data)
* [The tool: `ipi-scan`](#the-tool-ipi-scan)
* [Responsible use](#responsible-use)

## What we found

We scanned every new issue and pull request on GitHub from June 1 to September 30, 2026 (95.1 million items), issues, pull requests and comments from GH Archive for September 2025 to May 2026 plus comments for June to August 2026 (47.6 million items), and the READMEs of the 2 million most-starred repositories.

| Result | Value |
|---|---|
| Real AI-directed findings in issues, PRs and comments | **2,281**, about **16 per million items** |
| Attacks on AI agents | **47**: 43 in issues, PRs and comments (about **1 in 3.3 million** items) and 4 in READMEs |
| Projects leaving hidden instructions for their own agents | **1,519** findings in **178** projects |
| Growth, June to September 2026 (per million new issues and PRs) | **7.0 → 15.4**, 2.2 times |
| Projects coordinating their own agents through hidden markers | **11 → 69**, June to September 2026 |
| README files with real AI-directed hidden text | **106** of 2 million repositories |
| Share of classifier-confirmed items that were really issue/PR template text | **60%** |

The 2,281 findings fall into four categories:

| Category | Findings | Meaning |
|---|---:|---|
| First-party orchestration | 1,519 | A project leaving instructions or markers for its **own** AI agents or agent tooling |
| Content override | 374 | Steering what an agent writes, for the author's benefit (review language, attribution, promotion) |
| Defensive | 345 | Keeping agents away from parts of the work, making them identify themselves, or trapping them |
| Attack | 43 | Trying to make **someone else's** agent act against its owner: leak secrets, forge a verdict, run commands |

Three observations matter most for defenders:

1. **Attacks are rare but targeted.** More than half of them aim at a project's own triage, review or CI agent, and several name that agent's real tools or output fields, which means the attacker read the project's workflow first.
2. **Most hidden text is friendly, and it is growing fast.** Projects use hidden comments as a notepad their agents share: claims, state, hand-offs and instructions. 75% of these were written by the project's owners, members or collaborators.
3. **Friendly markers are forgeable.** They are plain text in a public thread. A bot that obeys a marker without checking who wrote it can be fooled by anyone who types the same marker.

Every number is a **lower bound**: we count only hidden text, and GH Archive now captures only a small share of GitHub's activity. See [docs/methodology.md](docs/methodology.md) for how every number was produced.

## What is in this repository

```
data/
  issues_prs/findings.jsonl   the 2,281 findings in issues, PRs and comments
  readmes/findings.jsonl      the README findings (106 repositories)
  tables/funnel.json          every step from items scanned to findings
  tables/monthly.csv          findings and rates per month and dataset
  README.md                   field-by-field description of the data
scripts/summarize.py          recomputes every number above from the data files
docs/
  methodology.md              datasets, detection, classification, review and counting rules
ipiscan/                      the scanning tool (Python package)
tests/                        tests for the tool
```

## The data

`data/issues_prs/findings.jsonl` has one JSON object per finding: the item's URL, repository, surface (issue, pull request, comment, review), creation time, category, the hidden text the author wrote, the stage-2 classifier's objective and target, and a short note on how the finding was judged. Findings in the first-party orchestration category also record **who reads the marker** (an AI model, or agent tooling) and the basis for that decision. GitHub usernames are not included.

`data/readmes/findings.jsonl` has one object per distinct hidden snippet with the repositories that carry it. The tables summarise both.

A quick look with Python:

```python
import json, collections
rows = [json.loads(line) for line in open("data/issues_prs/findings.jsonl", encoding="utf-8")]
print(collections.Counter(r["category"] for r in rows))
attacks = [r for r in rows if r["category"] == "attack"]
print(attacks[0]["url"], attacks[0]["hidden_text"][:200])
```

The full field list and the category definitions are in [data/README.md](data/README.md). To recompute every number in this README from the data files, and verify the tables against them:

```bash
python scripts/summarize.py --check
```

## The tool: `ipi-scan`

`ipi-scan` runs the same pipeline that produced the data:

1. **Detect.** Find text a person cannot see on GitHub but an agent receives. For issues, PRs and comments the default is HTML comments (`<!-- ... -->`), which carry almost all hidden text on those surfaces. For READMEs it also finds invisible Unicode (tag characters, zero-width runs, bidirectional overrides, variation selectors), white LaTeX and CSS-hidden elements. Text shown inside code blocks is visible, so it is ignored. Well-known machine markers left by GitHub apps are dropped.
2. **Stage 1.** A fast model screens each item's hidden text: is any of it aimed at an AI agent?
3. **Stage 2.** A stronger model re-examines every positive with more context and keeps it as an attack, defensive text or content override, or discards it as a false positive.
4. **Template check.** Hidden text copied from the repository's own issue or PR template (current and past versions, plus the owner's `.github` repository) is marked as template text, because the item's author did not write it.
5. **Report.** One line per finding, plus a readable summary.

The models only ever see the hidden text as data: it is wrapped in delimiters, forged delimiters are removed, and the prompts tell the model never to follow it. The prompts are in [ipiscan/prompts.py](ipiscan/prompts.py).

### Install

Python 3.11 or later.

```bash
git clone https://github.com/rdwr-gabin/github-ipi-in-the-wild
cd github-ipi-in-the-wild
pip install -e ".[openai]"        # or ".[anthropic]", or ".[all]" for both
```

Set the keys the tool reads from the environment:

| Variable | Needed for |
|---|---|
| `GITHUB_TOKEN` | Reading GitHub. Any token works, including `gh auth token`. Without one GitHub allows only 60 requests an hour. |
| `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` | The two classification stages. Not needed with `--detect-only`. |

With OpenAI the stages default to `gpt-5.6-luna` and `gpt-5.6-terra`, the models behind the published results. With Anthropic they default to `claude-haiku-5-5` and `claude-sonnet-5-5`. Choose explicitly with `--provider`, `--stage1-model` and `--stage2-model`.

### Quick start

Scan one repository (issues, PRs, comments and README):

```bash
ipi-scan repo OWNER/NAME
ipi-scan repo OWNER/NAME --since 2026-09-01     # only items created since a date
ipi-scan repo OWNER/NAME --detect-only          # find hidden text, no model calls
```

Check a local Markdown file, with no network and no keys:

```bash
ipi-scan text README.md
```

### Sources

| Command | Scans |
|---|---|
| `ipi-scan repo OWNER/NAME` | One repository: issues, pull requests, issue and PR comments, review comments, README. `--no-comments`, `--no-readme` narrow it. |
| `ipi-scan readme OWNER/NAME ...` | READMEs of the listed repositories, or of every repository in `--file repos.txt`. |
| `ipi-scan search --from 2026-11-01 --to 2026-11-02` | Every public issue and PR created in the window, through the GitHub Search API: the same coverage as our June to September 2026 crawl. Windows are split automatically to stay under the API's 1,000-result cap. |
| `ipi-scan archive --from 2026-11-01 --to 2026-11-02` | Newly opened issues and PRs, comments and reviews from GH Archive hourly files. `--hours 0,6,12,18` samples hours. |
| `ipi-scan text FILE` | A local file, detection only. |

Common options: `--out DIR` (results folder, default `results/<source>-<name>`), `--detect-only`, `--workers N` (parallel model calls), `--all-techniques` (also look for collapsed `<details>` blocks, invisible Unicode, white LaTeX and CSS hiding in issue and PR text), `--no-templates`, `--template-since DATE`.

Every step writes to disk as it goes. If a run stops, run the same command again: the source is read again, but items already collected are not duplicated and model calls already made are not repeated.

### Output

| File | Contents |
|---|---|
| `hidden_text.jsonl` | Every item that has hidden text, with each hidden region |
| `scanned.json` | How many items were scanned, by surface (the denominator for rates) |
| `stage1.jsonl`, `stage2.jsonl` | The two classifiers' raw verdicts |
| `findings.jsonl` | One line per item kept by stage 2, with its category, hidden text and template matches |
| `summary.md` | Counts per step and per category, and a table of findings to read |

Categories in `findings.jsonl` are `attack`, `defensive`, `content_override`, `uncertain` and `template_text` (every hidden region came from the repository's template).

### Reading the results

The classifiers narrow millions of items down to a list a person can read; they do not replace that person. In our data the final word on every finding came from manual review, and these were the decisive questions:

* **Who wrote it, and whose agent does it target?** An attack tries to make another party's agent act against that party. The same words written by a project for its own agents are not an attack.
* **Is it the project talking to its own agents?** Findings written by the owner, a member, a collaborator or the project's bot carry a `review_hint`. Look for the code that reads the marker: an agent prompt that tells the AI to obey it, or a script in the project's agent tooling that parses it. If you find one, it is first-party orchestration. Markers read only by CI checks or conventional bots, with no AI involved, are not AI-directed at all.
* **Is it a test?** Owners test their own agents, and security researchers probe others' agents with canaries and request-capture URLs. Read the repository and the payload before drawing conclusions.

### Cost and speed

Detection is fast and local: the full 143 million items took about a day on a laptop-class machine. In our scan the two model stages cost pennies per thousand items, using batch pricing, because only items with hidden text reach them (about 3% of items) and only stage-1 positives reach stage 2. `ipi-scan` makes ordinary API calls, which providers price higher than batch jobs. The slowest part of a large scan is reading GitHub within its rate limits.

### Limitations

* Only hidden text is detected. An injection written in plain sight is out of scope.
* The classifiers see the title and the hidden regions, not the rest of the visible body.
* GitHub's Copilot cloud agent removes HTML comments before its model sees them, so this kind of hidden text cannot reach that particular agent.
* GH Archive captures a small share of GitHub's issue activity in 2026 and lacks pull-request bodies after October 2025. Use `ipi-scan search` when coverage matters.

### Tests

```bash
pip install -e ".[dev]"
pytest
```

## Responsible use

Everything in `data/` was public on GitHub when collected. The hidden text is published as found, so some of it contains live attack payloads: shell commands, collection URLs and instructions written to manipulate agents. Treat it as untrusted data. Do not feed it to an agent that can act, and do not run any command in it.

Use `ipi-scan` on repositories you maintain, and for research. If it finds an attack in someone else's project, tell the maintainers privately.

## License

MIT. See [LICENSE](LICENSE).
