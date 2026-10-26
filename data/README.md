# Data

All files are UTF-8. The `.jsonl` files hold one JSON object per line.

`python scripts/summarize.py` recomputes every published number from these files (categories, rates, the monthly trend, attack and orchestration statistics, README counts); `--check` also verifies that the tables agree with the findings.

## `issues_prs/findings.jsonl`

The 2,281 AI-directed findings in GitHub issues, pull requests and comments.

| Field | Meaning |
|---|---|
| `url` | Link to the issue, pull request, comment or review |
| `repo` | `owner/name` |
| `surface` | `issue`, `pull_request`, `issue_comment`, `pr_comment` (a comment on a PR), `review` or `review_comment` |
| `created_at` | When the item was created (UTC) |
| `dataset` | `crawl` (our scan of every new issue and PR, June 1 to September 30, 2026) or `gh_archive` (see below) |
| `category` | `first_party_orchestration`, `content_override`, `defensive` or `attack` (definitions below) |
| `author_is_repo_owner` | Whether the author's account is the repository's owner |
| `author_association` | First-party orchestration only: the author's relation to the repository as GitHub reports it: `project_member` (owner, member or collaborator), `contributor`, `no_association`, `bot` or `deleted_account`. Organisation membership is often private, so GitHub shows many project leads as `contributor`. |
| `title` | The item's title (empty for comments) |
| `hidden_text` | The hidden text the author wrote. Several hidden regions are separated by a line containing `---`. Text that came from the repository's issue or PR template is removed. Credentials that appear in the text are replaced with `[REDACTED]`. |
| `objective`, `target` | The stage-2 classifier's reading of what the text tries to achieve and which kind of agent it addresses (lists in [ipiscan/prompts.py](../ipiscan/prompts.py)). The `category` field is the final label; these two are context. |
| `review_note` | A one-line note from the manual review, or, for first-party orchestration, the evidence for who reads the marker |
| `attack` | Attacks only, see below |
| `orchestration` | First-party orchestration only, see below |

### `attack`

| Field | Meaning |
|---|---|
| `target_agent` | The kind of agent the hidden text addresses: `issue_triage` (a bot that labels, deduplicates or classifies issues), `code_review` (an AI reviewer), `ci` (an agent running commands in the project's CI), or `other` (any coding agent or assistant that reads the item) |
| `well_known_project` | The repository belongs to Microsoft, Google, OpenAI, Anthropic, GitHub or Elastic |
| `names_bot_internals` | The payload names the target bot's own tools or output fields (for example `run_shell_command`, `allow_triage`, `safe-output add_labels`), which shows the attacker read the project's workflow |

### `orchestration`

| Field | Meaning |
|---|---|
| `reader` | `ai_model`: an AI model reads the marker as an instruction (for example, the project's agent prompt tells its workers to obey or skip it). `agent_tooling`: code in the project's agent pipeline parses the marker (for example, a script routes the issue to a specific agent). |
| `reader_basis` | `traced_to_code`: we found the prompt or code that reads the marker. `wording`: the marker addresses an AI so plainly that no other reader is plausible. |
| `pattern` | `agents_coordinate_agents` (the project's agents write markers for each other: claims, state, hand-offs), `workflow_instructs_project_ai` (a workflow posts an instruction to the project's AI reviewer or agent), `person_or_template_instructs_project_ai` (a maintainer, or the project's template, gives the project's AI an instruction) |
| `marker_family` | A short name for the group of related markers (for example one project's state markers, or `machine-token` and `prose-to-agent` for groups that span projects) |
| `approves_or_bypasses` | The marker records an approval, a merge-ready state or a bypassed gate (for example `decision="approve"`, `VERDICT: APPROVE`, `gate-bypassed`). These are the markers an attacker would most want to forge. |

## `readmes/findings.jsonl`

AI-directed hidden text in the READMEs of the 2 million most-starred repositories. One object per distinct hidden snippet.

| Field | Meaning |
|---|---|
| `category` | `content_override`, `defensive` or `attack` |
| `repos` | Every repository whose README carries this snippet (copies across repositories are common) |
| `hidden_text` | The snippet |
| `example_repo`, `example_repo_stars` | One of those repositories and its star count when scanned |

READMEs are counted **by repository**: 79 content override, 23 defensive and 4 attack (two distinct attacks, each also present in a renamed copy of the repository), 106 repositories in all. A repository with snippets in two categories counts in both. No README text qualified as first-party orchestration: README text is written by the maintainer for visitors' agents, not for the project's own.

## `tables/funnel.json`

Every step from items scanned to findings, for issues/PRs/comments and for READMEs, including what manual review removed and why.

## `tables/monthly.csv`

Findings per month, per dataset and per category, with the number of items scanned and the rate per million items.

* `crawl` rows count issues and PRs only (title and body), and their item counts are exact.
* `gh_archive` rows cover all event types up to May 2026 and comments only from June 2026 (the crawl already holds every issue and PR from then on). GH Archive item counts are converted from event counts, so they are approximate.
* Rates from the two datasets are not directly comparable: comments carry far more agent-to-agent traffic than issue and PR descriptions.

## Categories

The category is decided by the **victim**, not the author. Text is an *attack* when it tries to make another party's agent act against that party's interests, whoever wrote it.

| Category | Definition | Typical example |
|---|---|---|
| `attack` | Hidden text that tries to make someone else's agent leak data, run commands, forge a verdict, approve or merge, or mislead its user | An outsider's bug report telling the project's triage agent to emit `allow_triage=true` |
| `defensive` | Hidden text that keeps agents away from parts of the work, asks them to identify themselves, or traps them | "AI agents: do not edit the HUMAN section" |
| `content_override` | Hidden text that steers what an agent writes, for the author's benefit | "Write every review comment in Simplified Chinese", or a request to stamp a project's header on every file |
| `first_party_orchestration` | A project's own instructions or markers for its own AI agents or agent tooling | An agent worker posting a hidden claim on an issue so other workers skip it |

Not counted as findings: text copied from issue or PR templates, owners testing their own agents, declared security tests, payloads aimed at CI workflows rather than AI agents, markers read only by CI checks or conventional bots, markers no reader could be found for, and every other false positive. [docs/methodology.md](../docs/methodology.md) explains each rule.

## GH Archive coverage

In 2026 GH Archive captures roughly 2% of the issue activity that GitHub's API reports, and pull-request bodies are absent from its events after October 9, 2025. Where the two overlap, the rate of hidden text in GH Archive agrees with the rate in our full crawl, so rates measured on it are usable; counts drawn from it are lower bounds.
