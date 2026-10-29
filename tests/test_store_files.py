import json
from datetime import date, datetime
from pathlib import Path

import pytest

from ainews import store
from tests.helpers import make_item, write_day

TODAY = date(2026, 10, 31)


def test_retention_deletes_only_days_older_than_30(tmp_path):
    for day_date in ("2026-10-31", "2026-10-24", "2026-10-23", "2026-10-01", "2026-09-30"):
        write_day(tmp_path, day_date, [])
    assert store.apply_retention(tmp_path, TODAY) == ["2026-09-30"]
    remaining = []
    for path in sorted(tmp_path.glob("*.json")):
        remaining.append(path.stem)
    assert remaining == ["2026-10-01", "2026-10-23", "2026-10-24", "2026-10-31"]


def test_retention_on_missing_folder_deletes_nothing(tmp_path):
    assert store.apply_retention(tmp_path / "missing", TODAY) == []


def test_prune_work_dirs_deletes_only_run_folders_older_than_30_days(tmp_path):
    for name in ("2026-10-31", "2026-10-01", "2026-09-30", "publish"):
        (tmp_path / name / "sources").mkdir(parents=True)
        (tmp_path / name / "sources" / "1-web.txt").write_text("URL: x", encoding="utf-8")
    assert store.prune_work_dirs(tmp_path, TODAY) == ["2026-09-30"]
    remaining = []
    for folder in sorted(tmp_path.iterdir()):
        remaining.append(folder.name)
    assert remaining == ["2026-10-01", "2026-10-31", "publish"]


def test_prune_work_dirs_on_missing_folder_deletes_nothing(tmp_path):
    assert store.prune_work_dirs(tmp_path / "missing", TODAY) == []


def test_prune_run_log_keeps_the_last_30_days(tmp_path):
    log_path = tmp_path / "runs.log"
    log_lines = [
        "2026-09-30 08:05 collected: 9 new stories",
        "2026-10-01 08:05 collected: 12 new stories",
        "2026-10-31 08:20 published: 30 items",
    ]
    log_text = ""
    for line in log_lines:
        log_text += line + "\n"
    log_path.write_text(log_text, encoding="utf-8")
    assert store.prune_run_log(log_path, TODAY) == 1
    assert log_path.read_text(encoding="utf-8").splitlines() == log_lines[1:]


def test_prune_run_log_without_a_log_does_nothing(tmp_path):
    assert store.prune_run_log(tmp_path / "runs.log", TODAY) == 0


def test_update_seen_records_story_urls_and_forgets_old_entries(tmp_path):
    seen_path = tmp_path / "seen.json"
    seen_path.write_text(
        json.dumps({
            "https://alphasignal.ai/news/ancient": "2026-09-30",
            "https://alphasignal.ai/news/recent": "2026-10-20",
        }),
        encoding="utf-8",
    )
    days = [{
        "date": "2026-10-31",
        "items": [make_item(story_urls=["https://alphasignal.ai/news/a", "https://alphasignal.ai/news/b"])],
    }]
    kept = store.update_seen(seen_path, days, TODAY)
    assert kept == {
        "https://alphasignal.ai/news/recent": "2026-10-20",
        "https://alphasignal.ai/news/a": "2026-10-31",
        "https://alphasignal.ai/news/b": "2026-10-31",
    }
    assert json.loads(seen_path.read_text(encoding="utf-8")) == kept


def test_published_story_urls_collects_every_story_url_of_every_day():
    days = [
        {"date": "2026-10-05", "items": [make_item(story_urls=["https://a/1", "https://a/2"])]},
        {"date": "2026-10-04", "items": [make_item(id="other", story_urls=["https://a/2", "https://a/3"])]},
    ]
    assert store.published_story_urls(days) == {"https://a/1", "https://a/2", "https://a/3"}
    assert store.published_story_urls([]) == set()


def test_load_seen_when_missing_is_empty(tmp_path):
    assert store.load_seen(tmp_path / "seen.json") == {}


def test_read_bookmark_export_sorts_newest_first(tmp_path):
    export_path = tmp_path / "export.json"
    export_path.write_text(
        json.dumps([
            make_item(id="older", bookmarked_at="2026-10-01T08:00:00Z"),
            make_item(id="newer", bookmarked_at="2026-10-03T08:00:00Z"),
        ]),
        encoding="utf-8",
    )
    exported_ids = []
    for bookmark in store.read_bookmark_export(export_path):
        exported_ids.append(bookmark["id"])
    assert exported_ids == ["newer", "older"]


def test_read_bookmark_export_rejects_entry_without_bookmark_time(tmp_path):
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps([make_item()]), encoding="utf-8")
    with pytest.raises(store.DataError, match="bookmarked_at"):
        store.read_bookmark_export(export_path)


def test_read_bookmark_export_rejects_non_array(tmp_path):
    export_path = tmp_path / "export.json"
    export_path.write_text('{"bookmarks": []}', encoding="utf-8")
    with pytest.raises(store.DataError, match="JSON array"):
        store.read_bookmark_export(export_path)


def test_load_bookmarks_when_missing_is_empty(tmp_path):
    assert store.load_bookmarks(tmp_path / "bookmarks.json") == []


def test_append_run_log_adds_timestamped_lines(tmp_path):
    log_path = tmp_path / "data" / "runs.log"
    store.append_run_log(log_path, "collected: 3 new stories", datetime(2026, 10, 5, 8, 5))
    store.append_run_log(log_path, "built", datetime(2026, 10, 5, 8, 25))
    assert log_path.read_bytes() == b"2026-10-05 08:05 collected: 3 new stories\n2026-10-05 08:25 built\n"


def test_write_json_atomic_leaves_no_temporary_file(tmp_path):
    target = tmp_path / "out.json"
    store.write_json_atomic(target, {"a": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1}
    assert not (tmp_path / "out.json.tmp").exists()


def test_write_text_atomic_does_not_use_one_fixed_temporary_name(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"
    seen_temporary_paths = []
    real_replace = store.os.replace

    def spying_replace(source, destination):
        seen_temporary_paths.append(Path(source).name)
        real_replace(source, destination)

    monkeypatch.setattr(store.os, "replace", spying_replace)
    store.write_text_atomic(target, "one")
    store.write_text_atomic(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert seen_temporary_paths[0] != seen_temporary_paths[1]
    assert seen_temporary_paths[0] != "out.txt.tmp"
    assert list(tmp_path.iterdir()) == [target]


def test_write_text_atomic_removes_its_temporary_file_when_the_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"
    target.write_text("old", encoding="utf-8")

    def failing_replace(source, destination):
        raise OSError("locked")

    monkeypatch.setattr(store.os, "replace", failing_replace)
    with pytest.raises(OSError):
        store.write_text_atomic(target, "new")
    assert list(tmp_path.iterdir()) == [target]
    assert target.read_text(encoding="utf-8") == "old"


def test_append_run_log_collapses_line_breaks_so_a_message_cannot_forge_a_line(tmp_path):
    log_path = tmp_path / "data" / "runs.log"
    forged = "failed: 2026-10-05, boom\r\n2026-10-05 09:00 published: 2026-10-05, 9 items, 1 batches x\x85y\n"
    store.append_run_log(log_path, forged, datetime(2026, 10, 5, 8, 5))
    lines = log_path.read_bytes().split(b"\n")
    assert len(lines) == 2
    assert lines[1] == b""
    assert lines[0] == "2026-10-05 08:05 failed: 2026-10-05, boom 2026-10-05 09:00 published: 2026-10-05, 9 items, 1 batches x y".encode()
