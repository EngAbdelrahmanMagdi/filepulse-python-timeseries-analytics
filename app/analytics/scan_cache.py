import logging
from datetime import UTC, datetime
from threading import Lock

from app.analytics.static_analysis import analyze, scan
from app.api.schemas import FileSummary, ScanResponse, ScanStatus
from app.core.paths import RootPaths

logger = logging.getLogger(__name__)


class ScanCache:
    """One lazy attempt, then explicit refresh only; no filesystem work in status()."""

    def __init__(self, paths: RootPaths) -> None:
        self.paths = paths
        self._scan_lock = Lock()
        self._state_lock = Lock()
        self._result = ScanResponse()

    def status(self) -> ScanStatus:
        with self._state_lock:
            return ScanStatus.model_validate(self._result.model_dump(exclude={"summary"}))

    def get(self, *, refresh: bool = False) -> ScanResponse:
        with self._scan_lock:
            if refresh or self._result.state == "not_scanned":
                try:
                    frame, skipped = scan(self.paths)
                    result = ScanResponse(
                        state="ready",
                        scanned_at=datetime.now(UTC),
                        skipped_entries=skipped,
                        summary=FileSummary.model_validate(analyze(frame)),
                    )
                    logger.info("scan_complete files=%s skipped=%s", len(frame), skipped)
                except Exception as exc:
                    logger.warning("scan_failed reason=%s", type(exc).__name__)
                    result = self._result.model_copy(
                        update={
                            "state": "stale" if self._result.summary is not None else "failed",
                            "message": "Scan unavailable. Check the directory and retry Scan.",
                        }
                    )
                with self._state_lock:
                    self._result = result
            return self._result.model_copy(deep=True)
