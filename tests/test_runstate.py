import subprocess
import sys
from datetime import date, datetime, time
from pathlib import Path

from ainews import runstate
from tests.helpers import embedded_news_data, make_root_with_broken_config, make_template

ROOT = Path(__file__).resolve().parent.parent
DAY = date(2026, 10, 28)
SCHEDULED = time(8, 0)


def status_and_action(log_text, now, day=DAY):
    status = runstate.day_status(log_text, day)
    return status, runstate.next_action(status, now, SCHEDULED)[0]


def test_next_action_before_the_scheduled_time_is_nothing():
    assert status_and_action("", datetime(2026, 10, 28, 7, 10))[1] == "nothing"


def test_next_action_after_the_time_with_no_run_today_is_launch():
    log_text = "2026-10-27 08:00 started: 2026-10-27\n"
    assert status_and_action(log_text, datetime(2026, 10, 28, 14, 0))[1] == "launch"


def test_next_action_while_a_run_is_young_is_nothing():
    log_text = "2026-10-28 08:00 started: 2026-10-28\n"
    assert status_and_action(log_text, datetime(2026, 10, 28, 9, 0))[1] == "nothing"


def test_next_action_marks_a_silent_run_failed_after_90_minutes_from_its_start():
    log_text = "2026-10-28 14:05 started: 2026-10-28\n"
    assert status_and_action(log_text, datetime(2026, 10, 28, 15, 0))[1] == "nothing"
    assert status_and_action(log_text, datetime(2026, 10, 28, 15, 36))[1] == "mark-failed"


def test_next_action_after_published_is_nothing():
    log_text = (
        "2026-10-28 08:00 started: 2026-10-28\n"
        "2026-10-28 08:20 published: 2026-10-28, 10 items, 3 batches\n"
    )
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 12, 0))
    assert action == "nothing"
    assert status["result"] == "published"
    assert status["items"] == 10


def test_next_action_after_a_manual_run_without_started_is_nothing():
    log_text = "2026-10-28 07:00 published: 2026-10-28, 12 items, 4 batches\n"
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 8, 0))
    assert action == "nothing"
    assert status["published_any"] is True
    assert status["items"] == 12


def test_a_run_crossing_midnight_logs_its_own_day():
    log_text = (
        "2026-10-28 23:30 started: 2026-10-28\n"
        "2026-10-29 00:20 published: 2026-10-28, 10 items, 3 batches\n"
    )
    assert runstate.day_status(log_text, DAY)["result"] == "published"
    next_day = date(2026, 10, 29)
    status, action = status_and_action(log_text, datetime(2026, 10, 29, 8, 30), next_day)
    assert status["result"] is None
    assert action == "launch"


def test_next_action_retries_once_after_every_source_unreadable():
    log_text = (
        "2026-10-28 08:00 started: 2026-10-28\n"
        "2026-10-28 08:10 failed: 2026-10-28, every enabled source unreadable (3 of 3)\n"
    )
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 8, 15))
    assert action == "retry"
    assert status["every_source_unreadable"] is True
    log_text += (
        "2026-10-28 08:20 retry: 2026-10-28\n"
        "2026-10-28 08:30 failed: 2026-10-28, every enabled source unreadable (3 of 3)\n"
    )
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 8, 35))
    assert status["retried"] is True
    assert action == "nothing"


def test_next_action_does_not_retry_other_failures():
    log_text = (
        "2026-10-28 08:00 started: 2026-10-28\n"
        "2026-10-28 08:10 failed: 2026-10-28, assemble stopped\n"
    )
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 8, 15))
    assert action == "nothing"
    assert status["failed_reason"] == "assemble stopped"


def test_day_status_ignores_old_format_and_unknown_lines():
    log_text = (
        "2026-10-28 08:00 published: 30 items\n"
        "2026-10-28 08:01 collect failed, AlphaSignal feed unreadable: x\n"
        "2026-10-28 08:02 built: 7 feed days\n"
        "garbage\n"
        "2026-10-28 08:03 started: not-a-date\n"
    )
    status = runstate.day_status(log_text, DAY)
    assert status["started_at"] is None
    assert status["result"] is None
    assert status["published_any"] is False


def test_an_old_format_published_line_without_batches_counts_as_published():
    log_text = "2026-10-28 08:27 published: 2026-10-28, 33 items\n"
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 9, 0))
    assert action == "nothing"
    assert status["published_any"] is True
    assert status["result"] == "published"
    assert status["items"] == 33


def message_of(log_line):
    """The text after the 'YYYY-MM-DD HH:MM ' prefix."""
    return log_line.split(" ", 2)[2]


def run_cli(root, *arguments):
    return subprocess.run(
        [sys.executable, str(ROOT / "runstate.py"), "--root", str(root), *arguments],
        capture_output=True,
        text=True,
    )


