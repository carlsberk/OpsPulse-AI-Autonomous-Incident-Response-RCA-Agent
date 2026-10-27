"""A patch that removes a statement which still exists elsewhere in the retrieved source is flagged (never blocked)."""

from src.agent.evaluation import surviving_copies
from src.agent.patching import apply_hunks, parse_unified_diff

SOURCE = [
    "def table(rows, widths, header_widths):",
    "    if widths is not None:",
    "        num_cols = len(rows[0])",
    "        widths = expand(widths, num_cols)",
    "    if header_widths is not None:",
    "        num_cols = len(rows[0])",
    "        header_widths = expand(header_widths, num_cols)",
    "    pass",
    "    pass",
]


def diff(old: str, new: str, at: int) -> str:
    return f"--- a/t.py\n+++ b/t.py\n@@ -{at},1 +{at},1 @@\n-{old}\n+{new}\n"


def run(patch: str):
    parsed = parse_unified_diff(patch)
    return surviving_copies(parsed.hunks, apply_hunks(SOURCE, parsed.hunks, 0))


def test_fixing_one_of_two_identical_statements_is_flagged():
    flagged = run(diff("        num_cols = len(rows[0])", "        num_cols = len(rows[0]) if rows else 0", 6))
    assert flagged == [("num_cols = len(rows[0])", 1)]


def test_a_statement_the_patch_writes_back_is_not_a_survivor():
    assert run(diff("    if widths is not None:", "    if widths is not None:  # reviewed", 2)) == []


def test_short_or_comment_lines_are_ignored():
    assert run(diff("    pass", "    return", 8)) == []
