import logging
import signal
import socket
import threading

from app.collector.processor import Processor
from app.core.config import Settings
from app.core.logging import configure_logging
from app.core.models import FileEvent
from app.core.paths import RootPaths
from app.database.mongo import MongoStore
from app.database.postgres import PostgresStore

logger = logging.getLogger(__name__)


def decode_packet(payload: bytes, paths: RootPaths, max_payload: int = 8192) -> FileEvent:
    if not payload or len(payload) > max_payload:
        raise ValueError("invalid packet size")
    event = FileEvent.model_validate_json(payload.decode("utf-8"))
    paths.relative(event.relative_path)
    if event.destination_path is not None:
        paths.relative(event.destination_path)
    return event


def serve(
    receiver: socket.socket, paths: RootPaths, processor: Processor,
    stop: threading.Event, max_payload: int = 8192,
) -> None:
    receiver.settimeout(0.5)
    while not stop.is_set():
        try:
            payload, _ = receiver.recvfrom(max_payload + 1)
        except TimeoutError:
            continue
        try:
            event = decode_packet(payload, paths, max_payload)
        except (ValueError, OSError) as exc:
            logger.warning("packet_rejected reason=%s", type(exc).__name__)
            continue
        processor.process(event)


def main() -> None:
    configure_logging()
    # Driver background diagnostics can contain connection context; emit only our safe logs.
    logging.getLogger("psycopg.pool").disabled = True
    try:
        settings = Settings()
        settings.require_databases()
        paths = RootPaths(settings.watch_root)
    except (ValueError, OSError) as exc:
        logger.error("configuration_invalid reason=%s", type(exc).__name__)
        raise SystemExit(1) from None
    postgres = PostgresStore(settings)
    mongo = MongoStore(settings)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        try:
            mongo.initialize()
            logger.info("mongo_initialized")
        except Exception as exc:
            logger.error("mongo_initialization_failed reason=%s", type(exc).__name__)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind((settings.udp_host, settings.udp_port))
            logger.info("collector_started port=%s", settings.udp_port)
            serve(receiver, paths, Processor(postgres, mongo), stop, settings.udp_max_payload)
    finally:
        postgres.close()
        mongo.close()
        logger.info("collector_stopped")


if __name__ == "__main__":
    main()
