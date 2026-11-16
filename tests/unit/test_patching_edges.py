"""Patch parsing and application edges: the model's diff is untrusted input, so malformed shapes must be refused with a
named reason, never half-applied."""

import pytest

from src.agent.patching import PatchError, apply_hunks, apply_unified_diff, parse_unified_diff

SRC = "def f(x):\n    return x + 1\n\n\ndef g():\n    return 2\n"


def diff(*body, header="--- a/m.py\n+++ b/m.py\n"):
    return header + "\n".join(body) + "\n"


def test_header_paths_lose_their_a_and_b_prefixes_and_trailing_timestamps():
    parsed = parse_unified_diff("--- a/m.py\t2026-01-01\n+++ b/m.py\t2026-01-02\n@@ -1,1 +1,1 @@\n-x\n+y\n")
    assert parsed.path == "m.py"


def test_a_new_file_diff_takes_its_path_from_the_new_side():
    parsed = parse_unified_diff("--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,1 @@\n+x = 1\n")
    assert parsed.path == "new.py"


def test_a_deleted_file_diff_falls_back_to_the_old_path():
    parsed = parse_unified_diff("--- a/old.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-x = 1\n")
    assert parsed.path == "old.py"


def test_git_metadata_lines_are_ignored_and_a_diff_without_headers_counts_as_one_file():
    git_style = "diff --git a/m.py b/m.py\nindex 111..222 100644\n--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    assert parse_unified_diff(git_style).file_count == 1
    headerless = parse_unified_diff("@@ -1,1 +1,1 @@\n-a\n+b\n")
    assert headerless.path is None and headerless.file_count == 1


def test_a_hunk_header_that_does_not_parse_is_still_a_hunk_and_is_located_by_content():
    patched = apply_unified_diff(SRC, diff("@@ whatever @@", " def g():", "-    return 2", "+    return 3"))
    assert "return 3" in patched and "return 2" not in patched


def test_no_newline_markers_are_ignored():
    parsed = parse_unified_diff("--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,1 @@\n-a\n\\ No newline at end of file\n+b\n")
    assert [op for op, _ in parsed.hunks[0].ops] == ["-", "+"]


def test_a_line_that_is_not_context_added_or_removed_is_malformed():
    with pytest.raises(PatchError, match="malformed hunk line"):
        parse_unified_diff("--- a/m.py\n+++ b/m.py\n@@ -1,1 +1,1 @@\n-a\n+b\nrm -rf /\n")


def test_a_hunk_that_changes_nothing_is_refused():
    with pytest.raises(PatchError, match="without any added or removed line"):
        parse_unified_diff("--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n a\n b\n")


def test_a_pure_addition_hunk_cannot_be_located_and_says_so():
    with pytest.raises(PatchError, match="no context/removed lines"):
        apply_unified_diff(SRC, "--- a/m.py\n+++ b/m.py\n@@ -3,0 +4,1 @@\n+new = 1\n")


def test_the_closest_real_lines_are_quoted_when_the_context_is_nearly_right():
    wrong = diff("@@ -5,2 +5,2 @@", " def g():", "-    return 20", "+    return 3")
    with pytest.raises(PatchError, match="Closest lines in the source") as caught:
        apply_unified_diff(SRC, wrong)
    assert "return 2" in str(caught.value)


def test_unrelated_context_gets_no_misleading_suggestion():
    wrong = diff("@@ -1,1 +1,1 @@", "-zzzz qqqq wwww", "+x")
    with pytest.raises(PatchError) as caught:
        apply_unified_diff(SRC, wrong)
    assert "Closest lines" not in str(caught.value)


def test_hunks_apply_in_order_and_line_shifts_from_earlier_hunks_are_accounted_for():
    two = diff(
        "@@ -1,2 +1,3 @@",
        " def f(x):",
        "+    x = int(x)",
        "     return x + 1",
        "@@ -5,2 +6,2 @@",
        " def g():",
        "-    return 2",
        "+    return 3",
    )
    patched = apply_unified_diff(SRC, two)
    assert "x = int(x)" in patched and patched.endswith("return 3\n")


def test_apply_hunks_on_a_window_respects_the_line_offset():
    window = ["    return x + 1", "", ""]
    hunks = parse_unified_diff("--- a/m.py\n+++ b/m.py\n@@ -2,1 +2,1 @@\n-    return x + 1\n+    return x + 2\n").hunks
    assert apply_hunks(window, hunks, line_offset=1)[0] == "    return x + 2"
