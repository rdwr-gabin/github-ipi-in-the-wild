"""The unit the pipeline works on: one piece of GitHub text written by one account."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Where the text lives on GitHub.
SURFACES = ("issue", "pull_request", "issue_comment", "pr_comment", "review", "review_comment", "readme")

# GitHub's author_association values that mean "part of the project".
PROJECT_ROLES = {"OWNER", "MEMBER", "COLLABORATOR"}


@dataclass
class Item:
    url: str
    repo: str
    surface: str
    created_at: str = ""
    title: str = ""
    body: str = ""
    author: str = ""
    author_association: str = ""   # OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE, ...
    author_is_bot: bool = False
    regions: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        """What an agent reading the item receives: title and raw body."""
        return f"{self.title}\n\n{self.body}".strip() if self.title else self.body

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
