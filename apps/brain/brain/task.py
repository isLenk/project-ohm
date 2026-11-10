"""The one shape every job takes: a prompt in, text or JSON out."""

from dataclasses import dataclass


@dataclass
class Task:
    role: str                    # classify | extract | brief | curate | judge | reflect | label | screen
    system: str
    user: str
    schema: dict | None = None   # JSON Schema; the answer is parsed and checked against it (top-level keys and types)
    max_tokens: int = 200
    temperature: float = 0.1
    local_only: bool = False     # set it for anything with a screenshot or private text; cloud backends are skipped
    reasoning: str | None = None # "low" | "medium" | "high" for backends that have it (codex); None = backend default
    items: int = 0               # how many items pack() numbered into it, for the log
    images: list[str] = ()       # pictures to show with `user`: file paths or data URLs (codex, local, openrouter)
    private: bool = True         # mask names for cloud backends (privacy.py)
    people: list = ()            # extra people for this task, on top of privacy.people()
