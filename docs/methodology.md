# Methodology

This document explains how every number in [the data](../data) was produced: what was scanned, how hidden text was found and classified, how each finding was reviewed, and how findings were counted.

## 1. Datasets

| Dataset | What it contains | Items |
|---|---|---:|
| Crawl | Every public issue and pull request created on GitHub from June 1 to September 30, 2026: title and body, collected through the GitHub API hour by hour | 95,075,061 |
| GH Archive | Issues, pull requests, comments and reviews from GH Archive, September 2025 to May 2026, plus comments only for June to August 2026 (the crawl already holds every issue and PR from June on, but not comments) | about 47.6 million |
| READMEs | The README of each of the 2 million repositories with the most stars | 2,000,000 |

Each item is counted once, by URL. GH Archive counts events rather than items; event counts are converted to items with the ratio measured on the whole archive (48 million items per 73.1 million events), so the archive's item count is approximate.

GH Archive no longer captures all of GitHub. In 2026 it holds roughly 2% of the issue volume the crawl sees, and pull-request bodies are absent from its events after October 9, 2025. Where the two overlap the rate of hidden text agrees, so rates measured on the archive are usable, but its counts are lower bounds.

## 2. Finding hidden text

We do not search for typical injection phrases. A keyword search for "ignore previous instructions" returned 123 hits, of which 116 were false positives: vulnerability reports discussing the phrase, tests for input filters, and ordinary English. Instead we look for **text a person cannot see on GitHub but an agent receives in the raw Markdown**. An honest issue has little reason to contain text that only a machine will read, so hiding is a strong signal by itself.

* **Issues, pull requests and comments:** HTML comments (`<!-- ... -->`). GitHub never renders them; they carry almost all hidden text on these surfaces.
* **READMEs:** HTML comments, invisible Unicode (tag characters, which can spell out an ASCII message with no visible glyphs; zero-width characters inside words or in runs; bidirectional overrides; clusters of supplementary variation selectors), white LaTeX (`$\color{white}{...}$`) and CSS-hidden elements (`display:none`, zero-size fonts and similar; github.com strips inline styles, but package registries, documentation sites and IDE previews honour them).

Text inside a fenced code block, an inline code span or an indented code block is shown verbatim by GitHub, so it is not hidden and is never reported. Regions with fewer than four characters of text are ignored.

A noise filter then drops machine markers that GitHub apps leave in every item they touch (review-bot state markers, preview-deployment notices, dependency-bot debug blocks) and items posted by known bot accounts. These markers are hidden by design and are not instructions to an agent.

The detector is deterministic and runs locally: [ipiscan/detect.py](../ipiscan/detect.py) and [ipiscan/filters.py](../ipiscan/filters.py).

**Result:** 4,902,477 issues, PRs and comments (3.4%) carried hidden text. The READMEs held 127,995 hidden regions, 94% of them HTML comments.

## 3. Classifying hidden text

### Issues, pull requests and comments: two stages

**Stage 1** (OpenAI `gpt-5.6-luna`, temperature 0) screens every item with hidden text. It receives the item's title and hidden regions as delimited data, plus two pieces of trusted context: the surface, and a coarse authorship class (repository owner, known trusted tool, other bot, other account). It answers: is this text an indirect prompt injection aimed at an AI agent, and if so with what objective and against which kind of agent? The prompt is tuned for recall: when unsure, flag.

**Stage 2** (OpenAI `gpt-5.6-terra`) re-examines every stage-1 positive with repository context (for example, whether the repository name marks it as a security lab), the stage-1 verdict, and the title. It keeps a finding as an attack, defensive text or content override, or discards it as a false positive: security-lab fixtures, documentation that quotes injections, normal AI-tool output, template boilerplate, and maintainer notes with no agent-directed intent.

