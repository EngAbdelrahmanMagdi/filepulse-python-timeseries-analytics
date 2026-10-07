import argparse
import json
import logging
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from app.core.config import Settings
from app.core.filesystem import collect_metadata
from app.core.logging import configure_logging
from app.core.paths import RootPaths

DTYPES = {
    "relative_path": "string", "filename": "string", "extension": "string",
    "size_bytes": "int64", "directory_depth": "int64", "is_empty": "bool",
}
COLUMNS = [*DTYPES, "modified_at", "created_at"]


def scan(paths: RootPaths) -> tuple[pd.DataFrame, int]:
    rows, skipped = collect_metadata(paths)
    frame = pd.DataFrame.from_records(rows, columns=COLUMNS).astype(DTYPES)
    for name in ("modified_at", "created_at"):
        frame[name] = pd.to_datetime(frame[name], utc=True)
    return frame, skipped


def records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def analyze(frame: pd.DataFrame, limit: int = 10) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    sizes = frame["size_bytes"]
    extensions = frame.groupby("extension", dropna=False).agg(
        files=("relative_path", "count"), bytes=("size_bytes", "sum")
    ).reset_index().sort_values(["bytes", "extension"], ascending=[False, True])
    depth = frame["directory_depth"].value_counts().sort_index()
    bins = [-1, 0, 1024, 1024**2, 10 * 1024**2, 100 * 1024**2, float("inf")]
    labels = ["empty", "1 B–1 KiB", "1 KiB–1 MiB", "1–10 MiB", "10–100 MiB", ">100 MiB"]
    distribution = pd.cut(sizes, bins=bins, labels=labels).value_counts(sort=False)
    return {
        "total_files": len(frame), "total_bytes": int(sizes.sum()),
        "average_size": float(sizes.mean()) if len(frame) else 0.0,
        "median_size": float(sizes.median()) if len(frame) else 0.0,
        "p95_size": float(sizes.quantile(0.95)) if len(frame) else 0.0,
        "empty_count": int(frame["is_empty"].sum()),
        "extensions": records(extensions),
        "depth_distribution": {str(key): int(value) for key, value in depth.items()},
        "size_distribution": {str(key): int(value) for key, value in distribution.items()},
        "largest_files": records(frame.sort_values(
            ["size_bytes", "relative_path"], ascending=[False, True]
        ).head(limit)),
        "recent_files": records(frame.sort_values(
            ["modified_at", "relative_path"], ascending=[False, True]
        ).head(limit)),
        "oldest_files": records(frame.sort_values(["modified_at", "relative_path"]).head(limit)),
        "empty_files": records(frame.loc[frame["is_empty"]].head(limit)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan WATCH_ROOT metadata; print JSON analytics")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    configure_logging()
    try:
        paths = RootPaths(Settings().watch_root)
        frame, skipped = scan(paths)
    except (ValueError, OSError) as exc:
        logging.getLogger(__name__).error("scan_failed reason=%s", type(exc).__name__)
        raise SystemExit(1) from None
    result = analyze(frame, args.limit)
    result.update(scanned_at=datetime.now(UTC).isoformat(), skipped_entries=skipped)
    logging.getLogger(__name__).info("scan_complete files=%s skipped=%s", len(frame), skipped)
    print(json.dumps(result, allow_nan=False, indent=2))


if __name__ == "__main__":
    main()
