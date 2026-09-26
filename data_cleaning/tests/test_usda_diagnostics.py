import pytest
import requests

from data_cleaning.services.usda import USDARequestError, USDAService


def test_http_failure_keeps_sanitized_body_and_does_not_retry_400():
    records = []

    class Repo:
        def service_call(self, *args, **kwargs):
            records.append(kwargs)

    class Session:
        def get(self, *args, **kwargs):
            response = requests.Response()
            response.status_code = 400
            response._content = b'Bad parameter api_key=private-test-key&access_token=another-secret'
            return response

    service = USDAService(Repo(), session=Session())
    service.settings.update(usda_key="private-test-key", retries=2)
    with pytest.raises(USDARequestError) as caught:
        service.search("mud crab raw")
    assert len(records) == 1
    diagnostic = caught.value.details
    assert diagnostic["http_status"] == 400
    assert "Bad parameter" in diagnostic["response_excerpt"]
    assert "private-test-key" not in str(diagnostic)
    assert "another-secret" not in str(diagnostic)


def test_timeout_has_no_stale_response_body():
    class Repo:
        def service_call(self, *args, **kwargs):
            pass

    class Session:
        def get(self, *args, **kwargs):
            raise requests.Timeout("sensitive URL must not be retained")

    service = USDAService(Repo(), session=Session())
    service.settings.update(usda_key="test", retries=0)
    with pytest.raises(USDARequestError) as caught:
        service.search("water")
    assert caught.value.details["response_excerpt"] == ""
    assert "sensitive" not in str(caught.value)


def test_nginx_400_retries_then_succeeds():
    class Repo:
        def service_call(self, *args, **kwargs):
            pass
    class Session:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            response = requests.Response()
            response.status_code = 400 if self.calls == 1 else 200
            response._content = b'<html>400 Bad Request nginx</html>' if self.calls == 1 else b'{"foods": []}'
            return response
    session = Session()
    service = USDAService(Repo(), session=session, sleeper=lambda _: None)
    service.settings.update(usda_key="test", retries=2)
    assert service.search("mud crab raw") == []
    assert session.calls == 2
