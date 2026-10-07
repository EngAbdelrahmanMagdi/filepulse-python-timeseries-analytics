# FilePulse

> Metadata-only filesystem monitoring and analytics using Python, Watchdog, UDP,
> TimescaleDB, MongoDB, and Pandas.

Phase 1 delivers the data pipeline and static analysis. API, dashboard, and anomaly
analysis are deliberately deferred to later phases.

## Quick start

Requirements: Docker Desktop/Engine and Docker Compose. No host Python is required.

1. Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell).
2. Set nonempty `POSTGRES_PASSWORD` and `MONGO_PASSWORD` in `.env`. Use random
   hexadecimal passwords for this local demo; the MongoDB URI interpolates the password.
3. Start the stack:

```sh
docker compose up --build -d
docker compose ps
```

The four services are `timescaledb`, `mongodb`, `collector`, and `monitor`.
No host ports are published. The Python containers run as a non-root user with
read-only filesystems and a temporary `/tmp`. Database data lives in named volumes.
The initial SQL runs on a fresh PostgreSQL volume; restarting does not reapply it.

## Generate activity

Create `watched_data/demo/` first. The generator has **only that directory writable**,
no database credentials, and no network. It never modifies files outside its own
new demo run directory. The monitor sees the full watched directory read-only.

PowerShell:

```powershell
New-Item -ItemType Directory -Force watched_data/demo | Out-Null
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --mount "type=bind,source=$($PWD.Path)/watched_data/demo,target=/app/watched_data/demo" -e WATCH_ROOT=/app/watched_data filepulse:phase1 python -m scripts.generate_activity --profile normal
```

POSIX shell:

```sh
mkdir -p watched_data/demo
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --mount "type=bind,source=$(pwd)/watched_data/demo,target=/app/watched_data/demo" -e WATCH_ROOT=/app/watched_data filepulse:phase1 python -m scripts.generate_activity --profile normal
```

Replace `normal` with `burst` for 100 files instead of 6. Each run creates, appends,
renames, and deletes files in stages, waiting 2.5 seconds between stages. Set
`--interval` to at least twice `POLL_INTERVAL` if increasing the polling interval;
pass the same `POLL_INTERVAL` into the generator container.

## Static analysis

This is a one-off command using the monitor image and its read-only mount:

```sh
docker compose run --rm --no-deps monitor python -m app.analytics.static_analysis --limit 10
```

The JSON output contains file counts, bytes, average/median/p95 sizes, extension
counts and storage, size/depth distributions, largest/recent/oldest files, and empty
files. Top tables are limited to 1–100 rows. A scan reads metadata only, skips
symlinks and inaccessible/raced entries, and reports the skipped count. Empty
scans are valid. Root-level depth is zero. "Oldest" means oldest modification time;
creation time is included only when the platform exposes reliable birth time.
The tracked `.gitkeep` is an ordinary empty file and is counted in scans.

## Verification

```sh
docker compose run --rm --no-deps monitor ruff check --no-cache .
docker compose run --rm --no-deps monitor pytest -m "not integration" -p no:cacheprovider
docker compose run --rm collector pytest -m integration -p no:cacheprovider
```

Integration tests require the running stack. They verify real inserts, idempotent
replays, hypertable/index structure, a bounded `time_bucket()` query, and malformed
UDP followed by a valid event through the running collector. See
[Phase 1 handoff](docs/phase-1-handoff.md) for actual results and database-outage checks.

## Design decisions and limitations

- Watchdog's one-second polling observer is the default for Docker bind mounts.
  `OBSERVER_MODE=native` enables OS notifications where supported. Polling can
  miss sufficiently short-lived states and merge rapid modifications.
- JSON/UDP is a small demonstration of datagram transport. It has no ordering,
  acknowledgements, authentication, delivery guarantees, or event retries.
- TimescaleDB is the normalized analytical store; MongoDB retains raw validated
  documents. Writes are independent best-effort operations. Outage events are
  never replayed or recovered, so the stores can differ.
- Replays preserve their original timestamp. The SQL composite key and MongoDB
  `_id` are sufficient; no extra deduplication mechanism exists.
- Size deltas are measured only against known in-memory sizes. Deleted sizes and
  unknown previous sizes yield null deltas. Directory moves invalidate cache
  prefixes rather than rebuilding every descendant immediately.
- One trusted local directory is monitored. No file contents are read by monitoring
  or analytics. Demo generation writes synthetic contents but does not inspect them.
- This local project has no authentication or distributed monitoring. The isolated
  Compose network is not protection against a hostile container on the same network.

See [architecture](docs/architecture.md) for the event contract and boundaries.
Stop services with `docker compose down`; named volumes are retained.
