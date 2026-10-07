import os
from unittest.mock import Mock

import pytest
from watchdog.events import (
    DirMovedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from app.analytics.static_analysis import analyze, scan
from app.core.filesystem import file_metadata
from app.monitoring.watcher import EventHandler
from scripts.generate_activity import generate


def test_metadata_and_missing_file(paths):
    target = paths.root / "report.csv"
    target.write_bytes(b"12345")
    metadata = file_metadata(paths, "report.csv")
    assert metadata["size_bytes"] == 5
    assert metadata["directory_depth"] == 0
    assert metadata["modified_at"].tzinfo is not None
    target.unlink()
    assert file_metadata(paths, "report.csv") is None


def test_cache_modification_move_delete(paths):
    source = paths.root / "a.txt"
    source.write_bytes(b"123")
    sender = Mock()
    handler = EventHandler(paths, sender)
    source.write_bytes(b"12345")
    handler.on_any_event(FileModifiedEvent(str(source)))
    assert sender.send.call_args.args[0].size_delta == 2
    dest = paths.root / "b.txt"
    source.rename(dest)
    handler.on_any_event(FileMovedEvent(str(source), str(dest)))
    moved = sender.send.call_args.args[0]
    assert moved.destination_path == "b.txt"
    assert moved.size_delta == 0
    assert handler.sizes == {"b.txt": 5}
    dest.unlink()
    handler.on_any_event(FileDeletedEvent(str(dest)))
    deleted = sender.send.call_args.args[0]
    assert deleted.size_bytes is None and deleted.size_delta is None
    assert handler.sizes == {}


def test_unknown_previous_size_and_disappearing_metadata(paths):
    sender = Mock()
    handler = EventHandler(paths, sender)
    path = paths.root / "new.txt"
    path.write_bytes(b"new")
    handler.on_any_event(FileCreatedEvent(str(path)))
    assert sender.send.call_args.args[0].size_delta is None
    path.unlink()
    handler.on_any_event(FileModifiedEvent(str(path)))
    assert sender.send.call_args.args[0].size_bytes is None
    assert "new.txt" not in handler.sizes


def test_file_move_transfers_size_when_metadata_missing(paths):
    source = paths.root / "a"
    source.write_bytes(b"12")
    sender = Mock()
    handler = EventHandler(paths, sender)
    source.unlink()
    handler.on_any_event(FileMovedEvent(str(source), str(paths.root / "b")))
    assert handler.sizes == {"b": 2}
    assert sender.send.call_args.args[0].size_delta is None


def test_directory_move_invalidates_both_prefixes(paths):
    sender = Mock()
    handler = EventHandler(paths, sender)
    handler.sizes = {"old/a": 1, "new/b": 2, "oldish/c": 3, "keep": 4}
    handler.on_any_event(DirMovedEvent(str(paths.root / "old"), str(paths.root / "new")))
    assert handler.sizes == {"oldish/c": 3, "keep": 4}
    assert sender.send.call_args.args[0].is_directory


def test_watcher_rejects_outside_path(paths):
    sender = Mock()
    EventHandler(paths, sender).on_any_event(FileCreatedEvent(str(paths.root.parent / "outside")))
    sender.send.assert_not_called()


def test_static_fixture(paths):
    (paths.root / "a.txt").write_bytes(b"1234")
    (paths.root / "b.txt").touch()
    (paths.root / "nested").mkdir()
    (paths.root / "nested" / "c.csv").write_bytes(b"123456")
    os.utime(paths.root / "a.txt", (100, 100))
    frame, skipped = scan(paths)
    result = analyze(frame)
    assert skipped == 0
    assert result["total_files"] == 3
    assert result["total_bytes"] == 10
    assert result["median_size"] == 4
    assert result["p95_size"] == pytest.approx(5.8)
    assert result["empty_count"] == 1
    assert result["depth_distribution"] == {"0": 2, "1": 1}
    assert result["oldest_files"][0]["relative_path"] == "a.txt"
    assert result["largest_files"][0]["relative_path"] == "nested/c.csv"
    assert sum(result["size_distribution"].values()) == 3
    assert {row["extension"]: row["files"] for row in result["extensions"]} == {
        ".txt": 2, ".csv": 1,
    }


def test_empty_scan(paths):
    frame, skipped = scan(paths)
    result = analyze(frame)
    assert result["total_files"] == result["total_bytes"] == skipped == 0
    assert result["average_size"] == 0
    assert result["extensions"] == result["largest_files"] == []
    with pytest.raises(ValueError):
        analyze(frame, 101)


def test_scan_skips_symlinks(paths):
    (paths.root / "real").write_bytes(b"12")
    try:
        (paths.root / "link").symlink_to(paths.root / "real")
    except OSError:
        pytest.skip("symlink creation unavailable")
    frame, skipped = scan(paths)
    assert len(frame) == 1
    assert skipped == 1


def test_generator_profiles_confined_to_demo(paths, monkeypatch):
    monkeypatch.setattr("scripts.generate_activity.time.sleep", lambda _: None)
    sentinel = paths.root / "untouched"
    sentinel.write_bytes(b"original")
    normal = generate("normal", paths, 2.5)
    burst = generate("burst", paths, 2.5)
    assert normal["created"] == 6 and burst["created"] == 100
    assert sentinel.read_bytes() == b"original"
    assert all(path.is_relative_to(paths.root / "demo") for path in (
        paths.root / "demo"
    ).rglob("sample*"))
    with pytest.raises(ValueError):
        generate("normal", paths, 0.1)
