"""Find text that a person reading GitHub's rendered page cannot see, but an AI agent
reading the raw Markdown receives.

Techniques:

  html_comment        text inside <!-- ... -->. GitHub never renders comments.
  details             the body of a collapsed <details> block (off by default:
                      expanding it is one click away, so it is not truly hidden).
  latex_white         LaTeX / MathJax text coloured white, e.g. $\\color{white}{...}$.
  css_hidden          an element hidden with CSS (display:none, font-size:0, ...).
                      github.com strips inline styles, but npm, PyPI, documentation
                      sites and IDE previews honour them.
  unicode_tag         characters from the Unicode tag block (U+E0000-E007F). They have
                      no glyph and can spell out a whole ASCII message.
  zero_width          zero-width characters inside a word or in runs of three or more.
  bidi                bidirectional-override characters.
  variation_selector  clusters of supplementary variation selectors (U+E0100-E01EF).

Anything shown inside a fenced code block, an inline code span or an indented code
block is rendered visibly by GitHub, so it is never reported.

The detector is deterministic: no model, no network.
"""
from __future__ import annotations

import re

ISSUE_TECHNIQUES = ("html_comment",)
README_TECHNIQUES = ("html_comment", "latex_white", "css_hidden", "unicode")
ALL_TECHNIQUES = ("html_comment", "details", "latex_white", "css_hidden", "unicode")

MAX_REGION_CHARS = 4000

_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_FENCE_RE = re.compile(r"^[ \t]*(?:```+|~~~+)", re.MULTILINE)
_DETAILS_RE = re.compile(r"<details\b[^>]*>(.*?)</details>", re.DOTALL | re.IGNORECASE)
_SUMMARY_RE = re.compile(r"<summary\b[^>]*>(.*?)</summary>", re.DOTALL | re.IGNORECASE)
_TAGS_RE = re.compile(r"<[^>]+>")

_LATEX_WHITE_RE = re.compile(
    r"\\(?:text)?color\s*\{\s*(?:white|#fff(?:fff)?|rgb\(\s*255\s*,\s*255\s*,\s*255\s*\))\s*\}", re.I)
_CSS_HIDE_RE = re.compile(
    r"(display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0(?:px|pt|em|rem)?\b"
    r"|opacity\s*:\s*0(?:\.0+)?\b"
    r"|text-indent\s*:\s*-\s*\d"
    r"|color\s*:\s*#?f{3}(?:f{3})?\b"
    r"|color\s*:\s*white"
    r"|left\s*:\s*-\s*\d{3,}px"
    r"|clip\s*:\s*rect\(\s*0)", re.I)
# `hidden` only as a real HTML boolean attribute, never the English word in prose.
_CSS_ATTR_RE = re.compile(r"(aria-hidden\s*=\s*[\"']true[\"']|<[a-zA-Z][^<>]{0,300}?\shidden\s*(?=[/>=]))", re.I)

_ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x180E}
_BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
# Only the supplementary block: U+FE00-FE0F is ordinary emoji presentation.
_VARIATION_SELECTORS = set(range(0xE0100, 0xE01F0))


def in_code_context(text: str, pos: int) -> bool:
    """True when position `pos` sits inside a fenced block, an inline code span or an
    indented code block, where GitHub shows the raw text visibly."""
    before = text[:pos]
    if len(_FENCE_RE.findall(before)) % 2 == 1:
        return True
    line = before[before.rfind("\n") + 1:]
    if line.count("`") % 2 == 1:
        return True
    return line[:4] == "    " or line.startswith("\t")


def has_text(region: str) -> bool:
    """True when the region carries at least a few characters of real text once HTML tags
    are removed, so empty comments and icon-only elements are skipped."""
    return len(_TAGS_RE.sub(" ", region).strip()) >= 4


def _region(technique: str, text: str, offset: int, **extra) -> dict:
    return {"technique": technique, "hidden_text": text.strip()[:MAX_REGION_CHARS], "offset": offset, **extra}


def html_comments(text: str) -> list[dict]:
    out = []
    for m in _COMMENT_RE.finditer(text):
        if not in_code_context(text, m.start()) and has_text(m.group(1)):
            out.append(_region("html_comment", m.group(1), m.start()))
    return out


def details_blocks(text: str) -> list[dict]:
    out = []
    for m in _DETAILS_RE.finditer(text):
        if in_code_context(text, m.start()):
            continue
        inner = m.group(1)
        summary = _SUMMARY_RE.search(inner)
        if summary:  # the <summary> label is always visible
            inner = inner[:summary.start()] + inner[summary.end():]
        if has_text(inner):
            out.append(_region("details", inner, m.start()))
    return out


