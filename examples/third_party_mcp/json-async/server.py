"""Loopback-only remote-job demo. Requires FastMCP, not BenchThread or Artifact."""
import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager, closing, contextmanager, suppress
from pathlib import Path
from uuid import uuid4

from fastmcp import FastMCP

STATE = Path(os.environ.get("JSON_ASYNC_DB", "json-async-jobs.sqlite")).expanduser()


@contextmanager
def database():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(STATE, timeout=5)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            request_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL,
            numbers TEXT NOT NULL, result INTEGER)""")
        yield db


def complete_one():
    # ponytail: one short SQLite transaction suffices for this tiny pure calculation;
    # real long calculations need a separate executor, outside the DB transaction.
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT job_id, numbers FROM jobs WHERE result IS NULL ORDER BY rowid LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET result=? WHERE job_id=?", (sum(json.loads(row[1])), row[0]))


@asynccontextmanager
async def lifespan(_server):
    async def worker():
        while True:
            await asyncio.sleep(1)
            await asyncio.to_thread(complete_one)

    task = asyncio.create_task(worker())
    try:
        yield {}
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


mcp = FastMCP("JSON async sum", lifespan=lifespan)


@mcp.tool
def submit_sum(request_id: str, tool: str, arguments: dict) -> dict:
    """Persist the request and return its job ID without waiting for computation."""
    numbers = arguments.get("numbers")
    if (tool != "sum_numbers" or set(arguments) != {"numbers"}
            or not isinstance(numbers, list) or not 1 <= len(numbers) <= 100
            or any(type(n) is not int or abs(n) > 1_000_000 for n in numbers)
            or not 1 <= len(request_id) <= 128):
        raise ValueError("sum_numbers requires 1–100 integers in [-1000000, 1000000]")
    payload = json.dumps(numbers)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT job_id, numbers, result FROM jobs WHERE request_id=?", (request_id,)).fetchone()
        if row:
            if row[1] != payload:
                raise ValueError("request_id already used with different numbers")
            return {"job_id": row[0], "status": "queued" if row[2] is None else "completed"}
        job_id = uuid4().hex
        db.execute("INSERT INTO jobs VALUES (?, ?, ?, NULL)", (request_id, job_id, payload))
    return {"job_id": job_id, "status": "queued"}


def get_result(job_id):
    with database() as db:
        row = db.execute("SELECT result FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise ValueError("job not found")
    return row[0]


@mcp.tool
def get_sum_status(job_id: str) -> dict:
    return {"job_id": job_id, "status": "queued" if get_result(job_id) is None else "completed"}


@mcp.tool
def get_sum_result(job_id: str) -> dict:
    result = get_result(job_id)
    if result is None:
        raise ValueError("job not completed")
    return {"sum": result}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=int(os.environ.get("JSON_ASYNC_PORT", "10231")))
