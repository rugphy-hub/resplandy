import copy
import shutil
from datetime import date

import pytest

from resplandy.harness import start_run
from resplandy.llm import NeedsPerson
from resplandy.schemas import Claim, Critique, CritiqueItem, Gap
from resplandy.tools import planning

GOOD = {
    "milestones": [{"id": "M1", "name": "TEST MILESTONE", "done_when": "the test table exists", "due": "2026-12-10"}],
    "tasks": [
        {"id": "T1", "milestone_id": "M1", "name": "TEST TASK A", "type": "read", "hours": 4, "depends_on": [],
         "priority": "must", "claim_ids": ["C1"]},
        {"id": "T2", "milestone_id": "M1", "name": "TEST TASK B", "type": "analysis", "hours": 6,
         "depends_on": ["T1"], "priority": "should", "claim_ids": []},
    ],
}
CLAIMS = [Claim(id="run-c1", text="TEST CLAIM 1 about made-up sorbents.", paper_id="doi:10.0000/test.a",
                quote="test quote 1", location="abstract", status="verified")]
GAP = Gap(id="run-g1", statement="A candidate gap: humid test data is thin.", claim_ids=["run-c1"], status="chosen")


def changed(**task_changes) -> dict:
    plan = copy.deepcopy(GOOD)
    plan["tasks"][1].update(task_changes)
    return plan


@pytest.fixture
def limits(cfg):
    return planning.Constraints(deadline=date(2026, 12, 17), today=date(2026, 11, 19), free_hours=100.0, k=1.0,
                                max_task_hours=cfg.planning.max_task_hours, owner=cfg.planning.default_owner)


def forget(cfg):
    """Empty the LLM cache: the same prompt would otherwise get the answer an earlier case was given."""
    shutil.rmtree(cfg.paths.cache, ignore_errors=True)


def draft(cfg, scripted, limits, *replies):
    forget(cfg)
    client = scripted(PlanDraft=list(replies))
    return planning.draft_plan(start_run(cfg, client=client), "TEST GOAL?", CLAIMS, GAP, limits), client


def test_a_valid_draft_becomes_version_1_with_real_claim_ids(cfg, scripted, limits):
    plan, client = draft(cfg, scripted, limits, GOOD)
    assert (plan.version, plan.goal, plan.gap_id) == (1, "TEST GOAL?", "run-g1")
    assert [(t.id, t.hours, t.owner, t.claim_ids) for t in plan.tasks] == [
        ("T1", 4, cfg.planning.default_owner, ["run-c1"]), ("T2", 6, cfg.planning.default_owner, [])]
    assert client.requests[0]["model"] == cfg.models.writer


def test_a_plan_that_breaks_a_rule_gets_one_repair_round_then_needs_a_person(cfg, scripted, limits):
    broken = changed(depends_on=["T9"])
    with pytest.raises(NeedsPerson):
        draft(cfg, scripted, limits, broken, broken, GOOD)
    plan, client = draft(cfg, scripted, limits, changed(hours=12), GOOD)   # a 12-hour task is not "a day or less"
    assert len(client.requests) == 2 and "at most 8" in client.requests[1]["messages"][-1]["content"]
    assert [t.hours for t in plan.tasks] == [4, 6]


@pytest.mark.parametrize("bad, words", [
    (changed(depends_on=["T2"]), "cycle"), (changed(milestone_id="M9"), "milestone"),
    (changed(claim_ids=["C5"]), "claim"), (changed(id="T1"), "unique"),
])
def test_plan_rules(cfg, limits, bad, words):
    problems = planning.plan_problems(planning.PlanDraft.model_validate(bad), limits, {"C1"})
    assert any(words in problem for problem in problems)
    assert planning.plan_problems(planning.PlanDraft.model_validate(GOOD), limits, {"C1"}) == []


def test_the_reviewer_is_the_other_family_and_may_only_name_real_tasks(cfg, scripted, limits):
    plan, _ = draft(cfg, scripted, limits, GOOD)
    reply = {"items": [{"type": "missing_step", "severity": "high", "task_ids": ["T2", "T9"],
                        "evidence": "TEST EVIDENCE", "fix": "TEST FIX"}]}
    client = scripted(CritiqueDraft=[reply, {"items": []}])
    run = start_run(cfg, client=client)
    critique = planning.critique_plan(run, plan, CLAIMS, {"k": 1.0}, round=1)
    assert client.requests[0]["model"] == cfg.models.reviewer
    assert [(i.id, i.type, i.task_ids, i.answer) for i in critique.items] == [("K1", "missing_step", ["T2"], None)]
    assert planning.critique_plan(run, plan, CLAIMS, {"k": 1.0}, round=2).items == []   # "no issues"


def test_every_critique_item_must_be_answered(cfg, scripted, limits):
    plan, _ = draft(cfg, scripted, limits, GOOD)
    critique = Critique(plan_id=plan.id, round=1, items=[
        CritiqueItem(id="K1", type="missing_step", severity="high", evidence="TEST EVIDENCE", fix="TEST FIX"),
        CritiqueItem(id="K2", type="estimate", severity="low", evidence="TEST EVIDENCE", fix="TEST FIX")])
    silent = {"answers": [{"item_id": "K1", "answer": "accept", "reason": "Agreed."}], "plan": GOOD}
    with pytest.raises(NeedsPerson):
        planning.answer_critique(start_run(cfg, client=scripted(CritiqueAnswers=silent)), plan, critique, CLAIMS,
                                 limits)

    longer = changed(hours=8)
    full = {"answers": [{"item_id": "K1", "answer": "accept", "reason": "Agreed."},
                        {"item_id": "K2", "answer": "reject", "reason": "The estimate stands."}], "plan": longer}
    forget(cfg)
    answered, revised = planning.answer_critique(start_run(cfg, client=scripted(CritiqueAnswers=full)), plan, critique,
                                                 CLAIMS, limits)
    assert [(i.answer, i.reason) for i in answered.items] == [("accept", "Agreed."), ("reject", "The estimate stands.")]
    assert (revised.id, revised.version) == (plan.id, 2)
    assert "T2" in planning.plan_diff(plan, revised) and "6" in planning.plan_diff(plan, revised)

    refused = {"answers": [{"item_id": "K1", "answer": "reject", "reason": "No."},
                           {"item_id": "K2", "answer": "reject", "reason": "No."}], "plan": longer}
    forget(cfg)
    _, same = planning.answer_critique(start_run(cfg, client=scripted(CritiqueAnswers=refused)), plan, critique,
                                       CLAIMS, limits)
    assert same == plan   # nothing accepted, so nothing changes


def test_code_scales_the_hours_by_k(cfg, scripted, limits):
    plan, _ = draft(cfg, scripted, limits, GOOD)
    scaled = planning.scale_by_k(plan, 1.5)
    assert [t.hours for t in scaled.tasks] == [6.0, 9.0] and [t.hours for t in plan.tasks] == [4, 6]