def test_cli_next_action_with_a_broken_config_logs_one_failed_line(tmp_path):
    make_root_with_broken_config(tmp_path)
    for _ in range(2):
        completed = run_cli(tmp_path, "next-action")
        assert completed.returncode == 3
        assert completed.stdout.strip() == "nothing: config error"
    lines = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert f"failed: {date.today()}, config:" in lines[0]
    assert "time" in lines[0]


def test_cli_published_and_mark_failed_write_the_exact_lines(tmp_path):
    make_template(tmp_path / "template")
    assert run_cli(tmp_path, "started").returncode == 0
    assert run_cli(tmp_path, "published", "2026-10-27", "10", "3").returncode == 0
    assert run_cli(tmp_path, "failed", "2026-10-27", "assemble stopped").returncode == 0
    assert run_cli(tmp_path, "failed", "not-a-date", "x").returncode == 2
    assert run_cli(tmp_path, "mark-failed").returncode == 0
    lines = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8").splitlines()
    messages = []
    for line in lines:
        messages.append(message_of(line))
    assert messages[0] == f"started: {date.today()}"
    assert messages[1] == "published: 2026-10-27, 10 items, 3 batches"
    assert messages[2] == "failed: 2026-10-27, assemble stopped"
    assert messages[3] == (
        f"failed: {date.today()}, the run did not finish "
        "(Claude Code may be logged out, out of usage, closed or waiting for input)"
    )
    assert (tmp_path / "ai-news.html").exists()


def test_next_action_marks_failed_exactly_90_minutes_after_the_start():
    log_text = "2026-10-28 14:05 started: 2026-10-28\n"
    assert status_and_action(log_text, datetime(2026, 10, 28, 15, 34))[1] == "nothing"
    assert status_and_action(log_text, datetime(2026, 10, 28, 15, 35))[1] == "mark-failed"


def test_a_retry_in_the_same_minute_as_its_failure_is_in_progress():
    log_text = (
        "2026-10-28 08:00 started: 2026-10-28\n"
        "2026-10-28 08:10 failed: 2026-10-28, every enabled source unreadable (3 of 3)\n"
        "2026-10-28 08:10 retry: 2026-10-28\n"
    )
    status, action = status_and_action(log_text, datetime(2026, 10, 28, 8, 20))
    assert status["result"] is None
    assert action == "nothing"


def test_find_open_earlier_day_returns_a_day_whose_run_never_finished():
    log_text = (
        "2026-10-26 08:00 started: 2026-10-26\n"
        "2026-10-26 08:20 published: 2026-10-26, 10 items, 3 batches\n"
        "2026-10-27 14:05 started: 2026-10-27\n"
    )
    assert runstate.find_open_earlier_day(log_text, datetime(2026, 10, 28, 8, 0)) == date(2026, 10, 27)


def test_find_open_earlier_day_leaves_a_young_run_across_midnight_alone():
    log_text = "2026-10-27 23:50 started: 2026-10-27\n"
    assert runstate.find_open_earlier_day(log_text, datetime(2026, 10, 28, 0, 30)) is None
    assert runstate.find_open_earlier_day(log_text, datetime(2026, 10, 28, 1, 20)) == date(2026, 10, 27)


def test_find_open_earlier_day_ignores_finished_and_published_days():
    log_text = (
        "2026-10-26 08:00 started: 2026-10-26\n"
        "2026-10-26 08:10 failed: 2026-10-26, assemble stopped\n"
        "2026-10-27 08:00 started: 2026-10-27\n"
        "2026-10-27 08:20 published: 2026-10-27, 10 items, 3 batches\n"
        "2026-10-28 08:00 started: 2026-10-28\n"
    )
    assert runstate.find_open_earlier_day(log_text, datetime(2026, 10, 28, 12, 0)) is None


def test_cli_next_action_closes_an_earlier_open_day(tmp_path):
    log_path = tmp_path / "data" / "runs.log"
    log_path.parent.mkdir()
    log_path.write_text("2026-01-01 08:00 started: 2026-01-01\n", encoding="utf-8")
    completed = run_cli(tmp_path, "next-action")
    assert completed.returncode == 0
    messages = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        messages.append(message_of(line))
    assert messages[1] == (
        "failed: 2026-01-01, the run did not finish "
        "(Claude Code may be logged out, out of usage, closed or waiting for input)"
    )


def test_cli_closes_an_earlier_open_day_even_with_a_broken_config(tmp_path):
    make_root_with_broken_config(tmp_path)
    log_path = tmp_path / "data" / "runs.log"
    log_path.parent.mkdir()
    log_path.write_text("2026-01-01 08:00 started: 2026-01-01\n", encoding="utf-8")
    completed = run_cli(tmp_path, "next-action")
    assert completed.returncode == 3
    text = log_path.read_text(encoding="utf-8")
    assert "failed: 2026-01-01, the run did not finish" in text
    assert f"failed: {date.today()}, config:" in text


