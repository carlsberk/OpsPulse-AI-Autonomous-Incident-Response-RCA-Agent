"""A regression test the model writes to reproduce the incident - evidence for the reviewer, never trusted code.

The model may propose one new pytest file that raises the incident's exception when run against the code *as it is*.
Run in the sandbox it shows two things the quality gate cannot: that the crash can be reproduced from the code the model
read, and that the proposed patch makes that same reproduction pass. It is a stronger signal than "the diff applies",
and still not proof: the model wrote both the test and the fix, so the reviewer reads the test like any other claim.

Nothing here executes the test. It is validated as text (a new file, a test function, no process/network/file tools) and
handed to the sandbox, which runs it with no network, a read-only root and resource caps.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

MAX_CONTENT_CHARS = 6000
MAX_PATH_DEPTH = 4
FORBIDDEN_IMPORTS = frozenset(
    {"subprocess", "socket", "ctypes", "multiprocessing", "shutil", "urllib", "http", "requests", "httpx", "ftplib"}
)
FORBIDDEN_CALLS = frozenset({"exec", "eval", "compile", "__import__", "open", "input"})
FORBIDDEN_ATTRIBUTES = frozenset({"system", "popen", "remove", "unlink", "rmdir", "rmtree", "kill", "fork"})
_PATH = re.compile(r"[A-Za-z0-9_./-]+\.py")
_ERROR_NAME = re.compile(r"^\s*([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning|Timeout|Refused|Failure))\b")


@dataclass(frozen=True)
class ReproductionSpec:
    path: str
    content: str
    expected_error: str | None  # short class name of the incident's exception, e.g. "TypeError"


def expected_error_name(error_message: str) -> str | None:
    """Short exception class name from the incident's first line ("TypeError: x" -> "TypeError",
    "httpx.PoolTimeout" -> "PoolTimeout"), or None when the message does not start with one."""
    first = (error_message or "").strip().splitlines()[0] if (error_message or "").strip() else ""
    match = _ERROR_NAME.match(first)
    return match.group(1).split(".")[-1] if match else None


def validate_path(path: str) -> str | None:
    """Why `path` cannot be used for a new test file, or None."""
    if not _PATH.fullmatch(path or ""):
        return "the test path must be a relative .py path of letters, digits, '_', '-', '.' and '/'"
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or "" in parts or len(parts) > MAX_PATH_DEPTH:
        return "the test path must be relative, without '..', at most four levels deep"
    name = parts[-1]
    if not (name.startswith("test_") or name.endswith("_test.py")):
        return "the test file name must start with test_ or end with _test.py"
    return None


def validate_content(content: str) -> str | None:
    """Why the test text must not be run, or None. Static only; the sandbox is the real boundary."""
    if not content or len(content) > MAX_CONTENT_CHARS:
        return f"the test must be between 1 and {MAX_CONTENT_CHARS} characters"
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError) as exc:
        return f"the test is not valid Python ({type(exc).__name__})"
    has_test = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
            has_test = True
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            roots = {(node.module or "").split(".")[0]}
        else:
            roots = set()
        if roots & FORBIDDEN_IMPORTS:
            return f"the test imports {sorted(roots & FORBIDDEN_IMPORTS)[0]}, which a reproduction does not need"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                return f"the test calls {func.id}()"
            if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_ATTRIBUTES:
                return f"the test calls .{func.attr}()"
    if not has_test:
        return "the test defines no test function"
    return None


def validate(spec: ReproductionSpec) -> str | None:
    return validate_path(spec.path) or validate_content(spec.content)
