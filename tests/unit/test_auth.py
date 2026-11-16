"""Identity configuration, role checks and rate limiting."""

import hashlib

import pytest

from src.api.auth import SlidingWindowLimiter
from src.config import ConfigError, _identities
from tests.unit.conftest import REPORTER_HEADERS
from tests.unit.factories import incident_payload, make_analysis

HASH = hashlib.sha256(b"k").hexdigest()


@pytest.mark.parametrize(
    "value",
    [
        "alice:reviewer",
        f"alice:root:{HASH}",
        "alice:reviewer:notahash",
        f":reviewer:{HASH}",
        f"a:reviewer:{HASH},a:admin:{HASH}",
    ],
)
def test_invalid_api_keys_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv("API_KEYS", value)
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(ConfigError):
        _identities()


def test_configuration_stores_digests_not_keys(monkeypatch):
    monkeypatch.setenv("API_KEYS", f"ci:reporter:{HASH}")
    monkeypatch.setenv("API_KEY", "legacy-secret")
    ids = _identities()
    assert [(i.name, i.role) for i in ids] == [("ci", "reporter"), ("default", "admin")]
    assert all("legacy-secret" not in repr(i) for i in ids)


def test_reporter_can_submit_and_read(client, fake_llm, source_fetch):
    fake_llm([make_analysis()])
    resp = client.post("/webhook/incident", json=incident_payload(), headers=REPORTER_HEADERS)
    assert resp.status_code == 202
    body = client.get(f"/incidents/{resp.json()['incident_id']}", headers=REPORTER_HEADERS).json()
    assert body["submitted_by"] == "alice"


def test_rate_limit_returns_429_with_retry_after(client, fake_llm, source_fetch, set_settings):
    set_settings(rate_limit_per_minute=2)
    fake_llm([make_analysis()] * 3)
    responses = [client.post("/webhook/incident", json=incident_payload(), headers=REPORTER_HEADERS) for _ in range(3)]
    assert [r.status_code for r in responses] == [202, 202, 429]
    assert responses[2].json()["error"]["code"] == "rate_limited"
    assert int(responses[2].headers["Retry-After"]) >= 1


def test_sliding_window_frees_slots_after_a_minute():
    limiter = SlidingWindowLimiter()
    assert limiter.check("a", 1, now=0.0) is None
    assert limiter.check("a", 1, now=30.0) == pytest.approx(30.0)
    assert limiter.check("b", 1, now=30.0) is None  # limits are per identity
    assert limiter.check("a", 1, now=60.0) is None
    assert limiter.check("a", 0, now=60.0) is None  # 0 disables


def test_generated_api_keys_carry_256_random_bits_which_is_why_a_fast_hash_is_enough(capsys):
    """`authenticate` stores SHA-256 digests of keys. That is only sound for high-entropy keys, so pin the generator."""
    import base64
    import hashlib

    from scripts import make_api_key

    keys = []
    for _ in range(3):
        make_api_key.main(["alice", "reviewer"])
        out = capsys.readouterr().out
        key = out.split("\n  ")[1].split("\n")[0]
        entry = out.rsplit("\n  ", 1)[1].strip()
        assert entry.endswith(hashlib.sha256(key.encode()).hexdigest())
        assert len(base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))) >= 32  # 256 bits
        keys.append(key)
    assert len(set(keys)) == 3
