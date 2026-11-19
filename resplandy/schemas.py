"""Data contracts: one Pydantic model per handoff. The receiver validates before use."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Page = Annotated[int, Field(ge=1)]
Priority = Literal["must", "should", "could"]


class Paper(BaseModel):
    """One retrieved paper: a database record, a link or a library file."""

    id: str
    source: Literal["openalex", "arxiv", "europepmc", "library", "link"]
    title: str
    authors: list[str] = []
    year: int | None = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    abstract: str = ""
    file: str | None = None
    pages: int | None = None


class LibraryFile(BaseModel):
    """One file in the study's paper folder, as the inventory shows it."""

    file: str
    title: str
    pages: int = 0
    status: Literal["matched", "unmatched", "unreadable"]
    doi: str | None = None
    paper_id: str | None = None


class Claim(BaseModel):
    """One statement tied to a verbatim quote from one paper. Only a verified claim is used downstream."""

    id: str
    text: str
    paper_id: str
    quote: str
    location: Page | Literal["abstract"]
    numbers: list[str] = []
    abstract_only: bool = False
    status: Literal["pending", "verified", "unverified"] = "pending"
    rewritten: bool = False


class Check(BaseModel):
    """One verdict on one claim, from code, the judge or a person."""

    claim_id: str
    kind: Literal["code", "judge", "human"]
    name: str
    passed: bool
    detail: str = ""


class Gap(BaseModel):
    """One candidate gap, tied to the verified claims it rests on."""

    id: str
    statement: str
    claim_ids: list[str]
    receipt_id: str | None = None
    status: Literal["candidate", "chosen", "dropped"] = "candidate"


class ReceiptQuery(BaseModel):
    """One query run for a search receipt, with its hit count per database."""

    text: str
    negative: bool = False
    hits: dict[str, int] = {}


class NearestPaper(BaseModel):
    """One of the closest papers to a gap, and why it is not the gap."""

    paper_id: str
    why_not: str


class Receipt(BaseModel):
    """The search behind a candidate gap: what was run, where, when, and the nearest papers."""

    gap_id: str
    queries: list[ReceiptQuery] = []
    databases: list[str] = []
    run_date: date
    nearest: list[NearestPaper] = []


class Milestone(BaseModel):
    """A plan milestone with the line that says when it is done."""

    id: str
    name: str
    done_when: str
    due: date


class Task(BaseModel):
    """One task of a day or less. hours is the raw estimate; code applies k."""

    id: str
    milestone_id: str
    name: str
    type: str
    owner: str
    hours: float = Field(gt=0)
    depends_on: list[str] = []
    priority: Priority = "must"
    claim_ids: list[str] = []


class Plan(BaseModel):
    """A versioned study plan."""

    id: str
    version: int = 1
    goal: str
    gap_id: str | None = None
    milestones: list[Milestone]
    tasks: list[Task]


class CritiqueItem(BaseModel):
    """One problem the Reviewer found, and the Planner's answer to it."""

    id: str
    type: str
    severity: Literal["low", "medium", "high"]
    evidence: str
    fix: str
    task_ids: list[str] = []
    answer: Literal["accept", "reject"] | None = None
    reason: str = ""


class Critique(BaseModel):
    """One round of critique on one plan."""

    plan_id: str
    round: int
    items: list[CritiqueItem] = []


class FreeBlock(BaseModel):
    """One block of free time from the calendar."""

    start: datetime
    end: datetime
    evening: bool = False


class Slot(BaseModel):
    """A stretch of one free block given to one task. A long task has several slots."""

    task_id: str
    start: datetime
    end: datetime


class ScheduleOption(BaseModel):
    """One of the three schedules the solver offers."""

    plan_version: int
    label: str
    slots: list[Slot] = []
    slack_hours: dict[str, float] = {}
    dropped_task_ids: list[str] = []


class CheckIn(BaseModel):
    """One tick on one task; the app records the timestamp."""

    task_id: str
    person: str
    done: bool
    comment: str = ""
    timestamp: datetime
    blocker_type: Literal["waiting", "underestimated", "unclear scope", "other"] | None = None


class Status(BaseModel):
    """The Tracker's verdict at one moment."""

    timestamp: datetime
    pv_hours: float
    ev_hours: float
    spi: float
    k: float
    slipping_task_ids: list[str] = []
    action: Literal["none", "remind", "replan"]
