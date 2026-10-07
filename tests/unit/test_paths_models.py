import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.models import FileEvent
from app.core.paths import UnsafePath

BAD_PATHS = ["../escape", "a/../../escape", "/tmp/escape", "C:\\escape", "C:escape",
             "\\\\server\\share\\file", "//server/share/file", "", ".", "a\x00b"]


@pytest.mark.parametrize("value", BAD_PATHS)
def test_wire_and_domain_reject_unsafe_paths(paths, event, value):
    with pytest.raises(UnsafePath):
        paths.relative(value)
    data = event.model_dump(mode="json")
    data["relative_path"] = value
    with pytest.raises(ValidationError):
        FileEvent.model_validate_json(json.dumps(data))


def test_watcher_accepts_absolute_and_deleted_paths(paths):
    local = paths.root / "nested" / "gone.txt"
    assert paths.observed(local) == "nested/gone.txt"
    assert paths.relative("nested/gone.txt") == "nested/gone.txt"
    assert paths.relative("nested\\gone.txt") == "nested/gone.txt"
    with pytest.raises(UnsafePath):
        paths.observed(paths.root.parent / "outside")


def test_symlinks_rejected_even_inside_root(paths):
    target = paths.root / "target"
    target.mkdir()
    for name, destination in [("internal", target), ("external", paths.root.parent)]:
        link = paths.root / name
        try:
            link.symlink_to(destination, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation unavailable")
        with pytest.raises(UnsafePath):
            paths.relative(f"{name}/deleted.txt")
        with pytest.raises(UnsafePath):
            paths.observed(link / "deleted.txt")


@pytest.mark.parametrize("changes", [
    {"event_type": "opened"}, {"size_bytes": -1}, {"size_bytes": True},
    {"size_bytes": "100"}, {"is_directory": 1}, {"timestamp": "2026-01-01T00:00:00"},
    {"event_id": "not-a-uuid"}, {"destination_path": "dest.csv"},
    {"event_type": "moved"}, {"unknown": "value"},
    {"size_delta": 2**63}, {"is_directory": True},
])
def test_invalid_contract(event, changes):
    data = event.model_dump(mode="json") | changes
    with pytest.raises(ValidationError):
        FileEvent.model_validate_json(json.dumps(data))


def test_utc_and_move_contract(event):
    source_time = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2)))
    moved = FileEvent(
        event_id=uuid4(), timestamp=source_time, event_type="moved",
        relative_path="a\\old.csv", destination_path="a/new.csv", is_directory=False,
    )
    assert moved.timestamp == source_time.astimezone(UTC)
    assert moved.relative_path == "a/old.csv"
    assert FileEvent.model_validate_json(moved.model_dump_json()) == moved
