import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import build
from ainews import config, render, store
from tests.helpers import embedded_news_data, make_item, make_root_with_broken_config, make_template, write_day

TODAY = date(2026, 10, 31)


def make_root(tmp_path):
    make_template(tmp_path / "template")
    return tmp_path


def test_split_days_puts_day_7_in_feed_and_day_8_in_archive():
    days = [
        {"date": "2026-10-24", "items": []},
        {"date": "2026-10-23", "items": []},
        {"date": "2026-09-30", "items": []},
    ]
    feed_days, archive_days = render.split_days(days, TODAY)
    assert feed_days == [days[0]]
    assert archive_days == [days[1]]


def test_embed_json_cannot_close_the_script_element():
    data = {"title": "</script><script>alert(1)</script>", "line": "a\u2028b"}
    embedded = render.embed_json(data)
    assert "</script>" not in embedded
    assert "\u2028" not in embedded
    assert json.loads(embedded) == data


def test_render_page_standalone_and_fragment(tmp_path):
    make_template(tmp_path / "template")
    days = [{"date": "2026-10-31", "items": [make_item()]}]
    fragment = render.render_page(tmp_path / "template", days, [], [], TODAY, {"result": None, "items": None, "failedReason": None}, "08:00")
    standalone = render.standalone_page(fragment)
    assert standalone == render.STANDALONE_HEAD + fragment + render.STANDALONE_TAIL
    assert standalone.startswith("<!doctype html>")
    assert standalone.rstrip().endswith("</html>")
    assert fragment.startswith("<title>")
    assert "{{NEWS_DATA}}" not in fragment
    assert "/*__JS__*/" not in fragment
    assert "Xing4.0-29B-A4B" in fragment


