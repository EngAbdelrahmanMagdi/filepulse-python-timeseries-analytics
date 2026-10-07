from pymongo import ASCENDING, MongoClient
from pymongo.errors import DuplicateKeyError

from app.core.config import Settings
from app.core.models import FileEvent


class MongoStore:
    def __init__(self, settings: Settings) -> None:
        settings.require_databases()
        timeout = settings.db_timeout_seconds * 1000
        self.client = MongoClient(
            settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=timeout,
            connectTimeoutMS=timeout, socketTimeoutMS=timeout, timeoutMS=timeout,
            tz_aware=True,
        )
        self.collection = self.client[settings.mongo_database].raw_file_events

    def initialize(self) -> None:
        self.collection.create_index([("timestamp", ASCENDING)])

    def insert(self, event: FileEvent) -> None:
        document = event.model_dump(mode="json")
        document["_id"] = str(event.event_id)
        document["timestamp"] = event.timestamp
        document["metadata"] = {
            name: document.pop(name) for name in ("extension", "is_directory", "size_bytes")
        }
        try:
            self.collection.insert_one(document)
        except DuplicateKeyError as exc:
            details = exc.details or {}
            if (
                details.get("keyPattern") != {"_id": 1}
                or details.get("keyValue") != {"_id": str(event.event_id)}
            ):
                raise

    def close(self) -> None:
        self.client.close()
