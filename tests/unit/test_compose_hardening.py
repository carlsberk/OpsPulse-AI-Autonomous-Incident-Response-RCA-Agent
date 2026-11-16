"""The Compose stack's application containers run unprivileged and read-only; a refactor must not quietly undo that."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


@pytest.fixture(scope="module")
def stack():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("service", ["api", "worker"])
def test_application_containers_are_read_only_unprivileged_and_have_only_tmp_writable(stack, service):
    spec = stack["services"][service]
    assert spec["read_only"] is True and spec["tmpfs"] == ["/tmp"]
    assert spec["cap_drop"] == ["ALL"] and "no-new-privileges:true" in spec["security_opt"]
    assert "privileged" not in spec and "cap_add" not in spec


def test_the_worker_liveness_file_lives_on_the_writable_tmpfs(stack):
    # With a read-only root the healthcheck's file must be under the tmpfs, or the worker would never be "alive".
    assert stack["services"]["worker"]["environment"]["WORKER_LIVENESS_FILE"].startswith("/tmp/")
