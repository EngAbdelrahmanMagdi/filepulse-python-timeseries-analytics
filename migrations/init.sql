CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS file_events (
    timestamp TIMESTAMPTZ NOT NULL,
    event_id UUID NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('created', 'modified', 'deleted', 'moved')),
    relative_path TEXT NOT NULL,
    destination_path TEXT,
    extension TEXT,
    is_directory BOOLEAN NOT NULL,
    size_bytes BIGINT CHECK (size_bytes >= 0),
    size_delta BIGINT,
    PRIMARY KEY (timestamp, event_id),
    CHECK ((event_type = 'moved') = (destination_path IS NOT NULL)),
    CHECK (NOT is_directory OR (size_bytes IS NULL AND size_delta IS NULL))
);

-- Disable Timescale's automatic timestamp index: the PK already starts with timestamp.
SELECT create_hypertable('file_events', 'timestamp',
    if_not_exists => TRUE, create_default_indexes => FALSE);
CREATE INDEX IF NOT EXISTS file_events_type_time_idx ON file_events (event_type, timestamp);
