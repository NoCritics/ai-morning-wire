import json
import threading
import time
from datetime import date
from pathlib import Path

import pytest

import summarize
from ainews import store
from tests.helpers import converted_browser_capture, raw_browser_capture

RUN_DATE = date(2026, 10, 9)
GOOD_REPLY = (
    '```json\n[{"title": "A", "type": "tooling", "bullets": ["1", "2", "3"], "sources": [], '
    '"published": "2026-10-08", "story_urls": ["https://alphasignal.ai/news/a"]}]\n```\nA\n'
)


def write_stories(root):
    work_dir = root / "work" / "2026-10-09"
    stories = []
    for slug, has_file in (("a", True), ("b", True), ("c", False)):
        source_file = None
        if has_file:
            source_file = f"work/2026-10-09/sources/{slug}/1-web.txt"
            source_path = root / source_file
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(f"URL: https://{slug}.com/post\n\ntext\n", encoding="utf-8")
        stories.append({
            "slug": slug,
            "story_url": f"https://alphasignal.ai/news/{slug}",
            "via": "AlphaSignal",
            "headline": f"Headline {slug}",
            "published": "2026-10-08",
            "sources": [{"url": f"https://{slug}.com/post", "file": source_file}],
        })
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "stories.json").write_text(json.dumps(stories), encoding="utf-8")
    return work_dir


def test_batch_prompt_lists_collector_files_and_browser_captures(tmp_path):
    work_dir = write_stories(tmp_path)
    (work_dir / "sources" / "a" / "2-browser.txt").write_text(converted_browser_capture("https://a.com/x"), encoding="utf-8")
    stories = store.read_json(work_dir / "stories.json")
    prompt = summarize.batch_prompt(work_dir, [stories[0]])
    assert prompt.startswith(
        "Stories:\n- slug: a\n  found via: AlphaSignal\n  headline: Headline a\n"
        "  story_url: https://alphasignal.ai/news/a\n  published: 2026-10-08\n"
    )
    assert "1-web.txt" in prompt
    assert "2-browser.txt" in prompt


def test_items_from_reply_reads_the_json_block():
    items = summarize.items_from_reply(GOOD_REPLY)
    assert items[0]["title"] == "A"


@pytest.mark.parametrize(
    "reply, message",
    [
        ("No block here", "no ```json block"),
        ("```json\n{broken\n```", "not valid JSON"),
        ('```json\n{"title": "A"}\n```', "not a JSON array"),
    ],
)
def test_items_from_reply_rejects_bad_replies(reply, message):
    with pytest.raises(summarize.BatchError, match=message):
        summarize.items_from_reply(reply)


def test_summarize_writes_items_and_replies_per_batch(tmp_path):
    work_dir = write_stories(tmp_path)
    prompts = []

    def fake_agent(root, prompt):
        prompts.append(prompt)
        return GOOD_REPLY

    results, failures, forgotten = summarize.summarize(tmp_path, RUN_DATE, [["a"], ["b"]], agent_runner=fake_agent)
    assert failures == []
    assert forgotten == []
    assert sorted(prompts) == sorted([
        summarize.batch_prompt(work_dir, [store.read_json(work_dir / "stories.json")[0]]),
        summarize.batch_prompt(work_dir, [store.read_json(work_dir / "stories.json")[1]]),
    ])
    assert json.loads((work_dir / "items-2.json").read_text(encoding="utf-8"))[0]["title"] == "A"
    assert (work_dir / "replies" / "reply-1.md").read_text(encoding="utf-8") == GOOD_REPLY


def test_summarize_reports_a_failed_batch_and_keeps_the_others(tmp_path):
    work_dir = write_stories(tmp_path)

    def flaky_agent(root, prompt):
        if "slug: b" in prompt:
            return "Sorry, no JSON today."
        return GOOD_REPLY

    results, failures, forgotten = summarize.summarize(tmp_path, RUN_DATE, [["a"], ["b"]], agent_runner=flaky_agent)
    assert [result[0] for result in results] == [1]
    assert failures == [(2, "the reply has no ```json block")]
    assert (work_dir / "items-1.json").exists()
    assert not (work_dir / "items-2.json").exists()
    assert (work_dir / "replies" / "reply-2.md").exists()


def test_summarize_only_reruns_the_named_batches(tmp_path):
    write_stories(tmp_path)
    prompts = []

    def fake_agent(root, prompt):
        prompts.append(prompt)
        return GOOD_REPLY

    summarize.summarize(tmp_path, RUN_DATE, [["a"], ["b"]], only_numbers=[2], agent_runner=fake_agent)
    assert len(prompts) == 1
    assert "slug: b" in prompts[0]


def test_summarize_warns_about_a_readable_story_left_out_of_every_batch(tmp_path):
    write_stories(tmp_path)
    results, failures, forgotten = summarize.summarize(tmp_path, RUN_DATE, [["a"]], agent_runner=lambda root, prompt: GOOD_REPLY)
    assert forgotten == ["b"]


