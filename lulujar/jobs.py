"""Background extraction jobs with progress reporting and cancellation."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from . import config

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class Job:
    """A single extraction run."""

    id: str
    source_name: str
    source_path: str
    destination: str
    kind: str = ""
    status: str = QUEUED
    phase: str = "queued"
    percent: int = 0
    current: str = ""
    bytes_done: int = 0
    bytes_total: int = 0
    files_done: int = 0
    files_total: int = 0
    engine: str = ""
    error: str = ""
    result: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    finished: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_name": self.source_name,
            "source_path": self.source_path,
            "destination": self.destination,
            "kind": self.kind,
            "status": self.status,
            "phase": self.phase,
            "percent": self.percent,
            "current": self.current,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "files_done": self.files_done,
            "files_total": self.files_total,
            "engine": self.engine,
            "error": self.error,
            "result": self.result,
            "warnings": self.warnings,
            "created": self.created,
            "updated": self.updated,
            "finished": self.finished,
        }


class JobManager:
    """Thread-safe registry of extraction jobs."""

    def __init__(self, persist: bool = True):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._cancelled: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._persist = persist

    # -- persistence ------------------------------------------------------
    def _job_file(self, job_id: str) -> Path:
        return config.home_dir() / "jobs" / f"{job_id}.json"

    def _save(self, job: Job) -> None:
        if not self._persist:
            return
        try:
            path = self._job_file(job.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(job.to_dict(), handle, indent=2)
            os.replace(tmp, path)
        except OSError:
            pass

    def load_previous(self) -> List[Job]:
        """Reload jobs that were running when the server stopped."""
        directory = config.home_dir() / "jobs"
        if not directory.is_dir():
            return []
        restored: List[Job] = []
        for file in directory.glob("*.json"):
            try:
                with file.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            job = Job(
                id=data.get("id", file.stem),
                source_name=data.get("source_name", ""),
                source_path=data.get("source_path", ""),
                destination=data.get("destination", ""),
                kind=data.get("kind", ""),
                status=data.get("status", FAILED),
                phase=data.get("phase", ""),
                percent=data.get("percent", 0),
                current=data.get("current", ""),
                bytes_done=data.get("bytes_done", 0),
                bytes_total=data.get("bytes_total", 0),
                files_done=data.get("files_done", 0),
                files_total=data.get("files_total", 0),
                engine=data.get("engine", ""),
                error=data.get("error", ""),
                result=data.get("result", {}),
                warnings=data.get("warnings", []),
                created=data.get("created", time.time()),
                updated=data.get("updated", time.time()),
            )
            if job.status in (QUEUED, RUNNING):
                job.status = FAILED
                job.error = "the server was restarted while this job was running"
                job.finished = time.time()
            with self._lock:
                self._jobs[job.id] = job
            restored.append(job)
        restored.sort(key=lambda j: j.created, reverse=True)
        return restored

    # -- api --------------------------------------------------------------
    def create(
        self,
        source_name: str,
        source_path: str,
        destination: str,
        kind: str = "",
    ) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            source_name=source_name,
            source_path=source_path,
            destination=destination,
            kind=kind,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._cancelled[job.id] = threading.Event()
        self._save(job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> List[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
        return jobs[:limit]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancelled.get(job_id)
            job = self._jobs.get(job_id)
        if event is None or job is None:
            return False
        if job.status in (DONE, FAILED, CANCELLED):
            return False
        event.set()
        job.phase = "cancelling"
        job.updated = time.time()
        self._save(job)
        return True

    def run(self, job: Job, worker) -> None:
        """Run ``worker(job, progress, cancel)`` in a background thread."""

        def target() -> None:
            event = self._cancelled.setdefault(job.id, threading.Event())
            job.status = RUNNING
            job.phase = "starting"
            job.updated = time.time()
            self._save(job)

            def progress(payload: Dict[str, object]) -> None:
                for key, value in payload.items():
                    if hasattr(job, key):
                        setattr(job, key, value)
                job.updated = time.time()

            try:
                worker(job, progress, event.is_set)
                if event.is_set():
                    job.status = CANCELLED
                    job.phase = "cancelled"
                else:
                    job.status = DONE
                    job.phase = "done"
                    job.percent = 100
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                if event.is_set():
                    job.status = CANCELLED
                    job.phase = "cancelled"
                else:
                    job.status = FAILED
                    job.phase = "failed"
                    job.error = str(exc) or exc.__class__.__name__
            finally:
                job.finished = time.time()
                job.updated = job.finished
                self._save(job)

        thread = threading.Thread(target=target, name=f"lulujar-job-{job.id}", daemon=True)
        with self._lock:
            self._threads[job.id] = thread
        thread.start()

    def wait(self, job_id: str, timeout: float = 30.0) -> Optional[Job]:
        """Block until a job finishes (used by the CLI)."""
        with self._lock:
            thread = self._threads.get(job_id)
        if thread is None:
            return self.get(job_id)
        thread.join(timeout)
        return self.get(job_id)

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            self._jobs.pop(job_id, None)
            self._cancelled.pop(job_id, None)
            self._threads.pop(job_id, None)
        try:
            self._job_file(job_id).unlink()
        except OSError:
            pass
        return True
