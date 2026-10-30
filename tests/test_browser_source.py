import json
import shutil
import sys
from pathlib import Path

import pytest

import browser_source
from ainews import sources
from tests.helpers import raw_browser_capture

ARTICLE_TEXT = "GPT-6 and Intelligent UI for everyone\n\n\n   October 7, 2026   \n\n" + "Availability details. " * 20


def capture(text):
    return raw_browser_capture("https://openai.com/index/gpt-6-for-everyone/", text)


def test_browser_capture_text_decodes_the_json_string_and_tidies_lines():
    text = sources.browser_capture_text(capture(ARTICLE_TEXT))
    assert text.startswith("URL: https://openai.com/index/gpt-6-for-everyone/\n\n(text read in a browser)\n\n")
    assert "GPT-6 and Intelligent UI for everyone\n\nOctober 7, 2026\n\n" in text
    assert "\n\n\n" not in text


def test_browser_capture_text_accepts_plain_text_too():
    plain = "URL: https://example.com/a\n\n(text read in a browser)\n\n" + ARTICLE_TEXT
    assert sources.browser_capture_text(plain).startswith("URL: https://example.com/a\n")


def test_browser_capture_text_rejects_a_missing_url_header():
    with pytest.raises(sources.SourceError, match="URL"):
        sources.browser_capture_text(json.dumps(ARTICLE_TEXT))


def test_browser_capture_text_rejects_a_bot_check_page():
    with pytest.raises(sources.SourceError, match="bot check"):
        sources.browser_capture_text(capture("Just a moment...\nVerify you are human by completing the action below."))


def test_browser_capture_text_rejects_reddits_humanity_check():
    reddit_check = (
        "Prove your humanity\n\nWe're committed to safety and security. But not for bots. "
        "Complete the challenge below and let us know you're a real person.\n\n"
        "Reddit, Inc. 2026. All rights reserved. " + "Footer link. " * 10
    )
    with pytest.raises(sources.SourceError, match="bot check"):
        sources.browser_capture_text(capture(reddit_check))


def test_browser_capture_text_rejects_a_near_empty_page():
    with pytest.raises(sources.SourceError, match="almost no readable text"):
        sources.browser_capture_text(capture("Adobe Exchange"))


def test_browser_capture_text_truncates_long_pages():
    text = sources.browser_capture_text(capture("word " * 20000))
    assert "[truncated:" in text


def test_convert_rewrites_good_captures_and_deletes_bad_ones(tmp_path):
    good_path = tmp_path / "2-browser.txt"
    good_path.write_text(capture(ARTICLE_TEXT), encoding="utf-8")
    blocked_path = tmp_path / "3-browser.txt"
    blocked_path.write_text(capture("Access denied"), encoding="utf-8")
    results = browser_source.convert([good_path, blocked_path])
    assert results[0][1] is None
    assert good_path.read_text(encoding="utf-8").startswith("URL: https://openai.com/")
    assert "bot check" in results[1][1]
    assert not blocked_path.exists()


def make_capture_folder(root, date_text="2026-10-28", slug="story-a"):
    folder = root / "work" / date_text / "sources" / slug
    folder.mkdir(parents=True)
    return folder


def test_refusal_reason_accepts_a_capture_inside_the_sources_folder(tmp_path):
    folder = make_capture_folder(tmp_path)
    assert browser_source.refusal_reason(folder / "2-browser.txt", tmp_path) is None


@pytest.mark.parametrize(
    "relative_path",
    [
        "config.toml",
        "data/runs.log",
        "work/2026-10-28/sources/story-a/2-webfetch.txt",
        "work/2026-10-28/sources/2-browser.txt",
        "work/2026-10-28/sources/story-a/deeper/2-browser.txt",
        "work/2026-10-28/stories.json",
        "work/not-a-date/sources/story-a/2-browser.txt",
        "work/2026-10-28/replies/story-a/2-browser.txt",
        "elsewhere/2026-10-28/sources/story-a/2-browser.txt",
    ],
)
def test_refusal_reason_refuses_every_other_path(tmp_path, relative_path):
    assert browser_source.refusal_reason(tmp_path / relative_path, tmp_path) is not None


def test_refusal_reason_refuses_a_path_outside_the_folder(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside_folder = make_capture_folder(tmp_path / "other")
    assert "outside" in browser_source.refusal_reason(outside_folder / "2-browser.txt", root)


def test_refusal_reason_refuses_dot_dot_that_escapes_the_sources_folder(tmp_path):
    folder = make_capture_folder(tmp_path)
    escaping = folder / ".." / ".." / ".." / ".." / "config-browser.txt"
    assert browser_source.refusal_reason(escaping, tmp_path) is not None


def test_main_refuses_a_foreign_path_and_leaves_every_file_untouched(monkeypatch, capsys):
    foreign_path = Path(browser_source.__file__).resolve().parent / "pytest.ini"
    before = foreign_path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["browser_source.py", str(foreign_path)])
    assert browser_source.main() == 2
    assert "refused:" in capsys.readouterr().out
    assert foreign_path.read_bytes() == before


def test_main_refuses_the_whole_call_when_one_path_is_foreign(monkeypatch, capsys):
    root = Path(browser_source.__file__).resolve().parent
    folder = root / "work" / "2099-01-01" / "sources" / "guard-test"
    folder.mkdir(parents=True)
    try:
        capture_path = folder / "1-browser.txt"
        capture_text = capture(ARTICLE_TEXT)
        capture_path.write_text(capture_text, encoding="utf-8")
        monkeypatch.setattr(sys, "argv", ["browser_source.py", str(capture_path), str(root / "pytest.ini")])
        assert browser_source.main() == 2
        assert capture_path.read_text(encoding="utf-8") == capture_text
    finally:
        shutil.rmtree(root / "work" / "2099-01-01")
        if not any((root / "work").iterdir()):
            (root / "work").rmdir()