def latex_white(text: str) -> list[dict]:
    out = []
    for m in _LATEX_WHITE_RE.finditer(text):
        if in_code_context(text, m.start()):
            continue
        rest = text[m.end(): m.end() + 600]
        for close in ("$$", "$", "\\)", "\\]"):  # stop at the end of the math block
            i = rest.find(close)
            if i != -1:
                rest = rest[:i]
                break
        if has_text(rest):
            out.append(_region("latex_white", rest, m.start(), signal=m.group(0)))
    return out


def css_hidden(text: str) -> list[dict]:
    """The inner text of each element hidden by CSS: from the opening tag's '>' to the
    next closing tag."""
    out = []
    for rx in (_CSS_HIDE_RE, _CSS_ATTR_RE):
        for m in rx.finditer(text):
            if in_code_context(text, m.start()):
                continue
            gt = text.find(">", m.end())
            if gt == -1:
                continue
            end = text.find("</", gt + 1)
            if end == -1 or end - gt > 3000:
                end = gt + 800
            inner = text[gt + 1:end]
            if has_text(inner):
                out.append(_region("css_hidden", inner, m.start(), signal=m.group(1).strip()))
    return out


def _context(text: str, i: int, pad: int = 60) -> str:
    return text[max(0, i - pad): i + pad].replace("\n", " ")


def invisible_unicode(text: str) -> list[dict]:
    out = []
    # Tag characters, minus valid subdivision-flag emoji (U+1F3F4 + tag letters + U+E007F).
    tags = [i for i, ch in enumerate(text) if 0xE0000 <= ord(ch) <= 0xE007F]
    if tags:
        flag_parts: set[int] = set()
        i = 0
        while i < len(text):
            if ord(text[i]) == 0x1F3F4:
                j = i + 1
                run = []
                while j < len(text) and 0xE0000 <= ord(text[j]) <= 0xE007F:
                    run.append(j)
                    j += 1
                if run and ord(text[run[-1]]) == 0xE007F and len(run) <= 8:
                    flag_parts.update(run)
                i = j
            else:
                i += 1
        real = [i for i in tags if i not in flag_parts]
        if real:
            decoded = "".join(chr(ord(text[i]) - 0xE0000) for i in real if 0xE0020 <= ord(text[i]) <= 0xE007E)
            out.append(_region("unicode_tag", decoded, real[0], count=len(real), context=_context(text, real[0])))
    # Zero-width characters splitting a word, or in runs of three or more.
    zw = [i for i, ch in enumerate(text) if ord(ch) in _ZERO_WIDTH]
    if zw:
        in_word = [i for i in zw if 0 < i < len(text) - 1 and text[i - 1].isalnum() and text[i + 1].isalnum()]
        longest = run = 1
        for a, b in zip(zw, zw[1:]):
            run = run + 1 if b - a == 1 else 1
            longest = max(longest, run)
        if in_word or longest >= 3:
            first = in_word[0] if in_word else zw[0]
            out.append(_region("zero_width", _context(text, first), first, count=len(zw), max_run=longest))
    bidi = [i for i, ch in enumerate(text) if ord(ch) in _BIDI]
    if bidi:
        out.append(_region("bidi", _context(text, bidi[0]), bidi[0], count=len(bidi)))
    vs = [i for i, ch in enumerate(text) if ord(ch) in _VARIATION_SELECTORS]
    if len(vs) >= 4:
        out.append(_region("variation_selector", _context(text, vs[0]), vs[0], count=len(vs)))
    return out


_DETECTORS = {
    "html_comment": html_comments,
    "details": details_blocks,
    "latex_white": latex_white,
    "css_hidden": css_hidden,
    "unicode": invisible_unicode,
}


def scan(text: str, techniques=ISSUE_TECHNIQUES) -> list[dict]:
    """Return every hidden region in `text` found by the requested techniques, in order
    of position. Each region: {technique, hidden_text, offset, ...}."""
    if not text:
        return []
    unknown = set(techniques) - set(_DETECTORS)
    if unknown:
        raise ValueError(f"unknown technique(s): {', '.join(sorted(unknown))}")
    regions = [r for t in techniques for r in _DETECTORS[t](text)]
    return sorted(regions, key=lambda r: r["offset"])
