import pytest

from ainews import verify
from tests.helpers import converted_browser_capture, raw_browser_capture


def test_read_story_sources_reads_every_kind_of_saved_source(tmp_path):
    (tmp_path / "1-x.txt").write_text("URL: https://x.com/u/status/1\n\nPost with 42 GB\n", encoding="utf-8")
    (tmp_path / "2-browser.txt").write_text(converted_browser_capture("https://openai.com/index/a/"), encoding="utf-8")
    (tmp_path / "3-webfetch.txt").write_text("URL: https://site.com/b\n\n(text returned by the fetch tool)\n", encoding="utf-8")
    source_keys, text = verify.read_story_sources(tmp_path)
    assert source_keys == {"x.com/u/status/1", "openai.com/index/a", "site.com/b"}
    assert "42 GB" in text


def test_read_story_sources_ignores_raw_and_bot_check_browser_captures(tmp_path):
    (tmp_path / "1-browser.txt").write_text(raw_browser_capture("https://openai.com/index/raw"), encoding="utf-8")
    challenge_text = "Just a moment... Verify you are human by completing the action below. " * 4
    (tmp_path / "2-browser.txt").write_text(converted_browser_capture("https://openai.com/index/blocked", challenge_text), encoding="utf-8")
    (tmp_path / "3-browser.txt").write_text(converted_browser_capture("https://openai.com/index/good"), encoding="utf-8")
    source_keys, text = verify.read_story_sources(tmp_path)
    assert source_keys == {"openai.com/index/good"}
    assert "Just a moment" not in text


def test_read_story_sources_of_a_missing_folder_is_empty(tmp_path):
    assert verify.read_story_sources(tmp_path / "absent") == (set(), "")


def test_unmatched_numbers_ignores_thousands_commas():
    item = {"bullets": ["Q2D-Web: 190,000,000 documents", "Recall 74.8%", "Seen 3 times"]}
    assert verify.unmatched_numbers(item, "Q2D-Web has 190000000 docs, recall 74.8") == [("3", "Seen 3 times")]


@pytest.mark.parametrize(
    "source_text",
    [
        "Published Oct 12, 2026 by the team",
        "October 12, 2026",
        "12 October 2026",
        "Sept. 30 update; date: 2026-10-12",
        "Posted Oct. 12, 2026",
        "Posted 12 Oct 2026",
    ],
)
def test_unmatched_numbers_accepts_an_iso_date_the_source_writes_another_way(source_text):
    item = {"bullets": ["Announced 2026-10-12 by the team"]}
    assert verify.unmatched_numbers(item, source_text) == []


def test_unmatched_numbers_reports_an_iso_date_missing_from_the_sources_as_one_date():
    item = {"bullets": ["Announced 2026-10-12; 3 models"]}
    assert verify.unmatched_numbers(item, "Announced Oct 11, 2026 with 3 models") == [
        ("2026-10-12", "Announced 2026-10-12; 3 models"),
    ]


def test_unmatched_numbers_checks_the_whole_pr_check_background_notes_included():
    item = {"bullets": ["30,000 merges a day"], "pr_check": {
        "why": "vendor's own metric (1.7x faster)",
        "holds_up": "Seven engineers built it in 5 months.",
        "notes": [
            {"kind": "careful", "text": "Up from 2,500 merges a day."},
            {"kind": "background", "text": "Piper has served Google since 2012."},
        ],
    }}
    source = "Seven engineers, five months. 30,000 merges per day."
    assert verify.unmatched_numbers(item, source) == [
        ("1.7", "vendor's own metric (1.7x faster)"),
        ("5", "Seven engineers built it in 5 months."),
        ("2,500", "Up from 2,500 merges a day."),
        ("2012", "Piper has served Google since 2012."),
    ]


@pytest.mark.parametrize(
    "pitch, expected",
    [
        ("coding is no longer the bottleneck", True),
        ("Coding is no longer   the bottleneck", True),
        ("we don’t write code anymore", True),
        ("At Anthropic, coding is no longer the bottleneck … doing research", True),
        ("coding is over", False),
    ],
)
def test_pitch_in_sources(pitch, expected):
    source = "At Anthropic, coding is no longer the bottleneck to building systems or doing research. We don't write code anymore."
    assert verify.pitch_in_sources(pitch, source) is expected


def test_earlier_repeats_needs_a_shared_deep_link():
    item = {"sources": [{"url": "https://huggingface.co/LiquidAI/d1-3B"}, {"url": "https://liquid.ai/"}]}
    earlier = [
        ("2026-10-08", {"title": "d1 release", "sources": [{"url": "https://huggingface.co/LiquidAI/d1-3B/"}]}),
        ("2026-10-07", {"title": "Liquid home", "sources": [{"url": "https://www.liquid.ai"}]}),
    ]
    assert verify.earlier_repeats(item, earlier) == [
        ("2026-10-08", "d1 release", "https://huggingface.co/LiquidAI/d1-3B/")
    ]


@pytest.mark.parametrize(
    "source_text",
    ["Published Oct 05, 2026", "Published October 05, 2026", "Published 05 October 2026", "Published 5 Oct 2026", "Oct 5th, 2026"],
)
def test_unmatched_numbers_accepts_a_day_written_with_a_leading_zero(source_text):
    item = {"bullets": ["Announced 2026-10-05"]}
    assert verify.unmatched_numbers(item, source_text) == []


@pytest.mark.parametrize("source_text", ["Published Oct 15, 2026", "Published 25 October 2026", "Oct 05, 2025"])
def test_unmatched_numbers_does_not_match_day_5_inside_another_day_or_year(source_text):
    item = {"bullets": ["Announced 2026-10-05"]}
    assert verify.unmatched_numbers(item, source_text) == [("2026-10-05", "Announced 2026-10-05")]


def test_unmatched_numbers_checks_the_title():
    item = {"title": "Lab releases 70B model", "bullets": ["Size: 70B"]}
    assert verify.unmatched_numbers(item, "A 70B model") == []
    missing_item = {"title": "Lab releases 405B model", "bullets": []}
    assert verify.unmatched_numbers(missing_item, "A 70B model") == [("405", "Lab releases 405B model")]
