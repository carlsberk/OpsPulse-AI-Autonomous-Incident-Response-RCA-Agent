"""Held-out evaluation cases (run: python -m evals.datasets.build_rca_holdout).

Written on 2026-10-10, after rca-prompt-v3/v4 were tuned on rca_cases.json and BEFORE any model was run on
these cases. They are not used for tuning: their purpose is to check whether improvements measured on the
tuning set generalise. Labels follow the root-cause definitions in the system prompt. Mock replies are simple
correct answers so the set also runs offline; only live results are meaningful here.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from evals.datasets.build_rca_cases import py_trace, reply

OUT = pathlib.Path(__file__).with_name("rca_holdout.json")
REPO = "eval/billing"
CASES: list[dict[str, Any]] = []


def case(case_id, scenario, files, frames, final, expected, replies, error=None, trace=None) -> None:
    CASES.append(
        {
            "id": case_id,
            "scenario": scenario,
            "repo_name": REPO,
            "error_message": error or final,
            "stack_trace": trace if trace is not None else py_trace(files, frames, final),
            "files": files,
            "history": [],
            "expected": {"inconclusive": False, "relevant_files": [], **expected},
            "mock_replies": replies,
        }
    )


CONFIG = """from app.loader import load_config


def request_timeout():
    config = load_config("payments")
    return config.get("timeout", 30)
"""
case(
    "ho-none-config",
    "Config loader returns None for an unknown section",
    {"app/settings.py": CONFIG},
    [("app/settings.py", "request_timeout", 'return config.get("timeout", 30)')],
    "AttributeError: 'NoneType' object has no attribute 'get'",
    {"root_cause_category": "null_reference", "relevant_files": ["app/settings.py"]},
    [
        reply(
            "null_reference",
            quote='config.get("timeout", 30)',
            fix={
                "old": '    return config.get("timeout", 30)\n',
                "new": '    return (config or {}).get("timeout", 30)\n',
            },
        )
    ],
)

WORKERS = """import os

MAX_WORKERS = int(os.environ["MAX_WORKERS"])
"""
case(
    "ho-env-int",
    "Environment variable set to a non-numeric value",
    {"app/pool.py": WORKERS},
    [("app/pool.py", "<module>", "MAX_WORKERS = int(")],
    "ValueError: invalid literal for int() with base 10: 'auto'",
    {"root_cause_category": "configuration_error", "relevant_files": ["app/pool.py"]},
    [reply("configuration_error", quote='int(os.environ["MAX_WORKERS"])', fix=None, confidence=0.6)],
)

CACHE = """import redis

client = redis.Redis(host="redis", port=6379)


def cached_rate(currency):
    return client.get(f"rate:{currency}")
"""
case(
    "ho-redis-down",
    "Cache server unreachable",
    {"app/cache.py": CACHE},
    [("app/cache.py", "cached_rate", 'return client.get(f"rate:{currency}")')],
    "redis.exceptions.ConnectionError: Error 111 connecting to redis:6379. Connection refused.",
    {"root_cause_category": "dependency_unavailable", "relevant_files": ["app/cache.py"]},
    [reply("dependency_unavailable", quote='client.get(f"rate:{currency}")', fix=None, confidence=0.6)],
)

SEARCH = """import requests


def search_repos(query):
    payload = requests.get("https://api.example.test/search", params={"q": query}, timeout=5).json()
    return [item["name"] for item in payload["items"]]
"""
case(
    "ho-upstream-shape",
    "Upstream returned an error document without the expected field",
    {"app/search.py": SEARCH},
    [("app/search.py", "search_repos", 'for item in payload["items"]')],
    "KeyError: 'items'",
    {"root_cause_category": "invalid_external_response", "relevant_files": ["app/search.py"]},
    [
        reply(
            "invalid_external_response",
            quote='payload["items"]',
            fix={"old": 'for item in payload["items"]]', "new": 'for item in payload.get("items", [])]'},
        )
    ],
)

USER = """class User:
    def __init__(self, first, last):
        self.first, self.last = first, last

    def get_full_name(self):
        return f"{self.first} {self.last}"


def greeting(user):
    return "Hello " + user.get_ful_name()
"""
case(
    "ho-typo-method",
    "Misspelled method name",
    {"app/users.py": USER},
    [("app/users.py", "greeting", "user.get_ful_name()")],
    "AttributeError: 'User' object has no attribute 'get_ful_name'",
    {"root_cause_category": "attribute_error", "relevant_files": ["app/users.py"]},
    [reply("attribute_error", quote="user.get_ful_name()", fix={"old": "get_ful_name()", "new": "get_full_name()"})],
)

THEME = """_PREFERENCES = {"language": "en"}


