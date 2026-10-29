import json
from datetime import date

import pytest

import assemble
from ainews import store
from tests.helpers import converted_browser_capture, make_item, raw_browser_capture, write_day

RUN_DATE = date(2026, 10, 5)
SOURCE_URL = "https://huggingface.co/Venastine-Research/Xing4.0-29B-A4B-GGUF"
SOURCE_TEXT = "Xing4.0-29B-A4B: 29B total parameters, 4B active per token, 256K native context. License: Apache-2.0. Kolibri-1 is an open German reasoning model."


def draft(title, story_url, **overrides):
    item = make_item(title=title, story_urls=[story_url], **overrides)
    del item["id"]
    return item


def write_batch(root, number, drafts):
    batch_path = root / "work" / "2026-10-05" / f"items-{number}.json"
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text(json.dumps(drafts), encoding="utf-8")


def collect_stories(root, story_urls, source_url=SOURCE_URL, source_text=SOURCE_TEXT):
    """Write stories.json and one saved source per story, the way collect.py leaves them."""
    work_dir = root / "work" / "2026-10-05"
    stories_path = work_dir / "stories.json"
    records = []
    if stories_path.exists():
        records = json.loads(stories_path.read_text(encoding="utf-8"))
    for story_url in story_urls:
        slug = story_url.rstrip("/").rsplit("/", 1)[-1]
        records.append({"slug": slug, "story_url": story_url})
        source_path = work_dir / "sources" / slug / "1-huggingface.txt"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(f"URL: {source_url}\n\n{source_text}\n", encoding="utf-8")
    stories_path.parent.mkdir(parents=True, exist_ok=True)
    stories_path.write_text(json.dumps(records), encoding="utf-8")


def day_ids(root):
    ids = []
    for item in store.load_day(root / "data" / "days" / "2026-10-05.json")["items"]:
        ids.append(item["id"])
    return ids


def day_file_exists(root):
    return (root / "data" / "days" / "2026-10-05.json").exists()


def test_assemble_assigns_ids_and_writes_the_day_file(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/f"], source_url="https://github.com/f/video",
                    source_text=SOURCE_TEXT + " Runs MiniMax H3 on 8 GB GPUs.")
    write_batch(tmp_path, 1, [draft("Kolibri-1: open German reasoning model", "https://alphasignal.ai/news/k")])
    write_batch(tmp_path, 2, [draft("FreeVideo runs MiniMax H3 on 8 GB GPUs", "https://alphasignal.ai/news/f",
                                    sources=[{"label": "GitHub", "url": "https://github.com/f/video/"}])])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert day_ids(tmp_path) == ["kolibri-1-open-german-reasoning-model", "freevideo-runs-minimax-h3-on-8-gb-gpus"]
    assert len(added_items) == 2
    assert skipped_titles == []


def test_assemble_names_an_item_without_letters_in_its_title_item(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/new"])
    write_batch(tmp_path, 1, [draft("!!!", "https://alphasignal.ai/news/new")])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert added_items[0]["id"] == "item"


def test_assemble_adds_a_suffix_when_the_id_is_used_on_an_earlier_day(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(
        id="kolibri-1", story_urls=["https://alphasignal.ai/news/old"],
        sources=[{"label": "GitHub", "url": "https://github.com/other/repo"}],
    )])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/new"])
    write_batch(tmp_path, 1, [draft("Kolibri 1", "https://alphasignal.ai/news/new")])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert added_items[0]["id"] == "kolibri-1-2"


