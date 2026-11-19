"""Serve Agent Skills through MCP.

A skill is a folder with a SKILL.md file (https://agentskills.io). Some AI apps can install
skills directly, but any MCP app can use these tools, so one server carries both the access
(tools) and the know-how (skills).

Loading happens in stages, so unused skills cost almost nothing:
    list_skills      name and description of every skill
    load_skill       the full instructions of one skill
    read_skill_file  a reference file, only if a step needs it
"""
from __future__ import annotations

from pathlib import Path

import yaml


def discover(skills_dir: Path) -> dict[str, dict]:
    """Find skills. Folders starting with _ are templates and are skipped."""
    skills = {}
    for folder in sorted(p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith("_")):
        skill_file = folder / "SKILL.md"
        if not skill_file.exists():
            continue
        text = skill_file.read_text(encoding="utf-8")
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        skills[meta["name"]] = {"description": meta["description"], "body": body.strip(), "folder": folder}
    return skills


def read_file(skills: dict[str, dict], name: str, relative_path: str) -> str:
    """Read one file inside a skill's folder, refusing paths that lead outside it."""
    if name not in skills:
        raise ValueError(f"Unknown skill '{name}'. Available: {', '.join(skills)}")
    folder = skills[name]["folder"].resolve()
    target = (folder / relative_path).resolve()
    if not target.is_relative_to(folder):
        raise ValueError("That path is outside the skill's folder.")
    if not target.is_file():
        raise ValueError(f"'{relative_path}' does not exist in skill '{name}'.")
    return target.read_text(encoding="utf-8")
