"""A body over the limit is refused with 413 even when the client streams it without a Content-Length."""

from tests.unit.conftest import API_HEADERS


def test_declared_oversize_body_is_refused(client):
    body = b"x" * 1_000_001
    response = client.post(
        "/webhook/incident", content=body, headers={**API_HEADERS, "Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers.get("X-Request-ID")


def test_chunked_oversize_body_is_refused_without_buffering_it_all(client):
    def chunks():
        for _ in range(12):
            yield b"x" * 100_000  # no Content-Length: sent chunked

    response = client.post(
        "/webhook/incident", content=chunks(), headers={**API_HEADERS, "Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_normal_requests_are_unaffected(client):
    assert client.get("/healthz").status_code == 200