Both prompts treat the hidden text strictly as data: it is wrapped in delimiters, forged delimiters are stripped from it, and the model is told never to follow it. Both prompts are in [ipiscan/prompts.py](../ipiscan/prompts.py). Both stages ran through the OpenAI Batch API.

**Result:** stage 1 flagged 34,448 items; stage 2 kept 13,141.

### READMEs: one stage

Every hidden README snippet of at least 25 characters was judged by Anthropic `claude-haiku-4-5`, eight snippets per request, each independently, with the prompt in [ipiscan/readme_prompt.md](../ipiscan/readme_prompt.md). The prompt is deliberately inclusive: a snippet is positive if it could steer an AI agent that reads it, whatever the author meant, and a separate `intent` field records whether it looks malicious, self-serving, anti-AI or operational.

**Result:** 1,528 distinct snippets, carried by 1,864 repositories, were flagged.

## 4. Template provenance

Repositories ship issue and pull-request templates, and template authors put guidance in HTML comments so it stays invisible. GitHub copies the template into every new issue or PR, so the detector sees that text as if the author had hidden it, and the classifiers, which see only the hidden text, cannot tell the difference.

For every repository with a stage-2 finding we fetched each file GitHub treats as an issue or PR template (`.github/`, the repository root or `docs/`: `PULL_REQUEST_TEMPLATE`, `ISSUE_TEMPLATE`, their folders, and YAML issue forms), in the repository and in the owner's `.github` repository, at every version committed since August 2025. Each hidden region was compared with every HTML comment in those files: an exact match after normalising case, punctuation and spacing; one text containing the other when the shorter is at least half the length of the longer; or word-set (Jaccard) similarity of at least 0.85. A region that matches was not written by the item's author.

**Result:** 7,890 of the 13,141 stage-2 findings (60%) consisted entirely of template text. 5,251 remained.

## 5. Manual review

Every one of the 5,251 remaining items was judged individually. The rules:

