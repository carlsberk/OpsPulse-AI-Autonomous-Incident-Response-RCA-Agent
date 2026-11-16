"""The Docker image installs requirements.lock; it must stay consistent with the ranges in requirements.txt."""

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def locked() -> dict[str, tuple[Version, int]]:
    """name -> (version, number of hashes) for every pinned package in the lock file."""
    pins: dict[str, tuple[Version, int]] = {}
    text = (ROOT / "requirements.lock").read_text()
    for block in re.split(r"\n(?=\S)", text):
        head = re.match(r"([A-Za-z0-9_.\-\[\]]+)==([^\s\\]+)", block)
        if head:
            pins[canonicalize_name(head.group(1).split("[")[0])] = (
                Version(head.group(2)),
                len(re.findall(r"--hash=sha256:[0-9a-f]{64}", block)),
            )
    return pins


def declared() -> list[Requirement]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return [Requirement(line.split("#")[0].strip()) for line in lines if line.split("#")[0].strip()]


def test_every_locked_package_has_hashes():
    pins = locked()
    assert len(pins) > 30  # direct dependencies and their transitive ones
    assert [name for name, (_, hashes) in pins.items() if hashes == 0] == []


def test_the_lock_satisfies_every_declared_range():
    pins = locked()
    for req in declared():
        name = canonicalize_name(req.name)
        assert name in pins, f"{req.name} is declared but not locked (run scripts/update_lock.sh)"
        assert req.specifier.contains(pins[name][0], prereleases=True), (
            f"{req.name}=={pins[name][0]} is outside {req.specifier} (run scripts/update_lock.sh)"
        )


def test_the_dockerfile_installs_the_lock_with_hash_checking():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "--require-hashes" in dockerfile and "requirements.lock" in dockerfile