def test_build_writes_both_pages_and_marks_stories_seen(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    summary = build.build(root, TODAY)
    assert (root / "ai-news.html").read_text(encoding="utf-8").startswith("<!doctype html>")
    assert (root / "work" / "publish" / "ai-news.html").read_text(encoding="utf-8").startswith("<title>")
    seen = json.loads((root / "data" / "seen.json").read_text(encoding="utf-8"))
    assert seen == {"https://alphasignal.ai/news/xing": "2026-10-31"}
    assert summary.startswith("built: 1 feed days (1 items), 0 archive days, 0 bookmarks")


def test_build_keeps_the_local_page_whole_and_the_published_page_small(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    write_day(root / "data" / "days", "2026-09-30", [make_item(id="old-item", story_urls=["https://alphasignal.ai/news/old"])])
    bookmark = make_item(id="saved-item", bookmarked_at="2026-10-30T09:00:00Z")
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps([bookmark]), encoding="utf-8")
    build.build(root, TODAY, export_path)
    local_page = (root / "ai-news.html").read_text(encoding="utf-8")
    assert "saved-item" in local_page
    assert "xing4-0-29b-a4b" in local_page
    assert "old-item" not in local_page
    remaining_days = store.load_days(root / "data" / "days")
    saved_bookmarks = store.load_bookmarks(root / "data" / "bookmarks.json")
    no_run_status = {"result": None, "items": None, "failedReason": None}
    feed_days, archive_days = render.split_days(remaining_days, TODAY)
    assert local_page == render.standalone_page(render.render_page(root / "template", feed_days, archive_days, saved_bookmarks, TODAY, no_run_status, "08:00"))
    published_page = (root / "work" / "publish" / "ai-news.html").read_text(encoding="utf-8")
    assert "saved-item" not in published_page
    assert "xing4-0-29b-a4b" not in published_page
    assert "old-item" not in published_page
    seen = json.loads((root / "data" / "seen.json").read_text(encoding="utf-8"))
    assert seen == {"https://alphasignal.ai/news/xing": "2026-10-31"}


def test_render_published_page_links_side_files_and_embeds_no_items(tmp_path):
    make_template(tmp_path / "template")
    status = {"result": None, "items": None, "failedReason": None}
    published = render.render_published_page(tmp_path / "template", ["2026-10-31"], [], TODAY, status, "08:00")
    assert '<link rel="stylesheet" href="page.css">' in published
    assert '<script src="page.js"></script>' in published
    for left_over in ("/*__CSS__*/", "/*__JS__*/", "body{}", "void 0;", "Xing4.0-29B-A4B"):
        assert left_over not in published
    assert embedded_news_data(published) == {
        "built": "2026-10-31",
        "feedDates": ["2026-10-31"],
        "archiveDates": [],
        "status": status,
        "digestTime": "08:00",
    }


def test_build_writes_the_publish_folder(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    write_day(root / "data" / "days", "2026-10-20", [make_item(id="archived-item", story_urls=["https://alphasignal.ai/news/archived"])])
    bookmark = make_item(id="saved-item", bookmarked_at="2026-10-30T09:00:00Z")
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps([bookmark]), encoding="utf-8")
    build.build(root, TODAY, export_path)
    publish_dir = root / "work" / "publish"
    for loaded_day in store.load_days(root / "data" / "days"):
        day_file = publish_dir / "days" / f"{loaded_day['date']}.json"
        assert json.loads(day_file.read_text(encoding="utf-8")) == loaded_day
    assert json.loads((publish_dir / "bookmarks.json").read_text(encoding="utf-8")) == [bookmark]
    assert (publish_dir / "page.css").read_text(encoding="utf-8") == "body{}"
    assert (publish_dir / "page.js").read_text(encoding="utf-8") == "void 0;"
    assert json.loads((publish_dir / "files.json").read_text(encoding="utf-8")) == [
        "bookmarks.json",
        "days/2026-10-20.json",
        "days/2026-10-31.json",
        "page.css",
        "page.js",
    ]


def test_build_removes_publish_day_files_of_deleted_days(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    write_day(root / "data" / "days", "2026-09-30", [make_item(id="old-item", story_urls=["https://alphasignal.ai/news/old"])])
    publish_days_dir = root / "work" / "publish" / "days"
    publish_days_dir.mkdir(parents=True)
    (publish_days_dir / "2026-09-01.json").write_text("{}", encoding="utf-8")
    (publish_days_dir / "notes.txt").write_text("keep", encoding="utf-8")
    build.build(root, TODAY)
    assert not (publish_days_dir / "2026-09-01.json").exists()
    assert not (publish_days_dir / "2026-09-30.json").exists()
    assert (publish_days_dir / "notes.txt").exists()
    listed = json.loads((root / "work" / "publish" / "files.json").read_text(encoding="utf-8"))
    assert listed == ["bookmarks.json", "days/2026-10-31.json", "page.css", "page.js"]


def test_build_deletes_days_older_than_30(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-09-30", [make_item()])
    summary = build.build(root, TODAY)
    assert not (root / "data" / "days" / "2026-09-30.json").exists()
    assert "deleted days: 2026-09-30" in summary


def test_build_deletes_work_folders_and_log_lines_older_than_30_days(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    for name in ("2026-09-30", "2026-10-01"):
        (root / "work" / name).mkdir(parents=True)
    (root / "data" / "runs.log").write_text("2026-09-30 08:05 old line\n2026-10-31 08:05 new line\n", encoding="utf-8")
    summary = build.build(root, TODAY)
    assert not (root / "work" / "2026-09-30").exists()
    assert (root / "work" / "2026-10-01").exists()
    assert (root / "work" / "publish" / "ai-news.html").exists()
    assert "deleted work folders: 2026-09-30" in summary
    assert (root / "data" / "runs.log").read_text(encoding="utf-8") == "2026-10-31 08:05 new line\n"


def test_build_with_a_broken_day_file_leaves_the_page_untouched(tmp_path):
    root = make_root(tmp_path)
    (root / "ai-news.html").write_text("previous page", encoding="utf-8")
    (root / "work" / "publish").mkdir(parents=True)
    (root / "work" / "publish" / "files.json").write_text('["old.json"]', encoding="utf-8")
    write_day(root / "data" / "days", "2026-10-31", [make_item(type="rumour")])
    with pytest.raises(store.DataError):
        build.build(root, TODAY)
    assert (root / "ai-news.html").read_text(encoding="utf-8") == "previous page"
    assert (root / "work" / "publish" / "files.json").read_text(encoding="utf-8") == '["old.json"]'


def test_build_main_reports_failure_with_exit_code_1(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item(type="rumour")])
    monkeypatch.setattr("sys.argv", ["build.py", "--root", str(root), "--today", "2026-10-20"])
    assert build.main() == 1
    assert "page left unchanged" in capsys.readouterr().err
    assert "build stopped" in (root / "data" / "runs.log").read_text(encoding="utf-8")


def make_old_data(root):
    """An old day file, an old work folder and an old log line: everything retention would delete."""
    write_day(root / "data" / "days", "2026-09-01", [make_item(id="old-item", story_urls=["https://alphasignal.ai/news/old"])])
    (root / "work" / "2026-09-01").mkdir(parents=True)
    (root / "data" / "runs.log").write_text("2026-09-01 08:05 old line\n", encoding="utf-8")


def assert_old_data_survived(root):
    assert (root / "data" / "days" / "2026-09-01.json").exists()
    assert (root / "work" / "2026-09-01").exists()
    assert (root / "data" / "runs.log").read_text(encoding="utf-8") == "2026-09-01 08:05 old line\n"


def test_build_with_a_broken_bookmarks_file_deletes_nothing(tmp_path):
    root = make_root(tmp_path)
    make_old_data(root)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    (root / "data" / "bookmarks.json").write_text(json.dumps([{"id": "no-fields"}]), encoding="utf-8")
    with pytest.raises(store.DataError):
        build.build(root, TODAY)
    assert_old_data_survived(root)


def test_build_with_a_broken_day_file_deletes_nothing(tmp_path):
    root = make_root(tmp_path)
    make_old_data(root)
    write_day(root / "data" / "days", "2026-10-31", [make_item(type="rumour")])
    with pytest.raises(store.DataError):
        build.build(root, TODAY)
    assert_old_data_survived(root)


def test_build_with_a_broken_import_file_changes_nothing(tmp_path):
    root = make_root(tmp_path)
    make_old_data(root)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    (root / "data" / "bookmarks.json").write_text("[]", encoding="utf-8")
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps([{"id": "no-fields"}]), encoding="utf-8")
    with pytest.raises(store.DataError):
        build.build(root, TODAY, export_path)
    assert_old_data_survived(root)
    assert (root / "data" / "bookmarks.json").read_text(encoding="utf-8") == "[]"


def test_build_imports_bookmarks_and_shows_them_on_the_page(tmp_path):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    bookmark = make_item(id="saved-item", bookmarked_at="2026-10-30T09:00:00Z")
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps([bookmark]), encoding="utf-8")
    summary = build.build(root, TODAY, export_path)
    assert summary.endswith("1 bookmarks")
    assert store.load_bookmarks(root / "data" / "bookmarks.json")[0]["id"] == "saved-item"


def test_build_main_rejects_a_today_far_in_the_future_and_deletes_nothing(tmp_path, monkeypatch, capsys):
    root = make_root(tmp_path)
    make_old_data(root)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    monkeypatch.setattr("sys.argv", ["build.py", "--root", str(root), "--today", "2999-01-01"])
    assert build.main() == 1
    assert "--today 2999-01-01 is in the future" in capsys.readouterr().err
    assert_old_data_survived(root)
    assert (root / "data" / "days" / "2026-10-31.json").exists()


def test_build_main_accepts_tomorrow_as_today(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    tomorrow = date.today() + timedelta(days=1)
    monkeypatch.setattr("sys.argv", ["build.py", "--root", str(root), "--today", tomorrow.isoformat()])
    assert build.main() == 0


def page_data(root):
    return embedded_news_data((root / "work" / "publish" / "ai-news.html").read_text(encoding="utf-8"))


def test_build_embeds_todays_failed_status_and_the_digest_time(tmp_path):
    root = make_root(tmp_path)
    (root / "config.toml").write_text('time = "07:30"\n', encoding="utf-8")
    store.append_run_log(root / "data" / "runs.log", f"failed: {TODAY}, the run did not finish (Claude Code may be closed)")
    build.build(root, TODAY)
    data = page_data(root)
    assert data["status"]["result"] == "failed"
    assert data["status"]["failedReason"].startswith("the run did not finish")
    assert data["digestTime"] == "07:30"


def test_build_embeds_published_zero_items(tmp_path):
    root = make_root(tmp_path)
    store.append_run_log(root / "data" / "runs.log", f"published: {TODAY}, 0 items, 0 batches")
    build.build(root, TODAY)
    data = page_data(root)
    assert data["status"]["result"] == "published"
    assert data["status"]["items"] == 0


def test_build_with_a_broken_config_still_renders_with_no_digest_time(tmp_path):
    root = make_root_with_broken_config(tmp_path)
    store.append_run_log(root / "data" / "runs.log", f"failed: {TODAY}, config: time is wrong")
    build.build(root, TODAY)
    data = page_data(root)
    assert data["digestTime"] is None
    assert data["status"]["result"] == "failed"


def test_build_uses_the_settings_it_is_given(tmp_path):
    root = make_root(tmp_path)
    settings = dict(config.DEFAULTS)
    settings["time"] = "06:15"
    build.build(root, TODAY, settings=settings)
    assert page_data(root)["digestTime"] == "06:15"


def test_page_template_has_no_fixed_time_or_place():
    script = (Path(__file__).resolve().parent.parent / "template" / "page.js").read_text(encoding="utf-8")
    assert "8:" + "17" not in script  # kept split so a plain-text search for the old fixed values stays clean
    assert "U" + "AE" not in script


@pytest.mark.parametrize("seen_text", ["{not json", "[]"])
def test_build_with_a_broken_seen_file_changes_nothing(tmp_path, seen_text):
    root = make_root(tmp_path)
    write_day(root / "data" / "days", "2026-10-31", [make_item()])
    (root / "data" / "seen.json").write_text(seen_text, encoding="utf-8")
    (root / "ai-news.html").write_text("previous page", encoding="utf-8")
    with pytest.raises(store.DataError, match="seen.json"):
        build.build(root, TODAY)
    assert (root / "ai-news.html").read_text(encoding="utf-8") == "previous page"
    assert not (root / "work" / "publish").exists()
    assert (root / "data" / "seen.json").read_text(encoding="utf-8") == seen_text