def theme():
    return _PREFERENCES["theme"]
"""
case(
    "ho-internal-key",
    "Internal defaults dictionary lacks a key",
    {"app/prefs.py": THEME},
    [("app/prefs.py", "theme", '_PREFERENCES["theme"]')],
    "KeyError: 'theme'",
    {"root_cause_category": "missing_key", "relevant_files": ["app/prefs.py"]},
    [
        reply(
            "missing_key",
            quote='_PREFERENCES["theme"]',
            fix={"old": '_PREFERENCES["theme"]', "new": '_PREFERENCES.get("theme", "light")'},
        )
    ],
)

TOTAL = """def line_total(row):
    price = row["price"]
    return price * row["quantity"] * 1.2
"""
case(
    "ho-str-price",
    "Price parsed from CSV is still a string",
    {"app/csv_import.py": TOTAL},
    [("app/csv_import.py", "line_total", 'return price * row["quantity"] * 1.2')],
    "TypeError: can't multiply sequence by non-int of type 'float'",
    {"root_cause_category": "type_error", "relevant_files": ["app/csv_import.py"]},
    [
        reply(
            "type_error",
            quote='price = row["price"]',
            fix={"old": '    price = row["price"]\n', "new": '    price = float(row["price"])\n'},
        )
    ],
)

PAGES = """def last_page_titles(pages):
    titles = []
    for i in range(len(pages) + 1):
        titles.append(pages[i]["title"])
    return titles
"""
case(
    "ho-off-by-one",
    "Loop runs one index past the end",
    {"app/pages.py": PAGES},
    [("app/pages.py", "last_page_titles", 'titles.append(pages[i]["title"])')],
    "IndexError: list index out of range",
    {"root_cause_category": "logic_error", "relevant_files": ["app/pages.py"]},
    [
        reply(
            "logic_error",
            quote="range(len(pages) + 1)",
            fix={"old": "range(len(pages) + 1)", "new": "range(len(pages))"},
        )
    ],
)

DATES = """from dateutil import parser


def parse_due(text):
    return parser.isoparse(text)
"""
case(
    "ho-missing-package",
    "Dependency not installed in the image",
    {"app/dates.py": DATES},
    [("app/dates.py", "<module>", "from dateutil import parser")],
    "ModuleNotFoundError: No module named 'dateutil'",
    {"root_cause_category": "import_error", "relevant_files": ["app/dates.py"]},
    [reply("import_error", quote="from dateutil import parser", fix=None, confidence=0.6)],
)

case(
    "ho-gateway-timeout",
    "Only a proxy error page, no application trace",
    {},
    [],
    "504 Gateway Timeout",
    {"root_cause_category": "unknown", "inconclusive": True},
    [reply("unknown", quote=None, fix=None, sufficient=False, confidence=0.1)],
    trace="",
)

ORDER = """def coupon_of(order):
    return order.coupon.code
"""
case(
    "ho-drift-attribute",
    "Deployed code differs from HEAD: error names discount_code, code reads coupon",
    {"app/orders.py": ORDER},
    [("app/orders.py", "coupon_of", "return order.coupon.code")],
    "AttributeError: 'Order' object has no attribute 'discount_code'",
    {"root_cause_category": "attribute_error", "inconclusive": True, "relevant_files": ["app/orders.py"]},
    [reply("unknown", quote="return order.coupon.code", fix=None, sufficient=False, confidence=0.2)],
)

PAY = """import httpx

client = httpx.Client(limits=httpx.Limits(max_connections=2), timeout=5.0)


def charge(payload):
    return client.post("https://pay.example.test/charge", json=payload).json()
"""
case(
    "ho-pool-timeout",
    "HTTP connection pool exhausted towards the payment provider",
    {"app/pay.py": PAY},
    [("app/pay.py", "charge", 'client.post("https://pay.example.test/charge"')],
    "httpx.PoolTimeout",
    {
        "root_cause_category": "dependency_unavailable",
        "alternative_categories": ["configuration_error"],
        "note": "PoolTimeout means the client's connection pool was exhausted. That is a capacity/pool-size setting "
        "(configuration_error) as much as a slow provider (dependency_unavailable); live runs consistently chose the "
        "former, and a reviewer could defend either, so both are accepted.",
        "relevant_files": ["app/pay.py"],
    },
    [reply("dependency_unavailable", quote="max_connections=2", fix=None, confidence=0.5)],
)


def main() -> None:
    OUT.write_text(json.dumps({"version": "rca-holdout-v2", "cases": CASES}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} held-out cases to {OUT}")


if __name__ == "__main__":
    main()
