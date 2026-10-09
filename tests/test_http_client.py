import pytest
import requests

from taste.http_client import ApiError, GaveUpError, HttpClient
from tests.conftest import FAKE_LASTFM_KEY, FakeResponse, FakeTransport


def sequence(*responses):
    items = list(responses)

    def handler(url, params):
        return items.pop(0)

    return handler


def test_success_first_try(make_client, sleeper):
    client, transport = make_client(sequence(FakeResponse(200, {"ok": True})))
    assert client.get_json("https://example.test").data == {"ok": True}
    assert len(transport.calls) == 1
    assert sleeper.delays == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retries_retryable_http_status_with_backoff(make_client, sleeper, status):
    client, transport = make_client(
        sequence(FakeResponse(status, {}), FakeResponse(status, {}), FakeResponse(200, {"ok": 1}))
    )
    assert client.get_json("https://example.test").data == {"ok": 1}
    assert len(transport.calls) == 3
    backoff = [d for d in sleeper.delays if d >= 1]
    assert backoff == [2.0, 4.0]


def test_retry_after_header_wins_when_longer(make_client, sleeper):
    client, _ = make_client(
        sequence(FakeResponse(429, {}, headers={"Retry-After": "10"}), FakeResponse(200, {}))
    )
    client.get_json("https://example.test")
    assert 10.0 in sleeper.delays


def test_gives_up_after_max_retries(make_client, sleeper):
    client, transport = make_client(lambda url, params: FakeResponse(503, {}))
    with pytest.raises(GaveUpError) as info:
        client.get_json("https://example.test")
    assert len(transport.calls) == 5  # first try + 4 retries
    assert [d for d in sleeper.delays if d >= 1] == [2.0, 4.0, 8.0, 16.0]
    assert "gave up after 4 retries" in str(info.value)


def test_non_retryable_status_fails_immediately(make_client, sleeper):
    client, transport = make_client(lambda url, params: FakeResponse(404, {"error": "nope"}))
    with pytest.raises(ApiError) as info:
        client.get_json("https://example.test")
    assert not info.value.retryable
    assert len(transport.calls) == 1


def test_network_errors_are_retried_and_redacted(make_client):
    url = f"https://ws.example.test/?api_key={FAKE_LASTFM_KEY}"
    error = requests.ConnectionError(f"Max retries exceeded with url: {url}")
    client, transport = make_client(lambda u, p: error)
    with pytest.raises(GaveUpError) as info:
        client.get_json(url)
    assert FAKE_LASTFM_KEY not in str(info.value)
    assert "ConnectionError" in str(info.value)


def test_non_json_body_is_retried(make_client):
    client, transport = make_client(
        sequence(FakeResponse(200, "<html>oops</html>"), FakeResponse(200, {"ok": 1}))
    )
    assert client.get_json("https://example.test").data == {"ok": 1}
    assert len(transport.calls) == 2


def test_redact_strips_key_values_and_query_params():
    client = HttpClient(FakeTransport(lambda u, p: None), secrets=["s3cret-value"])
    text = "failed: https://x.test/?api_key=abc123&user=me plus s3cret-value"
    redacted = client.redact(text)
    assert "abc123" not in redacted
    assert "s3cret-value" not in redacted
    assert "user=me" in redacted


def test_throttle_spaces_requests(sleeper):
    now = [100.0]
    client = HttpClient(
        FakeTransport(lambda u, p: FakeResponse(200, {})),
        sleep=sleeper,
        clock=lambda: now[0],
        min_interval=0.3,
    )
    client.get_json("https://example.test")
    now[0] += 0.1
    client.get_json("https://example.test")
    assert sleeper.delays == [pytest.approx(0.2)]
