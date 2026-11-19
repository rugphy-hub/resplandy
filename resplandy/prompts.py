"""Load role prompts (prompts/<agent>.md) and skills (skills/<name>/SKILL.md plus its reference files)."""
from __future__ import annotations

from .config import ROOT


def role_prompt(agent: str) -> str:
    """The role prompt of one agent."""
    return (ROOT / "prompts" / f"{agent}.md").read_text(encoding="utf-8").strip()


def skill(name: str, *references: str) -> str:
    """A skill's instructions without its front matter, followed by the named reference files."""
    folder = ROOT / "skills" / name
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    parts = [text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()]
    parts += [(folder / "references" / reference).read_text(encoding="utf-8").strip() for reference in references]
    return "\n\n".join(parts)
