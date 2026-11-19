"""Planning tools. The Planner drafts and answers; the Reviewer, from another model family, critiques."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Literal

from pydantic import BaseModel, model_validator

from .. import prompts
from ..harness import Run, wrap_data
from ..llm import complete_json
from ..schemas import Claim, Critique, CritiqueItem, Gap, Milestone, Plan, Priority, Task

TaskType = Literal["read", "experiment", "simulation", "analysis", "writing", "other"]
CritiqueType = Literal["missing_step", "dependency_error", "estimate", "literature_warning", "scope", "other"]


@dataclass
class Constraints:
    """What a plan must respect. Code supplies these; the model never sets them."""

    deadline: date | None
    today: date
    max_task_hours: float
    owner: str
    free_hours: float | None = None
    k: float = 1.0


class MilestoneDraft(BaseModel):
    id: str
    name: str
    done_when: str
    due: date


class TaskDraft(BaseModel):
    id: str
    milestone_id: str
    name: str
    type: TaskType
    hours: float
    depends_on: list[str]
    priority: Priority
    claim_ids: list[str]


class PlanDraft(BaseModel):
    milestones: list[MilestoneDraft]
    tasks: list[TaskDraft]


class CritiqueItemDraft(BaseModel):
    type: CritiqueType
    severity: Literal["low", "medium", "high"]
    task_ids: list[str]
    evidence: str
    fix: str


class CritiqueDraft(BaseModel):
    items: list[CritiqueItemDraft]


class Answer(BaseModel):
    item_id: str
    answer: Literal["accept", "reject"]
    reason: str


class CritiqueAnswers(BaseModel):
    answers: list[Answer]
    plan: PlanDraft


def plan_problems(draft: PlanDraft, limits: Constraints, claim_ids: set[str]) -> list[str]:
    """Every rule a draft breaks: ids, dependencies, a cycle, the length of a task, due dates, cited claims."""
    problems = []
    milestones, ids = {m.id for m in draft.milestones}, [t.id for t in draft.tasks]
    if not draft.milestones or not draft.tasks:
        problems.append("a plan needs at least one milestone and one task")
    if len(set(ids)) != len(ids):
        problems.append("task ids must be unique")
    for task in draft.tasks:
        if task.milestone_id not in milestones:
            problems.append(f"task {task.id}: milestone {task.milestone_id!r} is not in the plan")
        if not 0 < task.hours <= limits.max_task_hours:
            problems.append(f"task {task.id}: hours must be above 0 and at most {limits.max_task_hours:g}, "
                            "because a task is one day or less; split it")
        unknown = [dependency for dependency in task.depends_on if dependency not in ids]
        if unknown:
            problems.append(f"task {task.id}: depends on task id(s) that are not in the plan: {unknown}")
        uncited = [claim_id for claim_id in task.claim_ids if claim_id not in claim_ids]
        if uncited:
            problems.append(f"task {task.id}: cites claim id(s) it was not given: {uncited}")
    for milestone in draft.milestones:
        if limits.deadline and milestone.due > limits.deadline:
            problems.append(f"milestone {milestone.id}: due {milestone.due} is after the deadline {limits.deadline}")
    waiting = {task.id: {d for d in task.depends_on if d in ids} for task in draft.tasks}
    while waiting:
        ready = [task_id for task_id, dependencies in waiting.items() if not dependencies]
        if not ready:
            problems.append(f"the dependencies form a cycle among {sorted(waiting)}")
            break
        for task_id in ready:
            del waiting[task_id]
        for dependencies in waiting.values():
            dependencies.difference_update(ready)
    return problems


def _checked(base: type[BaseModel], problems: Callable[[Any], list[str]]) -> type[BaseModel]:
    """The same schema plus our own rules, so a reply that breaks a rule gets the same one repair round as bad JSON."""

    class Checked(base):
        @model_validator(mode="after")
        def _consistent(self) -> "Checked":
            found = problems(self)
            if found:
                raise ValueError("; ".join(found))
            return self

    Checked.__name__ = Checked.__qualname__ = base.__name__
    return Checked


def _short_ids(claims: list[Claim]) -> dict[str, Claim]:
    """C1, C2 and so on for the claims a prompt shows; code maps them back to the stored ids."""
    return {f"C{number}": claim for number, claim in enumerate(claims, start=1)}


def _claim_blocks(short: dict[str, Claim]) -> str:
    return "\n\n".join(wrap_data(short_id, f"{claim.text} (basis: {claim.basis or 'unclear'}; grade: "
                                           f"{claim.grade or 'none'})") for short_id, claim in short.items())


def _plan_text(plan: Plan, short: dict[str, Claim]) -> str:
    """A plan as plain lines for a prompt, with the short claim ids."""
    real_to_short = {claim.id: short_id for short_id, claim in short.items()}
    lines = [f"{m.id}: {m.name}. Done when: {m.done_when}. Due {m.due}." for m in plan.milestones]
    for t in plan.tasks:
        claims = ", ".join(real_to_short.get(claim_id, "?") for claim_id in t.claim_ids) or "none"
        lines.append(f"{t.id} [{t.milestone_id}, {t.priority}, {t.type}, {t.hours:g} h, after: "
                     f"{', '.join(t.depends_on) or 'nothing'}, claims: {claims}] {t.name}")
    return "\n".join(lines)


def _rules(limits: Constraints) -> str:
    free = f" About {limits.free_hours:.0f} free daytime hours exist before the deadline; plan less than that." \
        if limits.free_hours else ""
    return (f"- Today is {limits.today}. The deadline is {limits.deadline or 'not set'}. Every milestone is due "
            f"on or before the deadline.\n- One person does the work.{free}\n"
            f"- A task takes {limits.max_task_hours:g} hours or less. Split anything longer.\n"
            f"- hours is your raw estimate. Do not pad it: code scales it by the person's k (now {limits.k:g}).\n"
            "- Use ids M1, M2 and so on for milestones and T1, T2 and so on for tasks. A task names its "
            "dependencies by task id, has a priority (must, should or could) and a type (read, experiment, "
            "simulation, analysis, writing or other), and lists the ids of the claims it rests on, or an empty list.")


def _to_plan(draft: PlanDraft, plan_id: str, version: int, goal: str, gap_id: str | None, owner: str,
             short: dict[str, Claim]) -> Plan:
    return Plan(
        id=plan_id, version=version, goal=goal, gap_id=gap_id,
        milestones=[Milestone(id=m.id, name=m.name, done_when=m.done_when, due=m.due) for m in draft.milestones],
        tasks=[Task(id=t.id, milestone_id=t.milestone_id, name=t.name, type=t.type, owner=owner, hours=t.hours,
                    depends_on=t.depends_on, priority=t.priority,
                    claim_ids=[short[claim_id].id for claim_id in t.claim_ids]) for t in draft.tasks])


def draft_plan(run: Run, goal: str, claims: list[Claim], gap: Gap, constraints: Constraints) -> Plan:
    """Draft a study plan for the chosen gap: milestones with a done-when line, and tasks of one day or less."""
    short = _short_ids(claims)
    schema = _checked(PlanDraft, lambda draft: plan_problems(draft, constraints, set(short)))
    system = prompts.role_prompt("planner") + "\n\n" + prompts.skill("study-planning")
    task = (f"Goal: {goal}\n\nThe gap the person chose:\n{wrap_data('gap', gap.statement)}\n\n"
            f"Verified claims you may rest tasks on, each in a data tag whose id is the claim id:\n\n"
            f"{_claim_blocks(short)}\n\nConstraints:\n{_rules(constraints)}\n\n"
            "Write the plan: 2 to 4 milestones, each with a done_when line a person can check, and the tasks.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    draft = complete_json("writer", messages, schema, run=run, agent="planner", step="draft_plan")
    return _to_plan(draft, f"{run.run_id}-plan", 1, goal, gap.id, constraints.owner, short)


def critique_plan(run: Run, plan: Plan, claims: list[Claim], k_history: dict, round: int = 1) -> Critique:
    """The Reviewer tries to break the plan: one item per problem, with its type, severity, tasks and a fix."""
    short = _short_ids(claims)
    system = prompts.role_prompt("reviewer") + "\n\n" + prompts.skill("plan-critique", "seeded-error-types.md")
    task = (f"Goal: {plan.goal}\n\nThe plan to critique (round {round}):\n"
            f"{wrap_data('plan', _plan_text(plan, short))}\n\nThe verified claims it may rest on:\n\n"
            f"{_claim_blocks(short)}\n\nThe person's k (actual time over estimated time): {k_history}.\n\n"
            "Return one item per real problem, naming the task ids it concerns. Return an empty list when "
            "there is none.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    reply = complete_json("reviewer", messages, CritiqueDraft, run=run, agent="reviewer",
                          step=f"critique_round_{round}")
    known = {task.id for task in plan.tasks}
    unknown = sum(task_id not in known for item in reply.items for task_id in item.task_ids)
    if unknown:
        run.trace.event(type="check", name="id_in_set", agent="reviewer", step=f"critique_round_{round}",
                        verdict="fail", detail={"unknown_task_ids": unknown})
    items = [CritiqueItem(id=f"K{number}", type=item.type, severity=item.severity, evidence=item.evidence,
                          fix=item.fix, task_ids=[task_id for task_id in item.task_ids if task_id in known])
             for number, item in enumerate(reply.items, start=1)]
    return Critique(plan_id=plan.id, round=round, items=items)


def answer_critique(run: Run, plan: Plan, critique: Critique, claims: list[Claim],
                    constraints: Constraints) -> tuple[Critique, Plan]:
    """The Planner accepts or rejects every critique item with a reason and returns the revised plan."""
    if not critique.items:
        return critique, plan
    short, item_ids = _short_ids(claims), [item.id for item in critique.items]

    def problems(reply: CritiqueAnswers) -> list[str]:
        answered = [answer.item_id for answer in reply.answers]
        found = []
        if [item_id for item_id in item_ids if item_id not in answered]:
            found.append(f"every critique item must be answered; missing: "
                         f"{[item_id for item_id in item_ids if item_id not in answered]}")
        if len(set(answered)) != len(answered) or set(answered) - set(item_ids):
            found.append("answer each item id exactly once, and no other id")
        return found + plan_problems(reply.plan, constraints, set(short))

    items = "\n".join(f"{item.id} [{item.type}, {item.severity}, tasks: {', '.join(item.task_ids) or 'none'}] "
                      f"{item.evidence} Fix: {item.fix}" for item in critique.items)
    system = prompts.role_prompt("planner") + "\n\n" + prompts.skill("study-planning")
    task = (f"Goal: {plan.goal}\n\nYour plan:\n{wrap_data('plan', _plan_text(plan, short))}\n\n"
            f"The Reviewer's critique:\n{wrap_data('critique', items)}\n\nThe verified claims:\n\n"
            f"{_claim_blocks(short)}\n\nConstraints:\n{_rules(constraints)}\n\n"
            "Answer every item: accept or reject, with a reason of one sentence. Then return the whole plan, "
            "revised for the items you accepted and unchanged otherwise.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    reply = complete_json("writer", messages, _checked(CritiqueAnswers, problems), run=run, agent="planner",
                          step=f"answer_round_{critique.round}")
    by_id = {answer.item_id: answer for answer in reply.answers}
    answered = Critique(plan_id=critique.plan_id, round=critique.round, items=[
        item.model_copy(update={"answer": by_id[item.id].answer, "reason": by_id[item.id].reason})
        for item in critique.items])
    if not any(answer.answer == "accept" for answer in reply.answers):
        return answered, plan  # nothing accepted, so nothing changes
    return answered, _to_plan(reply.plan, plan.id, plan.version + 1, plan.goal, plan.gap_id, constraints.owner, short)


def plan_diff(old: Plan, new: Plan) -> str:
    """What changed between two versions of a plan, one line per task."""
    before, after = {task.id: task for task in old.tasks}, {task.id: task for task in new.tasks}
    lines = [f"- {task.id} {task.name}" for task_id, task in before.items() if task_id not in after]
    lines += [f"+ {task.id} {task.name} ({task.hours:g} h, {task.priority})"
              for task_id, task in after.items() if task_id not in before]
    for task_id, task in after.items():
        if task_id in before:
            changes = [f"{name} {getattr(before[task_id], name)} -> {getattr(task, name)}"
                       for name in ("name", "hours", "depends_on", "priority", "milestone_id", "type")
                       if getattr(before[task_id], name) != getattr(task, name)]
            if changes:
                lines.append(f"~ {task_id}: " + "; ".join(changes))
    return "\n".join(lines) or "no change to the tasks"


def scale_by_k(plan: Plan, k: float) -> Plan:
    """The plan with every task's hours multiplied by k. The model writes raw estimates; code applies k."""
    return plan.model_copy(update={"tasks": [task.model_copy(update={"hours": round(task.hours * k, 2)})
                                             for task in plan.tasks]})