def test_assemble_rerun_on_same_day_does_not_duplicate(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k")])
    assemble.assemble(tmp_path, RUN_DATE)
    collect_stories(tmp_path, ["https://alphasignal.ai/news/f"], source_url="https://github.com/f/video")
    write_batch(tmp_path, 2, [draft("FreeVideo", "https://alphasignal.ai/news/f",
                                    sources=[{"label": "GitHub", "url": "https://github.com/f/video"}])])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert len(added_items) == 1
    assert added_items[0]["title"] == "FreeVideo"
    assert skipped_titles == ["Kolibri-1"]
    assert day_ids(tmp_path) == ["kolibri-1", "freevideo"]


def test_assemble_with_an_invalid_item_writes_nothing(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/b"])
    write_batch(tmp_path, 1, [draft("Bad item", "https://alphasignal.ai/news/b", type="rumour")])
    with pytest.raises(store.DataError, match="items-1.json item 1"):
        assemble.assemble(tmp_path, RUN_DATE)
    assert not day_file_exists(tmp_path)


def test_assemble_never_reuses_the_id_of_a_saved_bookmark(tmp_path):
    bookmarks_path = tmp_path / "data" / "bookmarks.json"
    bookmarks_path.parent.mkdir(parents=True, exist_ok=True)
    bookmarks_path.write_text(
        json.dumps([make_item(id="kolibri-1", bookmarked_at="2026-08-01T08:00:00Z")]),
        encoding="utf-8",
    )
    collect_stories(tmp_path, ["https://alphasignal.ai/news/new"])
    write_batch(tmp_path, 1, [draft("Kolibri 1", "https://alphasignal.ai/news/new")])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert added_items[0]["id"] == "kolibri-1-2"


def test_assemble_reports_a_broken_items_file_by_name(tmp_path):
    batch_path = tmp_path / "work" / "2026-10-05" / "items-1.json"
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text('[{"title": "12" monitor"}]', encoding="utf-8")
    with pytest.raises(store.DataError, match="items-1.json: not valid JSON"):
        assemble.assemble(tmp_path, RUN_DATE)


def test_assemble_rejects_a_source_link_that_was_never_read(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    invented_source = {"label": "GitHub", "url": "https://github.com/made-up/repo"}
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", sources=[invented_source])])
    with pytest.raises(store.DataError, match="items-1.json item 1: cites a source that was not read.*made-up/repo"):
        assemble.assemble(tmp_path, RUN_DATE)
    assert not day_file_exists(tmp_path)


def test_assemble_accepts_sources_read_in_a_browser(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    browser_path = tmp_path / "work" / "2026-10-05" / "sources" / "k" / "2-browser.txt"
    browser_path.write_text(converted_browser_capture("https://openai.com/index/post/"), encoding="utf-8")
    sources = [{"label": "Hugging Face", "url": SOURCE_URL}, {"label": "OpenAI", "url": "https://openai.com/index/post"}]
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", sources=sources)])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert len(added_items) == 1
    assert warnings == []


def test_assemble_rejects_a_source_that_is_only_a_raw_browser_capture(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    browser_path = tmp_path / "work" / "2026-10-05" / "sources" / "k" / "2-browser.txt"
    browser_path.write_text(raw_browser_capture("https://openai.com/index/post/"), encoding="utf-8")
    sources = [{"label": "Hugging Face", "url": SOURCE_URL}, {"label": "OpenAI", "url": "https://openai.com/index/post"}]
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", sources=sources)])
    with pytest.raises(store.DataError, match="cites a source that was not read"):
        assemble.assemble(tmp_path, RUN_DATE)


def test_assemble_rejects_an_item_for_a_story_not_collected_today(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/elsewhere")])
    with pytest.raises(store.DataError, match="not a story collected today"):
        assemble.assemble(tmp_path, RUN_DATE)


def test_assemble_pauses_for_a_number_missing_from_the_sources(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    bullets = ["29B total / 4B active per token (MoE)", "256K native context", "Runs at 176 tok/s"]
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", bullets=bullets)])
    with pytest.raises(assemble.ReviewNeeded) as review:
        assemble.assemble(tmp_path, RUN_DATE)
    assert review.value.warnings == ["items-1.json item 1: number 176 is not in the sources: Runs at 176 tok/s"]
    assert not day_file_exists(tmp_path)


def test_assemble_publishes_with_accepted_warnings(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    bullets = ["29B total / 4B active per token (MoE)", "256K native context", "Runs at 176 tok/s"]
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", bullets=bullets)])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE, accept_warnings=True)
    assert day_ids(tmp_path) == ["kolibri-1"]
    assert len(warnings) == 1


def test_assemble_pauses_for_a_likely_repeat_of_an_earlier_day(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(
        id="xing-old", title="Xing4.0 released", story_urls=["https://alphasignal.ai/news/old"],
    )])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/again"])
    write_batch(tmp_path, 1, [draft("Xing4.0 again", "https://alphasignal.ai/news/again")])
    with pytest.raises(assemble.ReviewNeeded) as review:
        assemble.assemble(tmp_path, RUN_DATE)
    assert review.value.warnings == [
        f'items-1.json item 1: may repeat 2026-10-04 item "Xing4.0 released" (both cite {SOURCE_URL})'
    ]


def test_assemble_does_not_call_a_shared_front_page_a_repeat(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(
        id="tiny-old", story_urls=["https://alphasignal.ai/news/old"],
        sources=[{"label": "tinygrad", "url": "https://tinygrad.org/"}],
    )])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/new"], source_url="https://tinygrad.org/")
    write_batch(tmp_path, 1, [draft("tinybox update", "https://alphasignal.ai/news/new",
                                    sources=[{"label": "tinygrad", "url": "https://tinygrad.org/"}])])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert warnings == []


def write_batches_file(root, batches):
    batches_path = root / "work" / "2026-10-05" / "batches.json"
    batches_path.parent.mkdir(parents=True, exist_ok=True)
    batches_path.write_text(json.dumps(batches), encoding="utf-8")


def test_mark_passed_over_records_summarized_stories_that_got_no_item(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k", "https://alphasignal.ai/news/f", "https://alphasignal.ai/news/g"])
    write_batches_file(tmp_path, [["k", "f"], ["g"]])
    write_batch(tmp_path, 1, [draft("Kolibri-1: open German reasoning model", "https://alphasignal.ai/news/k")])
    assemble.assemble(tmp_path, RUN_DATE)
    passed_over = assemble.mark_passed_over(tmp_path, RUN_DATE)
    assert passed_over == ["https://alphasignal.ai/news/f"]
    seen = store.load_seen(tmp_path / "data" / "seen.json")
    assert seen == {"https://alphasignal.ai/news/f": "2026-10-05"}


def test_mark_passed_over_leaves_stories_merged_into_another_item(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k", "https://alphasignal.ai/news/f"])
    write_batches_file(tmp_path, [["k", "f"]])
    merged_item = draft("Kolibri-1: open German reasoning model", "https://alphasignal.ai/news/k")
    merged_item["story_urls"].append("https://alphasignal.ai/news/f")
    write_batch(tmp_path, 1, [merged_item])
    assemble.assemble(tmp_path, RUN_DATE)
    assert assemble.mark_passed_over(tmp_path, RUN_DATE) == []


def test_mark_passed_over_without_a_batches_file_does_nothing(tmp_path):
    assert assemble.mark_passed_over(tmp_path, RUN_DATE) == []
    assert not (tmp_path / "data" / "seen.json").exists()


def pr_check(pitch):
    return {
        "why": "vendor's own description",
        "holds_up": "A 29B MoE with 4B active per token.",
        "notes": [{"kind": "careful", "text": "256K is the native context, not a tested limit."}],
        "pitch": pitch,
    }


def test_assemble_publishes_a_pr_check_whose_pitch_is_quoted_from_the_sources(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", pr_check=pr_check("4B active per token"))])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert warnings == []
    assert added_items[0]["pr_check"]["pitch"] == "4B active per token"


def test_assemble_pauses_for_a_pitch_that_is_not_in_the_sources(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    write_batch(tmp_path, 1, [draft("Kolibri-1", "https://alphasignal.ai/news/k", pr_check=pr_check("the best model ever made"))])
    with pytest.raises(assemble.ReviewNeeded) as review:
        assemble.assemble(tmp_path, RUN_DATE)
    assert review.value.warnings == [
        'items-1.json item 1: the PR check\'s pitch is not quoted word for word from the sources: "the best model ever made"'
    ]


def merged_draft_of_a_published_and_a_new_story():
    merged_item = draft("Kolibri-1: open German reasoning model", "https://alphasignal.ai/news/old")
    merged_item["story_urls"].append("https://alphasignal.ai/news/new")
    return merged_item


def test_assemble_warns_when_an_item_mixes_a_published_story_with_a_new_one(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(story_urls=["https://alphasignal.ai/news/old"])])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/old", "https://alphasignal.ai/news/new"])
    write_batch(tmp_path, 1, [merged_draft_of_a_published_and_a_new_story()])
    with pytest.raises(assemble.ReviewNeeded) as review:
        assemble.assemble(tmp_path, RUN_DATE)
    assert len(review.value.warnings) == 1
    assert "Kolibri-1: open German reasoning model" in review.value.warnings[0]
    assert "https://alphasignal.ai/news/old" in review.value.warnings[0]
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE, accept_warnings=True)
    assert added_items == []
    assert skipped_titles == []
    assert len(warnings) == 1


def test_assemble_skips_silently_when_every_story_of_an_item_was_published(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(
        story_urls=["https://alphasignal.ai/news/old", "https://alphasignal.ai/news/older"],
    )])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/old", "https://alphasignal.ai/news/older"])
    merged_item = merged_draft_of_a_published_and_a_new_story()
    merged_item["story_urls"][1] = "https://alphasignal.ai/news/older"
    write_batch(tmp_path, 1, [merged_item])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert added_items == []
    assert skipped_titles == ["Kolibri-1: open German reasoning model"]
    assert warnings == []


def test_mark_passed_over_leaves_the_new_story_of_an_item_skipped_for_a_published_one(tmp_path):
    write_day(tmp_path / "data" / "days", "2026-10-04", [make_item(story_urls=["https://alphasignal.ai/news/old"])])
    collect_stories(tmp_path, ["https://alphasignal.ai/news/old", "https://alphasignal.ai/news/new"])
    write_batches_file(tmp_path, [["old", "new"]])
    write_batch(tmp_path, 1, [merged_draft_of_a_published_and_a_new_story()])
    assemble.assemble(tmp_path, RUN_DATE, accept_warnings=True)
    assert assemble.mark_passed_over(tmp_path, RUN_DATE) == []
    assert not (tmp_path / "data" / "seen.json").exists()


def test_assemble_ignores_an_items_file_beyond_the_current_batches(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k"])
    write_batches_file(tmp_path, [["k"]])
    write_batch(tmp_path, 1, [draft("Kolibri-1: open German reasoning model", "https://alphasignal.ai/news/k")])
    write_batch(tmp_path, 2, [draft("Stale item", "https://alphasignal.ai/news/never-collected")])
    added_items, skipped_titles, warnings = assemble.assemble(tmp_path, RUN_DATE)
    assert len(added_items) == 1
    assert added_items[0]["title"] == "Kolibri-1: open German reasoning model"


def test_mark_passed_over_ignores_the_drafts_of_an_items_file_beyond_the_current_batches(tmp_path):
    collect_stories(tmp_path, ["https://alphasignal.ai/news/k", "https://alphasignal.ai/news/f"])
    write_batches_file(tmp_path, [["k", "f"]])
    write_batch(tmp_path, 1, [draft("Kolibri-1: open German reasoning model", "https://alphasignal.ai/news/k")])
    write_batch(tmp_path, 2, [draft("Stale item", "https://alphasignal.ai/news/f")])
    assemble.assemble(tmp_path, RUN_DATE)
    assert assemble.mark_passed_over(tmp_path, RUN_DATE) == ["https://alphasignal.ai/news/f"]
