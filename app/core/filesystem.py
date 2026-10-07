import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.paths import RootPaths, UnsafePath


def file_metadata(paths: RootPaths, relative: str) -> dict[str, Any] | None:
    try:
        local = paths.local(relative)
        info = local.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        # Recheck containment after metadata lookup to handle common rename races.
        paths.observed(local)
        birth = getattr(info, "st_birthtime", None)
        return {
            "relative_path": relative,
            "filename": local.name,
            "extension": local.suffix.lower(),
            "size_bytes": info.st_size,
            "modified_at": datetime.fromtimestamp(info.st_mtime, UTC),
            "created_at": datetime.fromtimestamp(birth, UTC) if birth is not None else None,
            "directory_depth": len(Path(relative).parts) - 1,
            "is_empty": info.st_size == 0,
        }
    except (OSError, ValueError):
        return None


def collect_metadata(paths: RootPaths) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0

    def on_error(_: OSError) -> None:
        nonlocal skipped
        skipped += 1

    for directory, dirs, files in os.walk(paths.root, followlinks=False, onerror=on_error):
        safe_dirs = []
        for name in dirs:
            try:
                paths.observed(Path(directory) / name)
                safe_dirs.append(name)
            except (UnsafePath, OSError):
                skipped += 1
        dirs[:] = safe_dirs
        for name in files:
            try:
                relative = paths.observed(Path(directory) / name)
                row = file_metadata(paths, relative)
            except (UnsafePath, OSError):
                row = None
            if row is None:
                skipped += 1
            else:
                rows.append(row)
    return rows, skipped
