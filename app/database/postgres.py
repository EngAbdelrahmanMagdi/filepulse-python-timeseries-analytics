from psycopg_pool import ConnectionPool

from app.core.config import Settings
from app.core.models import FileEvent

INSERT_EVENT = """
INSERT INTO file_events
(timestamp, event_id, event_type, relative_path, destination_path,
 extension, is_directory, size_bytes, size_delta)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


class PostgresStore:
    def __init__(self, settings: Settings) -> None:
        settings.require_databases()
        self.pool = ConnectionPool(
            kwargs={
                "host": settings.postgres_host, "port": settings.postgres_port,
                "dbname": settings.postgres_db, "user": settings.postgres_user,
                "password": settings.postgres_password.get_secret_value(),
                "connect_timeout": settings.db_timeout_seconds,
                "options": f"-c statement_timeout={settings.db_timeout_seconds * 1000}",
            },
            min_size=0, max_size=2, timeout=settings.db_timeout_seconds,
            reconnect_timeout=settings.db_timeout_seconds, open=True,
        )

    def insert(self, event: FileEvent) -> None:
        with self.pool.connection() as connection:
            connection.execute(INSERT_EVENT, (
                event.timestamp, event.event_id, event.event_type, event.relative_path,
                event.destination_path, event.extension, event.is_directory,
                event.size_bytes, event.size_delta,
            ))

    def close(self) -> None:
        self.pool.close()
