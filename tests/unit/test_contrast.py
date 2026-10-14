"""WCAG 2.1 AA contrast (4.5:1 for normal text) of the console's colour tokens, in both themes.

The console defines its palette as CSS variables; this reads them from styles.css and checks the text/background
pairs that are actually used together, so a palette change cannot silently make the page hard to read.
"""

import pathlib
import re

import pytest

from src.console import STATIC_DIR

CSS = (pathlib.Path(STATIC_DIR) / "styles.css").read_text(encoding="utf-8")

# (foreground token, background token): pairs that appear together in the console
PAIRS = [
    ("text", "bg"), ("text", "panel"), ("text", "panel-2"), ("muted", "panel"), ("muted", "bg"),
    ("muted", "panel-2"), ("accent", "panel"), ("accent", "accent-soft"), ("on-accent", "accent"),
    ("ok", "ok-soft"), ("warn", "warn-soft"), ("bad", "bad-soft"), ("neutral", "neutral-soft"),
    ("ok", "panel"), ("warn", "panel"), ("bad", "panel"), ("text", "add"), ("text", "del"), ("muted", "code"),
]  # fmt: skip


def _block(selector_start: str) -> dict[str, str]:
    start = CSS.index(selector_start)
    body = CSS[CSS.index("{", start) + 1 : CSS.index("}", start)]
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", body))


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


LIGHT = _block(":root {")
DARK = {**LIGHT, **_block(':root[data-theme="dark"] {')}


def test_contrast_helper_matches_known_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.02)  # the classic "just fails" grey


@pytest.mark.parametrize("theme,tokens", [("light", LIGHT), ("dark", DARK)])
def test_every_text_background_pair_meets_wcag_aa(theme, tokens):
    failures = []
    for fg, bg in PAIRS:
        ratio = contrast(tokens[fg], tokens[bg])
        if ratio < 4.5:
            failures.append(f"{fg} on {bg}: {ratio:.2f} ({tokens[fg]} / {tokens[bg]})")
    assert not failures, f"{theme} theme: " + "; ".join(failures)
