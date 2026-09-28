"""Persistent batch import queue: SQLite job table + one in-process asyncio worker.

Exactly one item is processed at a time. YouTube and podcasts are separate "lanes":
YouTube is paced (random pause between items, hourly cap, backoff on blocks), podcasts
only get a short pause. A crash loses at most the running item, which is reset to
pending on the next start. Independent: the per-item work is injected by `main.py`.
"""

import asyncio
import json
import random
import sqlite3
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable


DB_PATH = Path(__file__).parent / "data" / "batch.db"

YOUTUBE_PAUSE_SECONDS = (15, 45)
PODCAST_PAUSE_SECONDS = (2, 5)
YOUTUBE_HOURLY_CAP = 60
BLOCK_BACKOFF_SECONDS = [30 * 60, 2 * 3600, 6 * 3600]
CONFIRM_TIMEOUT_SECONDS = 30 * 60
IDLE_POLL_SECONDS = 30

LANES = ("youtube", "podcast")
FINISHED_STATES = ("done", "skipped", "failed", "duplicate", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    state TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    options_json TEXT NOT NULL,
    vault_json TEXT NOT NULL,
    estimate_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES batches(id),
    pos INTEGER NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    external_id TEXT NOT NULL,
    guid TEXT,
    title TEXT NOT NULL DEFAULT '',
    duration_s INTEGER NOT NULL DEFAULT 0,
    cost_estimate REAL NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    progress TEXT,
    note_path TEXT,
    cost_usd REAL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS items_state ON items(state, batch_id, pos);
CREATE TABLE IF NOT EXISTS lanes (
    name TEXT PRIMARY KEY,
    next_allowed_at REAL NOT NULL DEFAULT 0,
    blocked_until REAL NOT NULL DEFAULT 0,
    backoff_level INTEGER NOT NULL DEFAULT 0,
    block_reason TEXT
);
"""

_wake: asyncio.Event | None = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.executemany("INSERT OR IGNORE INTO lanes(name) VALUES (?)", [(l,) for l in LANES])
        # The worker died mid-item: run it again
        conn.execute("UPDATE items SET state='pending', started_at=NULL WHERE state='running'")
    expire_unconfirmed()


def wake() -> None:
    if _wake is not None:
        _wake.set()


# ─── Batches ─────────────────────────────────────────────────────────────────

def create_batch(
    source: str, source_url: str, title: str,
    options: dict, vault: dict, estimate: dict, items: list[dict],
) -> str:
    batch_id = uuid.uuid4().hex[:12]
    with _connect() as conn:
        conn.execute(
            "INSERT INTO batches VALUES (?,?,?,?,?,?,?,?,?)",
            (batch_id, time.time(), "pending_confirmation", source, source_url, title,
             json.dumps(options), json.dumps(vault), json.dumps(estimate)),
        )
        conn.executemany(
            "INSERT INTO items(batch_id,pos,source,url,external_id,guid,title,duration_s,cost_estimate)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(batch_id, i, source, it["url"], it["external_id"], it.get("guid"), it["title"],
              it["duration_seconds"], it.get("cost_estimate", 0)) for i, it in enumerate(items)],
        )
    return batch_id


def expire_unconfirmed() -> None:
    cutoff = time.time() - CONFIRM_TIMEOUT_SECONDS
    with _connect() as conn:
        conn.execute(
            "UPDATE items SET state='cancelled' WHERE state='pending' AND batch_id IN "
            "(SELECT id FROM batches WHERE state='pending_confirmation' AND created_at < ?)", (cutoff,))
        conn.execute(
            "UPDATE batches SET state='expired' WHERE state='pending_confirmation' AND created_at < ?", (cutoff,))


def confirm(batch_id: str) -> bool:
    expire_unconfirmed()
    with _connect() as conn:
        ok = conn.execute(
            "UPDATE batches SET state='queued' WHERE id=? AND state='pending_confirmation'", (batch_id,)
        ).rowcount == 1
    if ok:
        wake()
    return ok


def cancel(batch_id: str) -> bool:
    """Pending items are cancelled; a running item finishes normally."""
    with _connect() as conn:
        ok = conn.execute(
            "UPDATE batches SET state='cancelled' WHERE id=? AND state IN ('pending_confirmation','queued')",
            (batch_id,),
        ).rowcount == 1
        conn.execute("UPDATE items SET state='cancelled' WHERE batch_id=? AND state='pending'", (batch_id,))
    return ok


def retry_failed(batch_id: str) -> int:
    with _connect() as conn:
        n = conn.execute(
            "UPDATE items SET state='pending', error=NULL, started_at=NULL, finished_at=NULL"
            " WHERE batch_id=? AND state='failed'", (batch_id,)).rowcount
        if n:
            conn.execute("UPDATE batches SET state='queued' WHERE id=?", (batch_id,))
    if n:
        wake()
    return n


def _batch_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    items = [dict(r) for r in conn.execute("SELECT * FROM items WHERE batch_id=? ORDER BY pos", (row["id"],))]
    counts: dict[str, int] = {}
    for it in items:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "state": row["state"],
        "source": row["source"],
        "source_url": row["source_url"],
        "title": row["title"],
        "options": json.loads(row["options_json"]),
        "estimate": json.loads(row["estimate_json"]),
        "counts": counts,
        "cost_usd": round(sum(it["cost_usd"] or 0 for it in items), 4),
        "items": items,
    }


def get_batch(batch_id: str, with_vault: bool = False) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if row is None:
            return None
        batch = _batch_dict(conn, row)
        if with_vault:
            batch["vault"] = json.loads(row["vault_json"])
        return batch


def list_batches(limit: int = 10) -> list[dict]:
    expire_unconfirmed()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM batches WHERE state != 'expired' ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_batch_dict(conn, r) for r in rows]


# ─── Lanes ───────────────────────────────────────────────────────────────────

def youtube_done_last_hour(now: float | None = None) -> int:
    since = (now or time.time()) - 3600
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM items WHERE source='youtube' AND finished_at > ?"
            " AND state IN ('done','skipped','failed')", (since,)).fetchone()[0]


def lane_status() -> dict:
    now = time.time()
    with _connect() as conn:
        lanes = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM lanes")}
    lanes["youtube"]["done_last_hour"] = youtube_done_last_hour(now)
    lanes["youtube"]["hourly_cap"] = YOUTUBE_HOURLY_CAP
    for lane in lanes.values():
        lane["blocked"] = lane["blocked_until"] > now
    return lanes


def _eligible_lanes(now: float) -> list[str]:
    status = lane_status()
    out = []
    for name in LANES:
        lane = status[name]
        if now < lane["next_allowed_at"] or now < lane["blocked_until"]:
            continue
        if name == "youtube" and lane["done_last_hour"] >= YOUTUBE_HOURLY_CAP:
            continue
        out.append(name)
    return out


def next_item(now: float) -> dict | None:
    lanes = _eligible_lanes(now)
    if not lanes:
        return None
    marks = ",".join("?" * len(lanes))
    with _connect() as conn:
        row = conn.execute(
            f"SELECT items.* FROM items JOIN batches ON batches.id = items.batch_id"
            f" WHERE batches.state='queued' AND items.state='pending' AND items.source IN ({marks})"
            f" ORDER BY batches.created_at, items.pos LIMIT 1", lanes).fetchone()
        return dict(row) if row else None


def _set_lane(conn: sqlite3.Connection, name: str, **fields) -> None:
    sets = ",".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE lanes SET {sets} WHERE name=?", (*fields.values(), name))


def clear_block(name: str) -> None:
    with _connect() as conn:
        _set_lane(conn, name, blocked_until=0, block_reason=None)
    wake()


# ─── Worker ──────────────────────────────────────────────────────────────────

def _mark_running(item_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE items SET state='running', started_at=?, progress=NULL WHERE id=?", (time.time(), item_id))


def set_progress(item_id: int, message: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE items SET progress=? WHERE id=?", (message[:300], item_id))


def _record_result(item: dict, result: dict) -> None:
    now = time.time()
    lane = item["source"]
    with _connect() as conn:
        lane_row = conn.execute("SELECT * FROM lanes WHERE name=?", (lane,)).fetchone()
        batch_state = conn.execute("SELECT state FROM batches WHERE id=?", (item["batch_id"],)).fetchone()[0]

        if result.get("blocked"):
            level = lane_row["backoff_level"]
            wait = BLOCK_BACKOFF_SECONDS[min(level, len(BLOCK_BACKOFF_SECONDS) - 1)]
            _set_lane(conn, lane, blocked_until=now + wait, backoff_level=level + 1,
                      block_reason=(result.get("error") or "blocked")[:300])
            back = "cancelled" if batch_state == "cancelled" else "pending"
            conn.execute("UPDATE items SET state=?, started_at=NULL, error=?, progress=NULL WHERE id=?",
                         (back, result.get("error"), item["id"]))
            return

        conn.execute(
            "UPDATE items SET state=?, error=?, note_path=?, cost_usd=?, finished_at=?, progress=NULL WHERE id=?",
            (result["state"], result.get("error"), result.get("note_path"), result.get("cost_usd"), now, item["id"]),
        )
        if result.get("network", True):
            lo, hi = YOUTUBE_PAUSE_SECONDS if lane == "youtube" else PODCAST_PAUSE_SECONDS
            _set_lane(conn, lane, next_allowed_at=now + random.uniform(lo, hi))
        if result["state"] == "done":
            _set_lane(conn, lane, backoff_level=0)

        left = conn.execute(
            "SELECT COUNT(*) FROM items WHERE batch_id=? AND state IN ('pending','running')",
            (item["batch_id"],)).fetchone()[0]
        if not left and batch_state == "queued":
            conn.execute("UPDATE batches SET state='done' WHERE id=?", (item["batch_id"],))


def _seconds_until_something_is_eligible(now: float) -> float:
    status = lane_status()
    waits = [max(l["next_allowed_at"], l["blocked_until"]) - now for l in status.values()]
    future = [w for w in waits if w > 0]
    return max(1.0, min(min(future, default=IDLE_POLL_SECONDS), IDLE_POLL_SECONDS))


async def run_worker(process_item: Callable[[dict, dict], dict]) -> None:
    """process_item(item, batch) runs in a thread and returns
    {state, note_path?, cost_usd?, error?, blocked?, network?}."""
    global _wake
    _wake = asyncio.Event()
    while True:
        try:
            now = time.time()
            item = next_item(now)
            if item is None:
                expire_unconfirmed()
                _wake.clear()
                try:
                    await asyncio.wait_for(_wake.wait(), _seconds_until_something_is_eligible(now))
                except asyncio.TimeoutError:
                    pass
                continue

            batch = get_batch(item["batch_id"], with_vault=True)
            _mark_running(item["id"])
            try:
                result = await asyncio.to_thread(process_item, item, batch)
            except Exception as e:
                traceback.print_exc()
                result = {"state": "failed", "error": f"Unexpected error: {e}"}
            _record_result(item, result)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The worker must never die
            traceback.print_exc()
            await asyncio.sleep(5)