def test_cli_config_failure_is_not_repeated_after_a_launch_in_between(tmp_path):
    make_root_with_broken_config(tmp_path)
    assert run_cli(tmp_path, "next-action").returncode == 3
    assert run_cli(tmp_path, "started").returncode == 0
    assert run_cli(tmp_path, "failed", str(date.today()), "assemble stopped").returncode == 0
    assert run_cli(tmp_path, "next-action").returncode == 3
    text = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8")
    assert text.count(", config:") == 1


def test_cli_mark_failed_logs_a_stopped_build(tmp_path):
    make_template(tmp_path / "template")
    days_dir = tmp_path / "data" / "days"
    days_dir.mkdir(parents=True)
    (days_dir / "2026-10-27.json").write_text("not json", encoding="utf-8")
    completed = run_cli(tmp_path, "mark-failed")
    assert completed.returncode == 1
    assert completed.stderr.strip() != ""
    lines = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8").splitlines()
    assert message_of(lines[0]).startswith(f"failed: {date.today()}, the run did not finish")
    assert message_of(lines[1]).startswith("build stopped: ")


def test_cli_next_action_with_a_broken_config_rebuilds_the_page_with_the_failure(tmp_path):
    make_root_with_broken_config(tmp_path)
    assert run_cli(tmp_path, "next-action").returncode == 3
    page = (tmp_path / "work" / "publish" / "ai-news.html").read_text(encoding="utf-8")
    data = embedded_news_data(page)
    assert (tmp_path / "ai-news.html").exists()
    assert data["status"]["result"] == "failed"
    assert data["status"]["failedReason"].startswith("config:")
    assert data["digestTime"] is None


def test_cli_next_action_with_a_broken_config_and_a_broken_build_stays_exit_3(tmp_path):
    make_root_with_broken_config(tmp_path)
    days_dir = tmp_path / "data" / "days"
    days_dir.mkdir(parents=True)
    (days_dir / "2026-10-27.json").write_text("not json", encoding="utf-8")
    completed = run_cli(tmp_path, "next-action")
    assert completed.returncode == 3
    assert completed.stderr.strip() != ""
    text = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8")
    assert "build stopped: " in text


def test_cli_next_action_on_a_published_day_ignores_a_broken_config(tmp_path):
    make_root_with_broken_config(tmp_path)
    log_path = tmp_path / "data" / "runs.log"
    log_path.parent.mkdir()
    log_text = f"{date.today()} 08:00 published: {date.today()}, 33 items\n"
    log_path.write_text(log_text, encoding="utf-8")
    completed = run_cli(tmp_path, "next-action")
    assert completed.returncode == 0
    assert completed.stdout.startswith("nothing: today's digest is published")
    assert log_path.read_text(encoding="utf-8") == log_text
    assert not (tmp_path / "ai-news.html").exists()


def test_cli_next_action_during_a_run_in_progress_ignores_a_broken_config(tmp_path):
    make_root_with_broken_config(tmp_path)
    assert run_cli(tmp_path, "started").returncode == 0
    log_path = tmp_path / "data" / "runs.log"
    log_text = log_path.read_text(encoding="utf-8")
    completed = run_cli(tmp_path, "next-action")
    assert completed.returncode == 0
    assert completed.stdout.startswith("nothing: a run started at")
    assert log_path.read_text(encoding="utf-8") == log_text
    assert not (tmp_path / "ai-news.html").exists()


def test_cli_mark_failed_logs_a_build_that_cannot_write_the_page(tmp_path):
    make_template(tmp_path / "template")
    (tmp_path / "ai-news.html").mkdir()
    completed = run_cli(tmp_path, "mark-failed")
    assert completed.returncode == 1
    assert "Traceback" not in completed.stderr
    lines = (tmp_path / "data" / "runs.log").read_text(encoding="utf-8").splitlines()
    assert message_of(lines[1]).startswith("build stopped: ")


def test_cli_next_action_ignore_time_launches_before_the_scheduled_time(tmp_path):
    make_template(tmp_path / "template")
    (tmp_path / "config.toml").write_text('time = "23:59"\n', encoding="utf-8")
    without_flag = run_cli(tmp_path, "next-action")
    assert without_flag.stdout.startswith("nothing: before the scheduled time 23:59")
    with_flag = run_cli(tmp_path, "next-action", "--ignore-time")
    assert with_flag.returncode == 0
    assert with_flag.stdout.startswith("launch: no run started today")


def test_cli_next_action_ignore_time_keeps_every_other_rule(tmp_path):
    make_template(tmp_path / "template")
    (tmp_path / "config.toml").write_text('time = "23:59"\n', encoding="utf-8")
    assert run_cli(tmp_path, "started").returncode == 0
    completed = run_cli(tmp_path, "next-action", "--ignore-time")
    assert completed.stdout.startswith("nothing: a run started at")
