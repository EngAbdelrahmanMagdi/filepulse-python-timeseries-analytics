from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.models import FileEvent
from app.core.paths import RootPaths


@pytest.fixture
def paths(tmp_path):
    return RootPaths(tmp_path)


@pytest.fixture
def event():
    return FileEvent(
        event_id=uuid4(), timestamp=datetime.now(UTC), event_type="modified",
        relative_path="demo/test.csv", is_directory=False, extension=".csv",
        size_bytes=100, size_delta=20,
    )
