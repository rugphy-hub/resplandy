"""The SQLite store: one table per handoff. A row keeps a few key columns and the validated model as JSON."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# Tables for what is built today, plus runs. lessons, judge_labels and audit_rounds are designed, not built.
TABLES: dict[str, str] = {
    "projects": "id TEXT PRIMARY KEY, name TEXT, library_folder TEXT, created_at TEXT, data TEXT",
    "runs": "id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, status TEXT, started_at TEXT, ended_at TEXT, "
            "llm_calls INTEGER, cost_usd REAL, trace_path TEXT, data TEXT",
    "library_files": "project_id TEXT, file TEXT, status TEXT, paper_id TEXT, data TEXT, "
                     "PRIMARY KEY (project_id, file)",
    "papers": "project_id TEXT, id TEXT, run_id TEXT, source TEXT, doi TEXT, data TEXT, "
              "PRIMARY KEY (project_id, id)",
    "claims": "id TEXT PRIMARY KEY, project_id TEXT, run_id TEXT, paper_id TEXT, status TEXT, data TEXT",
    "checks": "id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, run_id TEXT, kind TEXT, name TEXT, "
              "passed INTEGER, data TEXT",
    "gaps": "id TEXT PRIMARY KEY, project_id TEXT, run_id TEXT, status TEXT, data TEXT",
    "receipts": "gap_id TEXT PRIMARY KEY, data TEXT",
    "plans": "id TEXT, version INTEGER, project_id TEXT, status TEXT, diff TEXT, data TEXT, "
             "PRIMARY KEY (id, version)",
    "critiques": "plan_id TEXT, plan_version INTEGER, round INTEGER, data TEXT, "
                 "PRIMARY KEY (plan_id, plan_version, round)",
    "schedules": "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, plan_id TEXT, plan_version INTEGER, "
                 "label TEXT, chosen INTEGER, data TEXT",
    "checkins": "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, task_id TEXT, person TEXT, "
                "done INTEGER, ts TEXT, data TEXT",
    "status_history": "id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, ts TEXT, spi REAL, action TEXT, "
                      "data TEXT",
    "user_model": "person TEXT, task_type TEXT, k REAL, n INTEGER, updated_at TEXT, "
                  "PRIMARY KEY (person, task_type)",
}


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database, creating its folder, with rows addressable by column name."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create every table that does not exist yet."""
    for name, columns in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")
    conn.commit()


def _columns(conn: sqlite3.Connection, table: str, used: set[str]) -> None:
    """Refuse a table or a column we did not define, so no name from outside reaches the SQL text."""
    if table not in TABLES:
        raise ValueError(f"Unknown table: {table!r}")
    unknown = used - {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if unknown:
        raise ValueError(f"Unknown column(s) for {table}: {sorted(unknown)}")


def put(conn: sqlite3.Connection, table: str, keys: dict[str, Any], model: BaseModel | None = None) -> None:
    """Insert or replace one row: the key columns, plus the model as JSON in the data column."""
    row = dict(keys)
    if model is not None:
        row["data"] = model.model_dump_json()
    _columns(conn, table, set(row))
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(row)}) VALUES ({marks})", list(row.values()))
    conn.commit()


def rows(conn: sqlite3.Connection, table: str, **where: Any) -> list[sqlite3.Row]:
    """The rows of one table that match every given column value, oldest first."""
    _columns(conn, table, set(where))
    clause = " AND ".join(f"{name} = ?" for name in where)
    sql = f"SELECT * FROM {table}" + (f" WHERE {clause}" if clause else "") + " ORDER BY rowid"
    return conn.execute(sql, list(where.values())).fetchall()


def load(conn: sqlite3.Connection, table: str, model: type[T], **where: Any) -> list[T]:
    """The matching rows of one table, validated back into their contract."""
    return [model.model_validate_json(row["data"]) for row in rows(conn, table, **where)]