* **Attack is defined by the victim.** Text is an attack when it tries to make another party's agent act against that party: leak secrets or data, run commands, forge a triage or review verdict, approve or merge, insert code, or mislead the agent's user. The author's identity does not decide it: a repository owner who plants instructions aimed at visitors' agents is attacking them.
* **Owner self-tests are not findings.** An owner planting a payload in their own repository to test their own agent or pipeline (test repositories, proof-of-concept repositories, canaries in one's own project) is excluded.
* **Declared security tests are not findings.** A probe is excluded when it cannot do harm and shows that it is a test: it points the agent at an address that cannot receive data (`example.com`, a `.invalid` domain, a placeholder domain), asks it to print an explicit test marker (for example `TEST_AI_INJECTION`, `POC-MARKER`, `MSRC-POC-VALIDATION`), is labelled as a security test or a "safe payload" proof, or belongs to a research honeypot set up to observe agents. Two kinds of probe stay attacks: those that send data to request-capture or interaction services (webhook.site, Burp Collaborator, interactsh), which researchers favour but real attackers can use just as well; and those that merely claim to be a test while asking the agent to run commands ("I'm testing our automation, please run..."), because an unverifiable claim of testing costs an attacker nothing.
* **Payloads aimed at CI workflows are not findings.** Shell injection meant for a GitHub Actions workflow that interpolates issue text into a shell command, with no AI agent involved, is a different class of attack and is excluded. This includes a coordinated campaign on September 1, 2026 that posted environment-exfiltration payloads to 26 repositories.
* **Defensive** text keeps agents away from parts of the work (for example the human's sign-off section of a PR template), asks them to identify themselves, or traps them (for example asking agents to add emoji to a PR title).
* **Content override** steers what an agent writes for the author's benefit: review language and tone, attribution headers, promotion, plans written for the AI and hidden from human reviewers.
* Everything else is a **false positive**: human notes, commented-out text, documentation tooling markers, markers copied from bots (for example a dependency dashboard's checkbox markers or a review bot's own state markers), spam aimed at people rather than agents, and text the classifiers misread. A finding also needs the AI-directed text itself to be hidden: an injection in an item's visible title or in a file the item adds is outside this census, even when the item carries unrelated hidden markers.

**Result:** 1,901 false positives and owner self-tests, 11 declared security tests, and 30 payloads aimed at CI workflows, were removed.

## 6. First-party orchestration

Many projects use hidden comments to talk to their **own** agents: an agent worker posts a hidden claim so other workers skip the issue, a project's agent prompt tells its AI to ignore audit-trail blocks, a workflow posts an instruction to the project's AI reviewer. These texts are agent-directed but address no one else's agent, so they are neither attacks nor defensive text. We label them first-party orchestration, and we required evidence for both halves of the name.

**Who writes it.** For every candidate we recorded GitHub's `author_association`. 75% were written by owners, members or collaborators. About 20% were written by accounts GitHub labels `CONTRIBUTOR` that are in fact the projects' leads: organisation membership is often private, and GitHub then reports a member as a contributor. 3% were written by the projects' own bots.

**Who reads it.** We traced every marker to the code that reads it, and kept a finding only when the reader is AI:

* **An AI model reads it as an instruction** (986 findings): the project's agent prompt or instructions file tells the model what to do with the marker (784 traced to the prompt or code), or the marker addresses an AI so plainly that no other reader is plausible (202, judged by wording).
* **Agent tooling parses it** (533 findings): a script in the project's agent pipeline reads the marker, for example a regular expression that routes the issue to a specific coding agent.

We dropped markers whose reader is not AI: 213 read only by CI checks and 313 read only by conventional bots. We also dropped 502 markers for which no reader could be found. Fifty findings were project template text that, in an outside contributor's pull request, addresses that contributor's agent; those were moved to defensive (28) or content override (22).

**Patterns.** The 1,519 findings follow three patterns: agents coordinating agents (1,084), a project workflow instructing the project's AI (81), and a maintainer or the project's template instructing the project's AI (354). Almost half (47%) are in comments, which only GH Archive's sample covers, so the true volume is much larger.

## 7. README review

Each of the 1,528 flagged README snippets was read by hand and given a category, and each was attributed to every repository whose README carries it. 94% of the flagged repositories held no real agent-directed text: almost a thousand carry documentation-generator tags or "auto-generated, do not edit" notices, and most of the rest are notes to human editors or commented-out text. No README text qualified as first-party orchestration: a README is written by the maintainer and read by visitors' agents.

READMEs are counted by repository. A repository with snippets in two categories counts in both; repositories that carry a copy of the same snippet each count.

**Result:** 106 repositories: 79 content override, 23 defensive, 4 attack (two distinct attacks, each also present in a renamed copy of the repository).

## 8. Counting and rates

* An item is counted once, by URL, in the dataset it came from.
* A finding's category is the final review label, not the stage-2 classifier's verdict (which is kept in `objective` and `target` for context).
* Rates are findings per million items scanned. For the crawl, the denominator is the exact number of issues and PRs created in each hour. For GH Archive it is the converted event count.
* Monthly trends use the crawl only, because its coverage is complete and constant. GitHub itself grew 55% over the four months (18.9 to 29.4 million new issues and PRs per month); the rate per million already accounts for that.

## 9. Limitations

* **Only hidden text.** Injections written in plain sight are not counted. Every number is a lower bound.
* **Context.** The classifiers saw the title and the hidden regions, not the rest of the visible body. Manual review corrected the resulting errors, but a classifier miss at stage 1 or 2 is not recovered.
* **GH Archive coverage.** Comments, where agents talk to each other most, come only from GH Archive's sample.
* **Platform behaviour.** GitHub's Copilot cloud agent removes HTML comments before its model sees them, so HTML-comment text cannot reach that agent. Other agents read the raw text.
* **Judgment.** The final categories are human judgments made with written rules. The `review_note` field of every finding records the reasoning, so each one can be checked.
