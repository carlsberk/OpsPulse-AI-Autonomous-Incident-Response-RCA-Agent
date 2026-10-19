"""Documentation assembly: links between published pages become site paths, everything else points to GitHub."""

from scripts.build_docs import REPO_URL, build, page_map, rewrite_links

PAGES = {
    "README.md": "index.md",
    "CHANGELOG.md": "changelog.md",
    "docs/RUNBOOK.md": "runbook.md",
    "docs/adr/0001-gate.md": "adr/0001-gate.md",
}


def test_links_between_pages_and_images_are_rewritten_for_the_site():
    text = "[runbook](docs/RUNBOOK.md#llm) [adr](docs/adr/0001-gate.md) ![shot](docs/images/a.jpg) [log](CHANGELOG.md)"
    out = rewrite_links(text, "README.md", PAGES)
    assert "(runbook.md#llm)" in out and "(adr/0001-gate.md)" in out
    assert "(images/a.jpg)" in out and "(changelog.md)" in out


def test_relative_links_resolve_from_the_page_that_contains_them():
    assert "(../changelog.md)" in rewrite_links("[c](../../CHANGELOG.md)", "docs/adr/0001-gate.md", PAGES)
    assert "(runbook.md)" in rewrite_links("[r](RUNBOOK.md)", "docs/RUNBOOK.md", PAGES)


def test_other_repository_files_point_to_github_and_external_links_are_untouched():
    out = rewrite_links(
        "[a](src/agent/evaluation.py#L1) [b](evals/results) [c](https://x.test/y) [d](#top)", "README.md", PAGES
    )
    assert f"({REPO_URL}/blob/main/src/agent/evaluation.py#L1)" in out
    assert f"({REPO_URL}/tree/main/evals/results)" in out
    assert "(https://x.test/y)" in out and "(#top)" in out


def test_build_writes_the_pages_and_images(tmp_path):
    written = build(out=tmp_path / "site-src")
    assert "index.md" in written and "runbook.md" in written
    assert any(name.startswith("adr/") for name in written)
    assert (tmp_path / "site-src" / "images").is_dir()
    assert set(page_map(tmp_path)) >= {"README.md", "docs/RUNBOOK.md"}


def test_the_understanding_pages_are_published_and_link_only_to_things_that_exist(tmp_path):
    written = build(out=tmp_path / "site-src")
    assert {"architecture.md", "limitations.md", "trace-example.md"} <= set(written)
    for name in ("architecture.md", "limitations.md", "trace-example.md"):
        text = (tmp_path / "site-src" / name).read_text(encoding="utf-8")
        assert "](docs/" not in text and "](../" not in text, f"{name} has an unrewritten relative link"
