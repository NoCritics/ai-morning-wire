import pytest

from ainews import store
from tests.helpers import make_item, write_day


def test_valid_item_passes():
    store.validate_item(make_item(), "test")


@pytest.mark.parametrize(
    "overrides, message_part",
    [
        ({"type": "rumour"}, "type 'rumour'"),
        ({"bullets": ["one", "two"]}, "3 to 5"),
        ({"bullets": ["1", "2", "3", "4", "5", "6"]}, "3 to 5"),
        ({"bullets": ["one", "", "three"]}, "non-empty"),
        ({"sources": []}, "at least one source"),
        ({"sources": [{"label": "Blog", "url": "ftp://example.com"}]}, "http(s)"),
        ({"id": "Has Spaces"}, "a-z, 0-9"),
        ({"published": "4 Oct"}, "YYYY-MM-DD"),
        ({"story_urls": []}, "story_urls"),
        ({"title": "  "}, "'title'"),
    ],
)
def test_invalid_items_are_rejected(overrides, message_part):
    with pytest.raises(store.DataError) as raised:
        store.validate_item(make_item(**overrides), "day.json item 1")
    assert message_part in str(raised.value)
    assert "day.json item 1" in str(raised.value)


SCION_PR_CHECK = {
    "why": "vendor's own metric (merges per day)",
    "holds_up": "Claude helped seven engineers build and migrate to a new source control system in five months.",
    "notes": [
        {"kind": "context", "text": "The post credits Google's Piper and Meta's Sapling as prior art."},
        {"kind": "background", "text": "Spanner is Google's distributed database."},
        {"kind": "careful", "text": "30,000 merges a day counts code merged, not useful software."},
    ],
    "pitch": "coding is no longer the bottleneck",
}


def test_item_with_a_pr_check_passes():
    store.validate_item(make_item(pr_check=SCION_PR_CHECK), "test")


def pr_check_with(**changes):
    pr_check = dict(SCION_PR_CHECK)
    pr_check.update(changes)
    return pr_check


@pytest.mark.parametrize(
    "pr_check, message_part",
    [
        ("plain text", "'pr_check' must be a JSON object"),
        (pr_check_with(why=""), "'why'"),
        (pr_check_with(holds_up=None), "'holds_up'"),
        (pr_check_with(notes=[]), "1 to 4 notes"),
        (pr_check_with(notes=[{"kind": "opinion", "text": "x"}]), "context, background or careful"),
        (pr_check_with(notes=[{"kind": "careful", "text": " "}]), "'text'"),
        (pr_check_with(pitch=""), "'pitch'"),
    ],
)
def test_invalid_pr_checks_are_rejected(pr_check, message_part):
    with pytest.raises(store.DataError) as raised:
        store.validate_item(make_item(pr_check=pr_check), "day.json item 1")
    assert message_part in str(raised.value)


def test_pr_check_pitch_is_optional():
    pr_check = dict(SCION_PR_CHECK)
    del pr_check["pitch"]
    store.validate_item(make_item(pr_check=pr_check), "test")


def test_load_day_rejects_date_that_differs_from_file_name(tmp_path):
    day_path = tmp_path / "2026-10-05.json"
    day_path.write_text('{"date": "2026-10-04", "items": []}', encoding="utf-8")
    with pytest.raises(store.DataError, match="must equal 2026-10-05"):
        store.load_day(day_path)


def test_load_day_rejects_broken_json(tmp_path):
    day_path = tmp_path / "2026-10-05.json"
    day_path.write_text('{"date": ', encoding="utf-8")
    with pytest.raises(store.DataError, match="not valid JSON"):
        store.load_day(day_path)


def test_load_day_rejects_badly_named_file(tmp_path):
    day_path = tmp_path / "notes.json"
    day_path.write_text("{}", encoding="utf-8")
    with pytest.raises(store.DataError, match="YYYY-MM-DD.json"):
        store.load_day(day_path)


def test_load_days_returns_newest_first(tmp_path):
    write_day(tmp_path, "2026-10-03", [make_item(id="older")])
    write_day(tmp_path, "2026-10-05", [make_item(id="newer")])
    dates = []
    for day in store.load_days(tmp_path):
        dates.append(day["date"])
    assert dates == ["2026-10-05", "2026-10-03"]


def test_load_days_rejects_an_id_used_on_two_days(tmp_path):
    write_day(tmp_path, "2026-10-03", [make_item(id="same-id")])
    write_day(tmp_path, "2026-10-05", [make_item(id="same-id")])
    with pytest.raises(store.DataError, match="'same-id' is used more than once"):
        store.load_days(tmp_path)


def test_load_days_on_missing_folder_is_empty(tmp_path):
    assert store.load_days(tmp_path / "missing") == []


def test_load_day_upgrades_items_saved_with_alphasignal_urls(tmp_path):
    legacy_item = make_item()
    legacy_item["alphasignal_urls"] = legacy_item.pop("story_urls")
    day_path = write_day(tmp_path, "2026-10-05", [legacy_item])
    day = store.load_day(day_path)
    assert day["items"][0]["story_urls"] == ["https://alphasignal.ai/news/xing"]
    assert "alphasignal_urls" not in day["items"][0]


def test_industry_is_an_item_type():
    store.validate_item(make_item(type="industry"), "test")
