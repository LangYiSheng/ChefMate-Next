"""Local dataset failure diagnostics replace retired online API tests."""
import pytest
from data_cleaning.services.usda import USDAService, USDARequestError


def test_missing_database_does_not_create_file(tmp_path):
    path = tmp_path / "absent.db"
    service = USDAService(path=path)
    assert service.status()["ready"] is False
    with pytest.raises(USDARequestError):
        service.search("water")
    assert not path.exists()


def test_corrupt_database_is_reported(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not sqlite")
    assert USDAService(path=path).status()["ready"] is False


def test_lookup_never_calls_network(local_catalog, monkeypatch):
    import requests
    local, _, _ = local_catalog
    def forbidden(*args, **kwargs):
        pytest.fail("USDA lookup must not access network")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    client = USDAService(path=local.path)
    assert client.search("potato raw")[0]["dataType"] == "Foundation"
    assert client.food("123")["fdcId"] == 123
    assert client.provenance("123")["version"] == "test"
