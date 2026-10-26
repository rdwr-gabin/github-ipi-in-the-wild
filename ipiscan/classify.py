"""The two classification stages.

Stage 1 sees the item's title and its hidden regions, plus the surface and a coarse
authorship class. Stage 2 sees every stage-1 positive again with repository context,
the stage-1 verdict, and the title as visible context.

Results are appended to JSON-lines files as they arrive, keyed by item URL, so an
interrupted run resumes where it stopped.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
from pathlib import Path
from typing import Callable

from . import prompts
from .filters import authorship
from .items import Item
from .llm import LLM, MAX_OUTPUT_TOKENS

REGION_SEP = "\n\n---\n\n"


def hidden_text(item: Item) -> str:
    return REGION_SEP.join(r.get("hidden_text") or "" for r in item.regions)


def stage1_text(item: Item) -> str:
    hidden = hidden_text(item)
    title = item.title.strip()
    if title and title.lower() not in hidden.lower():
        return f"Item title: {title}\n\n{hidden}"
    return hidden


def visible_context(item: Item) -> str:
    parts = [f"Title: {item.title}"] if item.title else []
    techniques = sorted({r["technique"] for r in item.regions})
    parts.append(f"Surface: {item.surface} | Techniques: {', '.join(techniques)} | Hidden regions: {len(item.regions)}")
    return "\n\n".join(parts)


def _load(path: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if "api_error" not in row:  # failed calls are retried on the next run
                    done[row["url"]] = row
    return done


def _run(items: list[Item], path: Path, fn: Callable[[Item], dict], workers: int, label: str) -> dict[str, dict]:
    done = _load(path)
    todo = [it for it in items if it.url not in done]
    if todo:
        print(f"{label}: {len(todo)} to classify ({len(done)} already done)", flush=True)
    with path.open("a", encoding="utf-8") as out, cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, it): it for it in todo}
        for n, fut in enumerate(cf.as_completed(futures), 1):
            row = fut.result()
            done[row["url"]] = row
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if n % 50 == 0 or n == len(todo):
                print(f"  {label}: {n}/{len(todo)}", flush=True)
    return done


def run_stage1(llm: LLM, items: list[Item], path: Path, workers: int = 8) -> dict[str, dict]:
    def one(item: Item) -> dict:
        text = stage1_text(item)
        user = prompts.stage1_user(text, surface=item.surface, authorship=authorship(item, text))
        try:
            parsed = prompts.parse_json(llm.complete(1, prompts.STAGE1_SYSTEM, user), prompts.STAGE1_KEYS)
        except Exception as e:  # keep going; the item is retried on the next run
            parsed = {"api_error": f"{type(e).__name__}: {e}"[:300]}
        return {"url": item.url, "model": llm.models[1], **parsed}
    return _run(items, path, one, workers, "stage 1")


def run_stage2(llm: LLM, items: list[Item], stage1: dict[str, dict], path: Path, workers: int = 8) -> dict[str, dict]:
    def one(item: Item) -> dict:
        s1 = {k: stage1[item.url].get(k) for k in prompts.STAGE1_KEYS}
        user = prompts.stage2_user(
            repo=item.repo, item_url=item.url, surface=item.surface,
            authorship=authorship(item, hidden_text(item)), author=item.author, author_is_bot=item.author_is_bot,
            title=item.title, visible_context=visible_context(item), hidden_text=hidden_text(item), stage1=s1,
        )
        try:
            parsed = prompts.parse_json(llm.complete(2, prompts.STAGE2_SYSTEM, user), prompts.STAGE2_KEYS)
        except Exception as e:
            parsed = {"api_error": f"{type(e).__name__}: {e}"[:300]}
        if "api_error" not in parsed:
            parsed["keep_finding"] = prompts.stage2_keep(parsed)
        return {"url": item.url, "model": llm.models[2], **parsed}
    return _run(items, path, one, workers, "stage 2")



def run_readme(llm: LLM, items: list[Item], path: Path, workers: int = 8) -> dict[str, dict]:
    """READMEs: every hidden snippet of at least README_MIN_CHARS characters is judged on
    its own, in numbered groups, with the README prompt and the stage-1 model."""
    def one(item: Item) -> dict:
        snippets = [r for r in item.regions if len((r.get("hidden_text") or "").strip()) >= prompts.README_MIN_CHARS]
        verdicts = []
        try:
            for i in range(0, len(snippets), prompts.README_GROUP):
                group = snippets[i:i + prompts.README_GROUP]
                user = prompts.readme_user([r["hidden_text"] for r in group])
                parsed = None
                for _ in range(2):  # one retry when the reply is not a complete array
                    reply = llm.complete(1, prompts.README_SYSTEM, user, max_tokens=MAX_OUTPUT_TOKENS["readme"])
                    parsed = prompts.parse_readme(reply, len(group))
                    if parsed:
                        break
                if parsed is None:
                    return {"url": item.url, "api_error": "no complete JSON array in reply"}
                verdicts += [{**v, "technique": r["technique"], "hidden_text": r["hidden_text"]} for v, r in zip(parsed, group)]
        except Exception as e:
            return {"url": item.url, "api_error": f"{type(e).__name__}: {e}"[:300]}
        return {"url": item.url, "model": llm.models[1], "is_injection": any(v.get("is_injection") for v in verdicts),
                "snippets": verdicts}
    return _run(items, path, one, workers, "readme")
