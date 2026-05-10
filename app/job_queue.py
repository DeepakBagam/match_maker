from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Callable
from uuid import uuid4
import os


JobWorker = Callable[[], dict[str, Any]]
_MAX_JOB_WORKERS = max(2, min(4, os.cpu_count() or 2))
_JOB_TTL = timedelta(hours=6)


@dataclass
class JobRecord:
    job_id: str
    kind: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(sep=" ", timespec="seconds"))
    started_at: str = ""
    finished_at: str = ""
    result: dict[str, Any] | None = None
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_executor = ThreadPoolExecutor(max_workers=_MAX_JOB_WORKERS, thread_name_prefix="matchlayer-job")
_jobs: dict[str, JobRecord] = {}
_jobs_lock = Lock()


def _prune_jobs(now: datetime | None = None) -> None:
    cutoff = (now or datetime.now()) - _JOB_TTL
    stale_ids: list[str] = []
    for job_id, record in _jobs.items():
        reference = record.finished_at or record.created_at
        try:
            timestamp = datetime.fromisoformat(reference)
        except ValueError:
            continue
        if timestamp < cutoff:
            stale_ids.append(job_id)
    for job_id in stale_ids:
        _jobs.pop(job_id, None)


def submit_job(kind: str, worker: JobWorker) -> dict[str, Any]:
    record = JobRecord(job_id=uuid4().hex, kind=kind)
    with _jobs_lock:
        _prune_jobs()
        _jobs[record.job_id] = record
    _executor.submit(_run_job, record.job_id, worker)
    return record.to_dict()


def _run_job(job_id: str, worker: JobWorker) -> None:
    with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            return
        record.status = "running"
        record.started_at = datetime.now().isoformat(sep=" ", timespec="seconds")

    try:
        result = worker() or {}
    except Exception as exc:
        with _jobs_lock:
            record = _jobs.get(job_id)
            if record is None:
                return
            record.status = "failure"
            record.finished_at = datetime.now().isoformat(sep=" ", timespec="seconds")
            record.error_message = f"{exc.__class__.__name__}: {exc}"
        return

    with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            return
        record.status = "success"
        record.finished_at = datetime.now().isoformat(sep=" ", timespec="seconds")
        record.result = result


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        _prune_jobs()
        record = _jobs.get(job_id)
        return record.to_dict() if record else None