@pytest.mark.parametrize(
    "batches, message",
    [
        ([["a"], ["zzz"]], "zzz is not in stories.json"),
        ([["a"], ["a"]], "a is in more than one batch"),
    ],
)
def test_summarize_rejects_bad_batches(tmp_path, batches, message):
    write_stories(tmp_path)
    with pytest.raises(store.DataError, match=message):
        summarize.summarize(tmp_path, RUN_DATE, batches, agent_runner=lambda root, prompt: GOOD_REPLY)


def test_story_source_paths_are_absolute_and_sorted_by_name(tmp_path):
    work_dir = write_stories(tmp_path)
    story_dir = work_dir / "sources" / "a"
    story_dir.joinpath("3-webfetch.txt").write_text("URL: https://a.com/f\n\ntext\n", encoding="utf-8")
    story_dir.joinpath("2-browser.txt").write_text(converted_browser_capture("https://a.com/x"), encoding="utf-8")
    story = store.read_json(work_dir / "stories.json")[0]
    paths = summarize.story_source_paths(work_dir, story)
    names = []
    for path in paths:
        assert Path(path).is_absolute()
        names.append(Path(path).name)
    assert names == ["1-web.txt", "2-browser.txt", "3-webfetch.txt"]


def test_batch_prompt_leaves_out_raw_and_bot_check_browser_captures(tmp_path):
    work_dir = write_stories(tmp_path)
    story_dir = work_dir / "sources" / "a"
    story_dir.joinpath("2-browser.txt").write_text(raw_browser_capture("https://a.com/x"), encoding="utf-8")
    challenge_text = "Just a moment... Verify you are human by completing the action below. " * 4
    story_dir.joinpath("3-browser.txt").write_text(converted_browser_capture("https://a.com/y", challenge_text), encoding="utf-8")
    story_dir.joinpath("4-browser.txt").write_text(converted_browser_capture("https://a.com/z"), encoding="utf-8")
    stories = store.read_json(work_dir / "stories.json")
    prompt = summarize.batch_prompt(work_dir, [stories[0]])
    assert "2-browser.txt" not in prompt
    assert "3-browser.txt" not in prompt
    assert "4-browser.txt" in prompt


def test_summarize_deletes_the_old_items_file_of_a_batch_that_fails_again(tmp_path):
    work_dir = write_stories(tmp_path)
    (work_dir / "items-1.json").write_text("[]", encoding="utf-8")
    results, failures, forgotten = summarize.summarize(
        tmp_path, RUN_DATE, [["a"], ["b"]], only_numbers=[1], agent_runner=lambda root, prompt: "No JSON today.",
    )
    assert [failure[0] for failure in failures] == [1]
    assert not (work_dir / "items-1.json").exists()


def test_summarize_deletes_items_files_beyond_the_current_batches(tmp_path):
    work_dir = write_stories(tmp_path)
    (work_dir / "items-3.json").write_text("[]", encoding="utf-8")
    (work_dir / "items-10.json").write_text("[]", encoding="utf-8")
    summarize.summarize(tmp_path, RUN_DATE, [["a"], ["b"]], agent_runner=lambda root, prompt: GOOD_REPLY)
    assert (work_dir / "items-2.json").exists()
    assert not (work_dir / "items-3.json").exists()
    assert not (work_dir / "items-10.json").exists()


def test_summarize_with_only_keeps_the_other_items_files(tmp_path):
    work_dir = write_stories(tmp_path)
    (work_dir / "items-3.json").write_text("[]", encoding="utf-8")
    summarize.summarize(tmp_path, RUN_DATE, [["a"], ["b"]], only_numbers=[1], agent_runner=lambda root, prompt: GOOD_REPLY)
    assert (work_dir / "items-3.json").exists()


def test_summarize_runs_at_most_three_batches_at_once(tmp_path):
    work_dir = tmp_path / "work" / "2026-10-09"
    work_dir.mkdir(parents=True)
    stories = []
    batch_slugs = []
    for number in range(7):
        stories.append({"slug": f"s{number}", "story_url": f"https://alphasignal.ai/news/s{number}", "via": "AlphaSignal",
                        "headline": "H", "published": "2026-10-08", "sources": []})
        batch_slugs.append([f"s{number}"])
    (work_dir / "stories.json").write_text(json.dumps(stories), encoding="utf-8")
    lock = threading.Lock()
    running = {"now": 0, "most": 0}

    def counting_agent(root, prompt):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        time.sleep(0.05)
        with lock:
            running["now"] -= 1
        return GOOD_REPLY

    results, failures, forgotten = summarize.summarize(tmp_path, RUN_DATE, batch_slugs, agent_runner=counting_agent)
    assert len(results) == 7
    assert running["most"] == summarize.MAX_PARALLEL_BATCHES == 3
