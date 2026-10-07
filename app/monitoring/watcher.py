import logging
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from app.core.config import Settings
from app.core.filesystem import collect_metadata, file_metadata
from app.core.logging import configure_logging
from app.core.models import FileEvent
from app.core.paths import RootPaths, UnsafePath
from app.monitoring.udp_client import UdpSender

logger = logging.getLogger(__name__)


class EventHandler(FileSystemEventHandler):
    def __init__(self, paths: RootPaths, sender: UdpSender) -> None:
        self.paths = paths
        self.sender = sender
        rows, _ = collect_metadata(paths)
        self.sizes: dict[str, int] = {row["relative_path"]: row["size_bytes"] for row in rows}

    def invalidate(self, *prefixes: str) -> None:
        self.sizes = {
            path: size for path, size in self.sizes.items()
            if not any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)
        }

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in {"created", "modified", "deleted", "moved"}:
            return
        try:
            source = self.paths.observed(str(event.src_path))
            destination = (
                self.paths.observed(str(event.dest_path)) if event.event_type == "moved" else None
            )
            current_path = destination or source
            previous = self.sizes.get(source)
            metadata = None if event.is_directory or event.event_type == "deleted" else (
                file_metadata(self.paths, current_path)
            )
            size = metadata["size_bytes"] if metadata else None
            delta = size - previous if size is not None and previous is not None else None
            if event.is_directory:
                if event.event_type in {"deleted", "moved"}:
                    self.invalidate(source, *([destination] if destination else []))
            else:
                if event.event_type in {"deleted", "moved"}:
                    self.sizes.pop(source, None)
                if destination:
                    self.sizes.pop(destination, None)
                    if previous is not None:
                        self.sizes[destination] = previous
                if size is not None:
                    self.sizes[current_path] = size
                elif event.event_type == "modified":
                    self.sizes.pop(source, None)
            canonical = FileEvent(
                event_id=uuid4(), timestamp=datetime.now(UTC), event_type=event.event_type,
                relative_path=source, destination_path=destination,
                extension=None if event.is_directory else Path(current_path).suffix.lower(),
                is_directory=event.is_directory, size_bytes=size, size_delta=delta,
            )
            self.sender.send(canonical)
            logger.info("event_sent event_id=%s type=%s", canonical.event_id, canonical.event_type)
        except (UnsafePath, OSError, ValueError) as exc:
            logger.warning("event_skipped reason=%s", type(exc).__name__)


def main() -> None:
    configure_logging()
    try:
        settings = Settings()
        paths = RootPaths(settings.watch_root)
    except (ValueError, OSError) as exc:
        logger.error("configuration_invalid reason=%s", type(exc).__name__)
        raise SystemExit(1) from None
    sender = UdpSender(settings.udp_host, settings.udp_port, settings.udp_max_payload)
    observer = (
        PollingObserver(timeout=settings.poll_interval)
        if settings.observer_mode == "polling" else Observer()
    )
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    observer.schedule(EventHandler(paths, sender), str(paths.root), recursive=True)
    observer.start()
    logger.info("monitor_started observer=%s", settings.observer_mode)
    try:
        stop.wait()
    finally:
        observer.stop()
        observer.join()
        sender.close()
        logger.info("monitor_stopped")


if __name__ == "__main__":
    main()
