import socket
import threading
from unittest.mock import Mock

import pytest
from pymongo.errors import DuplicateKeyError, OperationFailure

from app.collector.processor import Processor
from app.collector.server import decode_packet, serve
from app.core.config import Settings
from app.core.paths import UnsafePath
from app.database.mongo import MongoStore
from app.monitoring.udp_client import UdpSender


@pytest.mark.parametrize("payload", [b"", b"\xff", b"{", b"[]", b"null", b"{}", b"x" * 8193])
def test_malformed_udp(paths, payload):
    with pytest.raises(ValueError):
        decode_packet(payload, paths)


def test_roundtrip_and_symlink_validation(paths, event):
    assert decode_packet(event.model_dump_json().encode(), paths) == event
    (paths.root / "demo").symlink_to(paths.root.parent, target_is_directory=True)
    with pytest.raises(UnsafePath):
        decode_packet(event.model_dump_json().encode(), paths)


def test_real_udp_malformed_then_valid(paths, event):
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    processor = Mock()
    processed = threading.Event()
    processor.process.side_effect = lambda _: processed.set()
    stop = threading.Event()
    thread = threading.Thread(target=serve, args=(receiver, paths, processor, stop))
    thread.start()
    sender = UdpSender("127.0.0.1", receiver.getsockname()[1])
    try:
        sender.socket.sendto(b"bad JSON", sender.address)
        sender.send(event)
        assert processed.wait(3)
        assert thread.is_alive()
        processor.process.assert_called_once_with(event)
        sender.max_payload = 10
        with pytest.raises(ValueError):
            sender.send(event)
    finally:
        stop.set()
        thread.join(3)
        sender.close()
        receiver.close()


@pytest.mark.parametrize("failed", [0, 1])
def test_independent_writes_continue(event, failed):
    stores = [Mock(), Mock()]
    stores[failed].insert.side_effect = RuntimeError("do not log this secret")
    processor = Processor(*stores)
    result = processor.process(event)
    assert list(result.values()) == [failed != 0, failed != 1]
    for store in stores:
        store.insert.assert_called_once_with(event)
    stores[failed].insert.side_effect = None
    assert all(processor.process(event).values())


def test_mongo_only_matching_id_duplicate_succeeds(event):
    store = MongoStore.__new__(MongoStore)
    store.collection = Mock()
    details = {"keyPattern": {"_id": 1}, "keyValue": {"_id": str(event.event_id)}}
    store.collection.insert_one.side_effect = DuplicateKeyError("duplicate", details=details)
    store.insert(event)
    document = store.collection.insert_one.call_args.args[0]
    assert document["_id"] == str(event.event_id)
    assert document["metadata"]["size_bytes"] == 100
    assert document["timestamp"] == event.timestamp
    for error in [
        OperationFailure("not a duplicate"),
        DuplicateKeyError("other index", details={"keyPattern": {"extension": 1}}),
        DuplicateKeyError("other ID", details={
            "keyPattern": {"_id": 1}, "keyValue": {"_id": "another"},
        }),
    ]:
        store.collection.insert_one.side_effect = error
        with pytest.raises(type(error)):
            store.insert(event)


def test_configuration_requires_secrets():
    with pytest.raises(ValueError):
        Settings(_env_file=None, postgres_password=None, mongo_uri=None).require_databases()
