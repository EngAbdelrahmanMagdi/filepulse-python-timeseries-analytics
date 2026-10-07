import logging

from app.core.models import FileEvent
from app.database.mongo import MongoStore
from app.database.postgres import PostgresStore

logger = logging.getLogger(__name__)


class Processor:
    def __init__(self, postgres: PostgresStore, mongo: MongoStore) -> None:
        self.stores = {"timescaledb": postgres, "mongodb": mongo}

    def process(self, event: FileEvent) -> dict[str, bool]:
        outcomes = {}
        for name, store in self.stores.items():
            try:
                store.insert(event)
                outcomes[name] = True
                logger.info("persisted store=%s event_id=%s", name, event.event_id)
            except Exception as exc:
                outcomes[name] = False
                logger.error(
                    "persistence_failed store=%s event_id=%s reason=%s",
                    name, event.event_id, type(exc).__name__,
                )
        return outcomes
