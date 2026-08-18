import pytest

from kvstore.store import KVStore


@pytest.fixture
def store(tmp_path):
    s = KVStore(tmp_path / "db.json")
    s.open()
    yield
    s.close()
