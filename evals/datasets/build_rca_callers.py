"""Cases whose root cause lies in a calling function (run: python -m evals.datasets.build_rca_callers).

The failing line is innocent: a caller passes a bad value. Only the trigger file's window (+/- 50 lines) used to be
shown to the model, so quoting the caller was impossible to verify. With caller context (`CALLER_CONTEXT_FRAMES`,
default 2) the caller's lines are shown and quotes from them are verified. Run the same dataset with
`CALLER_CONTEXT_FRAMES=0` to measure the difference. One control case has its cause in the trigger frame, so the
extra context must not distract.

Mock replies quote the caller line and propose no patch (the fix belongs in the caller, which the patch policy does
not allow to change), so mock mode measures grounding and routing, not the model.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from evals.datasets.build_rca_cases import py_trace, reply

OUT = pathlib.Path(__file__).with_name("rca_callers.json")
REPO = "eval/callers"
CASES: list[dict[str, Any]] = []


def case(case_id, scenario, files, frames, final, expected, replies) -> None:
    CASES.append(
        {
            "id": case_id,
            "scenario": scenario,
            "repo_name": REPO,
            "error_message": final,
            "stack_trace": py_trace(files, frames, final),
            "files": files,
            "history": [],
            "expected": {"inconclusive": False, "relevant_files": [], **expected},
            "mock_replies": replies,
        }
    )


GREETING = """def greeting(customer):
    return f"Hello, {customer.name}!"
"""
CUSTOMERS = """def find_customer(customers, customer_id):
    for customer in customers:
        if customer.id == customer_id:
            return customer
    return None
"""
MAILER = """from app.customers import find_customer
from app.greeting import greeting


def welcome_email(customers, customer_id):
    customer = find_customer(customers, customer_id)
    return greeting(customer)
"""
CUSTOMER_FILES = {"app/greeting.py": GREETING, "app/customers.py": CUSTOMERS, "app/mailer.py": MAILER}
CUSTOMER_FRAMES = [
    ("app/mailer.py", "welcome_email", "return greeting(customer)"),
    ("app/greeting.py", "greeting", "customer.name"),
]
CUSTOMER_ERROR = "AttributeError: 'NoneType' object has no attribute 'name'"
CALLER_QUOTE = "customer = find_customer(customers, customer_id)"
case(
    "caller-passes-none",
    "A lookup that returns None for an unknown id is passed on to a function that reads an attribute of it",
    CUSTOMER_FILES,
    CUSTOMER_FRAMES,
    CUSTOMER_ERROR,
    {"root_cause_category": "null_reference", "relevant_files": ["app/mailer.py"]},
    [reply("null_reference", quote=CALLER_QUOTE, fix=None)],
)

PAYMENTS = """def split_payment(total, parts):
    share = total / parts
    return [share] * parts
"""
ORDERS = """from app.payments import split_payment


def checkout(order):
    payers = order.get("payers", [])
    shares = split_payment(order["total"], len(payers))
    return dict(zip(payers, shares))
"""
case(
    "caller-empty-list",
    "Checkout splits a bill between zero payers",
    {"app/payments.py": PAYMENTS, "app/orders.py": ORDERS},
    [("app/orders.py", "checkout", "split_payment(order"), ("app/payments.py", "split_payment", "total / parts")],
    "ZeroDivisionError: division by zero",
    {"root_cause_category": "logic_error", "relevant_files": ["app/orders.py"]},
    [reply("logic_error", quote='payers = order.get("payers", [])', fix=None)],
)

case(
    "caller-symptomatic-patch",
    "The model quotes the calling code but patches the callee; the gate must ask where the fix belongs",
    CUSTOMER_FILES,
    CUSTOMER_FRAMES,
    CUSTOMER_ERROR,
    {"root_cause_category": "null_reference", "relevant_files": ["app/mailer.py"]},
    [
        reply(
            "null_reference",
            quote=CALLER_QUOTE,
            fix={
                "old": '    return f"Hello, {customer.name}!"\n',
                "new": '    if customer is None:\n        return "Hello!"\n    return f"Hello, {customer.name}!"\n',
            },
        ),
        reply("null_reference", quote=CALLER_QUOTE, fix=None),
    ],
)

UTIL = """def page_offset(page, size):
    return (page - 1) * size
"""
SERVICE = """from app.util import page_offset


def list_items(db, page, size=20):
    offset = page_offset(page, size)
    return db.fetch(offset=offset, limit=size)
"""
HANDLER = """from app.service import list_items


def get_items(request, db):
    page = request.query.get("page", "1")
    return list_items(db, page)
"""
case(
    "caller-two-levels-up",
    "A handler passes the raw query-string value (str) two calls down to arithmetic",
    {"app/util.py": UTIL, "app/service.py": SERVICE, "app/handlers.py": HANDLER},
    [
        ("app/handlers.py", "get_items", "return list_items"),
        ("app/service.py", "list_items", "offset = page_offset"),
        ("app/util.py", "page_offset", "(page - 1)"),
    ],
    "TypeError: unsupported operand type(s) for -: 'str' and 'int'",
    {"root_cause_category": "type_error", "relevant_files": ["app/handlers.py"]},
    [reply("type_error", quote='page = request.query.get("page", "1")', fix=None)],
)

STATS = """def mean(values):
    return sum(values) / len(values)


def safe_mean(values):
    return mean(values)
"""
DASH = """from app.stats import safe_mean


def tile(metrics):
    values = [m["value"] for m in metrics if m["value"] is not None]
    return safe_mean(values)
"""
case(
    "control-cause-in-trigger",
    "Control: the caller filters correctly; safe_mean promises safety but does not handle an empty list",
    {"app/stats.py": STATS, "app/dashboard.py": DASH},
    [
        ("app/dashboard.py", "tile", "return safe_mean"),
        ("app/stats.py", "safe_mean", "return mean(values)"),
        ("app/stats.py", "mean", "sum(values) / len"),
    ],
    "ZeroDivisionError: division by zero",
    {"root_cause_category": "logic_error", "relevant_files": ["app/stats.py"]},
    [
        reply(
            "logic_error",
            quote="return sum(values) / len(values)",
            fix={"old": "    return mean(values)\n", "new": "    return mean(values) if values else 0.0\n"},
        )
    ],
)


def main() -> None:
    OUT.write_text(json.dumps({"version": "rca-callers-v3", "cases": CASES}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} caller cases to {OUT}")


if __name__ == "__main__":
    main()
