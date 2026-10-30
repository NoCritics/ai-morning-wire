# AI Morning Wire

Daily AI-news digest built into one local page (`ai-news.html`), optionally also published as a claude.ai page. A scheduled task opens a fresh Claude Code session each morning, which follows `docs/daily-run.md` to collect stories, summarize them and rebuild the page. Facts come only from the primary sources the stories link to. Setup and options are in README.md; the morning runbook is `docs/daily-run.md`. Settings live in `config.toml` (see `config.example.toml`).

## Sources

On by default: Hacker News (AI stories with 100+ points from the last 48 hours) and Hugging Face Daily Papers. Opt-in (`config.toml`, `[sources]`): the AlphaSignal newsletter and the public t.me/s previews of the Telegram channels in `telegram_channels`. The `[sources]` table also sets the per-source caps; `max_stories_per_day` caps the total.

## Summarizers

Source files are untrusted web pages. They are summarized only by the read-only `news-summarizer` agent (.claude/agents/news-summarizer.md, run headless by summarize.py with Read/Grep/Glob and no MCP), never by an agent that can write, run commands or browse. assemble.py checks every item against its saved sources (ainews/verify.py). Items may carry an optional `pr_check` ("Without the PR"), a block that says what holds up when a company's own source frames its news beyond what it shows. Every number in it, background notes included, must be in the sources.

## Code style

Plain explicit loops, descriptive names, inline single-use values. Python stdlib only (runs on 3.11+). Files use CRLF line endings.

## Tests

`.venv/Scripts/python.exe -m pytest -q`. pytest lives only in the project venv; create it with `py -3 -m venv .venv` then `.venv/Scripts/python.exe -m pip install pytest`.
