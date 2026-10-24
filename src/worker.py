"""Queue worker: claims `queued` incidents and runs the analysis pipeline.

Run inside the API process (EMBEDDED_WORKER=true, default) or as separate processes:

    python -m src.worker

Any number of workers may run against the same database: claims are compare-and-set updates with a lease.
"""

from __future__ import annotations

import logging
import pathlib
import signal
import sys
import threading
import time
import uuid
from datetime import timedelta
from types import FrameType

from sqlalchemy.exc import SQLAlchemyError

from src import metrics
from src.config import settings
from src.db import repositories
from src.errors import ErrorCategory
from src.services import incident_service
from src.services.incident_service import DatabaseUnavailableError

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 30.0


def touch_liveness() -> None:
    """Record that this worker is alive (polling or analysing); read by the container healthcheck."""
    if not settings.worker_liveness_file:
        return
    try:
        pathlib.Path(settings.worker_liveness_file).touch()
    except OSError as exc:
        logger.warning("could not update the liveness file: %s", type(exc).__name__)


def is_alive(max_age_seconds: float = 120.0) -> bool:
    """Healthcheck helper: True if the liveness file was touched within `max_age_seconds`."""
    try:
        age = time.time() - pathlib.Path(settings.worker_liveness_file).stat().st_mtime
    except OSError:
        return False
    return age <= max_age_seconds


class LeaseHeartbeat:
    """Renews a claim every lease/3 while the analysis runs, so the lease can be short (fast crash recovery)
    without a slow analysis being reclaimed by another worker. Renewal failures are logged, never raised."""

    def __init__(
        self, incident_id: uuid.UUID, claim_token: str, lease: timedelta, interval: float | None = None
    ) -> None:
        self.incident_id = incident_id
        self.claim_token = claim_token
        self.lease = lease
        # Renew well before expiry; never wait longer than 30 s so liveness (below) stays fresh during analysis.
        self.interval = interval if interval is not None else min(max(lease.total_seconds() / 3, 1.0), 30.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="opspulse-lease", daemon=True)
        self.renewals = 0

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            touch_liveness()
            try:
                if not repositories.renew_lease(self.incident_id, self.claim_token, self.lease):
                    return  # finished or no longer ours
                self.renewals += 1
            except SQLAlchemyError as exc:
                logger.warning("lease renewal failed: %s", type(exc).__name__)

    def __enter__(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lease = timedelta(seconds=settings.job_lease_seconds)

    def recover_expired(self) -> tuple[int, int]:
        requeued, failed = repositories.requeue_expired(
            settings.max_job_attempts,
            self.lease,
            f"analysis was interrupted {settings.max_job_attempts} time(s) (worker stopped); re-submit the incident",
            ErrorCategory.INTERRUPTED.value,
        )
        if requeued or failed:
            logger.warning("expired claims: %d re-queued, %d failed", requeued, failed)
            metrics.JOBS_RECOVERED.labels("requeued").inc(requeued)
            metrics.JOBS_RECOVERED.labels("failed").inc(failed)
        return requeued, failed

    def run_once(self) -> bool:
        """Recover expired claims, then process at most one incident. True if an incident was processed."""
        try:
            self.recover_expired()
            job = repositories.claim_next(self.lease)
            metrics.QUEUE_DEPTH.set(repositories.queue_depth())
        except SQLAlchemyError as exc:
            raise DatabaseUnavailableError("database unavailable") from exc
        if job is None:
            return False
        logger.info("claimed incident %s (claim %d)", job["incident_id"], job["job_attempts"])
        with LeaseHeartbeat(job["incident_id"], job["claim_token"], self.lease):
            incident_service.run_incident_pipeline(
                job["incident_id"],
                job["repo_name"],
                job["error_message"],
                job["stack_trace"],
                claim_token=job["claim_token"],
                commit_sha=job.get("commit_sha"),
            )
        return True

    def run_forever(self) -> None:
        backoff = settings.worker_poll_seconds
        while not self._stop.is_set():
            touch_liveness()
            try:
                busy = self.run_once()
                backoff = settings.worker_poll_seconds
            except DatabaseUnavailableError:
                logger.error("worker: database unavailable, retrying in %.0fs", backoff)
                busy = False
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
            except Exception:  # loop boundary: log and keep serving; the claim's lease re-queues the incident
                logger.exception("worker iteration failed")
                busy = False
            if not busy:
                self._stop.wait(backoff)

    def start_in_thread(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="opspulse-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the loop to stop. An analysis in progress is not interrupted; its lease covers a hard stop."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)


def main() -> int:
    if sys.argv[1:] == ["--healthcheck"]:
        return 0 if is_alive() else 1
    from src.logging_config import configure_logging

    configure_logging(settings.log_level)
    worker = Worker()

    def shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("signal %d received, stopping after the current incident", signum)
        worker.stop(timeout=0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    logger.info("worker started (poll=%.1fs, lease=%ss)", settings.worker_poll_seconds, settings.job_lease_seconds)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
