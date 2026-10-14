import pytest

from src.agent.patching import (
    PatchError,
    apply_hunks,
    apply_unified_diff,
    format_code_window,
    parse_code_window,
    parse_unified_diff,
    path_matches,
)
from tests.unit.factories import BAD_DIFF, GOOD_DIFF, SOURCE


def test_apply_good_diff_and_eol_preserved():
    out = apply_unified_diff(SOURCE, GOOD_DIFF)
    assert 'raise ValueError("missing profile")' in out and out.endswith("\n")
    crlf = apply_unified_diff(SOURCE.replace("\n", "\r\n"), GOOD_DIFF)
    assert "\r\n" in crlf and "\n" not in crlf.replace("\r\n", "")


def test_bad_context_is_rejected():
    with pytest.raises(PatchError):
        apply_unified_diff(SOURCE, BAD_DIFF)


def test_wrong_hunk_numbers_still_apply():
    shifted = GOOD_DIFF.replace("@@ -3,6 +3,8 @@", "@@ -40,6 +40,8 @@")
    assert "raise ValueError" in apply_unified_diff(SOURCE, shifted)


def test_window_roundtrip_and_offset_apply():
    window = format_code_window(SOURCE.splitlines()[2:], 3)
    start, lines = parse_code_window(window)
    assert start == 3
    positions = []
    out = apply_hunks(lines, parse_unified_diff(GOOD_DIFF).hunks, start - 1, positions)
    assert positions == [3] and any("raise ValueError" in x for x in out)


def test_parser_edge_cases():
    with pytest.raises(PatchError):
        parse_unified_diff("")
    with pytest.raises(PatchError):
        parse_unified_diff("--- a/x\n+++ b/x\n")
    fenced = "```diff\n" + GOOD_DIFF + "```"
    assert parse_unified_diff(fenced).path == "src/app.py"
    assert path_matches("src/app.py", "app/src/app.py") and not path_matches("a.py", "b.py")


def test_second_file_header_after_a_hunk_is_not_misread_as_removed_line():
    two_files = GOOD_DIFF + GOOD_DIFF.replace("src/app.py", "src/b.py")
    parsed = parse_unified_diff(two_files)
    assert parsed.file_count == 2 and len(parsed.hunks) == 2
    assert all(text != "-- a/src/b.py" for hunk in parsed.hunks for _, text in hunk.ops)


def test_unapplied_hunk_names_the_closest_real_lines_without_loosening_the_match():
    source = [
        "def total(items):",
        "    result = 0",
        "    for item in items:",
        "        result += item.price",
        "    return result",
    ]
    # the model changed the indentation of a context line and dropped nothing else
    diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -2,3 +2,3 @@\n"
        "      result = 0\n"
        "      for item in items:\n"
        "-        result += item.price\n"
        "+        result += item.price * item.qty\n"
    )
    parsed = parse_unified_diff(diff)
    with pytest.raises(PatchError) as info:
        apply_hunks(source, parsed.hunks)
    message = str(info.value)
    assert "were not found" in message and "Closest lines in the source" in message
    assert "L2: '    result = 0'" in message  # the real line, with its real number and indentation
    # the same hunk with the exact indentation applies
    ok = parse_unified_diff(
        diff.replace("      result = 0", "     result = 0").replace("      for item", "     for item")
    )
    assert apply_hunks(source, ok.hunks)[3] == "        result += item.price * item.qty"


def test_a_hunk_that_miscounts_blank_lines_is_reported_at_the_line_that_differs():
    source = ["from dateutil import parser", "", "", "def parse_due(text):", "    return parser.isoparse(text)"]
    diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -1,5 +1,5 @@\n"
        "-from dateutil import parser\n"
        "-\n"
        "-\n"
        "-\n"  # the file has two blank lines, not three
        "-def parse_due(text):\n"
        "+import dateutil.parser as parser\n"
        " \n"
        "     return parser.isoparse(text)\n"
    )
    parsed = parse_unified_diff(diff)
    with pytest.raises(PatchError) as info:
        apply_hunks(source, parsed.hunks)
    message = str(info.value)
    assert "hunk 1: context/removed lines were not found" in message
    assert "matches the file from line 1 for 3 line(s)" in message
    assert "hunk line 4 ('')" in message and "'def parse_due(text):'" in message  # what differs, and what is there
