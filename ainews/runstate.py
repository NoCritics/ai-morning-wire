"""What one scheduled wake-up should do, decided from data/runs.log."""
import re
from datetime import date, datetime, timedelta

RUN_LIMIT = timedelta(minutes=90)
LINE_PREFIX = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) (.*)")
PUBLISHED_FIELDS = re.compile(r"(\S+), (\d+) items(?:, (\d+) batches)?")
UNREADABLE_REASON = "every enabled source unreadable"
KINDS = ("started", "retry", "published", "failed")


def parse_line(line):
    """Returns (written_at, kind, line_date, rest, items) or None when the line is not a run line.

    items is the item count of a published line and None for every other kind.
    """
    prefix_match = LINE_PREFIX.fullmatch(line.rstrip("\r\n"))
    if prefix_match is None:
        return None
    try:
        written_at = datetime.strptime(prefix_match.group(1), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    message = prefix_match.group(2)
    for kind in KINDS:
        marker = f"{kind}: "
        if not message.startswith(marker):
            continue
        rest = message[len(marker):]
        items = None
        if kind == "published":
            fields_match = PUBLISHED_FIELDS.fullmatch(rest)
            if fields_match is None:
                return None
            date_text = fields_match.group(1)
            items = int(fields_match.group(2))
        else:
            date_text = rest.split(", ", 1)[0]
        try:
            line_date = date.fromisoformat(date_text)
        except ValueError:
            return None
        return written_at, kind, line_date, rest, items
    return None


def day_status(log_text, day):
    """Lines are ordered by file position: the result is the last result line written after the last start."""
    started_at = None
    retried = False
    published_any = False
    config_failed = False
    result = None
    items = None
    failed_reason = None
    for line in log_text.splitlines():
        parsed = parse_line(line)
        if parsed is None:
            continue
        written_at, kind, line_date, rest, line_items = parsed
        if line_date != day:
            continue
        if kind == "started" or kind == "retry":
            started_at = written_at
            result = None
            items = None
            failed_reason = None
            if kind == "retry":
                retried = True
            continue
        result = kind
        items = line_items
        failed_reason = None
        if kind == "published":
            published_any = True
        else:
            failed_reason = rest.split(", ", 1)[1] if ", " in rest else ""
            if failed_reason.startswith("config:"):
                config_failed = True
    return {
        "started_at": started_at,
        "result": result,
        "items": items,
        "failed_reason": failed_reason,
        "every_source_unreadable": failed_reason is not None and failed_reason.startswith(UNREADABLE_REASON),
        "retried": retried,
        "published_any": published_any,
        "config_failed": config_failed,
    }


def find_open_earlier_day(log_text, now):
    """The most recent day before today whose last run started 90+ minutes ago and never logged a result."""
    earlier_days = set()
    for line in log_text.splitlines():
        parsed = parse_line(line)
        if parsed is not None and parsed[2] < now.date():
            earlier_days.add(parsed[2])
    for day in sorted(earlier_days, reverse=True):
        status = day_status(log_text, day)
        if status["started_at"] is None or status["result"] is not None or status["published_any"]:
            continue
        if now - status["started_at"] >= RUN_LIMIT:
            return day
    return None


def settled_without_schedule(status, now):
    """("nothing", reason) when today is published or a run is still within its time limit, else None.

    Neither case depends on the scheduled time, so the caller need not read config.toml for them.
    """
    if status["published_any"]:
        return "nothing", "today's digest is published"
    started_at = status["started_at"]
    if started_at is not None and status["result"] is None and now - started_at < RUN_LIMIT:
        return "nothing", f"a run started at {started_at:%H:%M} is still in progress"
    return None


def next_action(status, now, scheduled):
    """Returns (action, reason); action is launch, retry, mark-failed or nothing."""
    settled = settled_without_schedule(status, now)
    if settled is not None:
        return settled
    started_at = status["started_at"]
    if started_at is None:
        if now.time() < scheduled:
            return "nothing", f"before the scheduled time {scheduled:%H:%M}"
        return "launch", "no run started today"
    if status["result"] is None:
        return "mark-failed", f"the run started at {started_at:%H:%M} logged no result within 90 minutes"
    if status["result"] == "failed" and status["every_source_unreadable"] and not status["retried"]:
        return "retry", "every source was unreadable and there has been no retry yet"
    return "nothing", f"today's run ended: {status['result']}"
