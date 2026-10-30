# AI Morning Wire

A short AI-news digest, built on your PC every morning. Each story is a few bullet points of hard facts (model sizes, prices, licenses, benchmark scores) with links to the primary sources. When a company's announcement oversells its news, a "Without the PR" note says what the source actually shows.

A scheduled task opens [Claude Code](https://claude.com/claude-code) each morning. It collects stories, summarizes them, checks every number against the saved sources and rebuilds one local web page. It costs nothing beyond your Claude plan.

![A sample day of the digest, built from invented stories](docs/sample.png)

The screenshot uses invented stories.

## Requirements

- Windows 10 or 11.
- Python 3.11 or newer (the `py` launcher or a real `python`, not the Microsoft Store stub). No packages needed.
- [Claude Code](https://claude.com/claude-code), installed and logged in.
- Windows Terminal (`wt`), included with Windows 11.
- [Git for Windows](https://git-scm.com/download/win). The morning session runs its commands in Git Bash.

Setup checks all of these and tells you what's missing.

## Setup

```powershell
git clone https://github.com/NoCritics/ai-morning-wire
cd ai-morning-wire
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Setup asks for the digest time (default 08:00), whether to also publish a private claude.ai page, and whether to turn on the opt-in sources or the browser fallback. Then it:

1. writes `config.toml` and `.claude/settings.local.json` (both kept out of git);
2. shows the `Register-ScheduledTask` command and registers the task if you say yes;
3. starts the first run.

The first run opens a Windows Terminal window. Accept Claude Code's "trust this folder" prompt there: until you do, Claude Code ignores this folder's allow rules and scheduled runs would stall. If the claude.ai page is on, also approve the page's database capability once. Later runs need no prompts.

Options: `-Time HH:MM`, `-TaskName <name>` (default: `task_name` from `config.toml`, else "AI Morning Wire"), `-Defaults` (take every default), `-Yes` (approve every confirmation except replacing an existing `config.toml`) and `-DryRun` (print everything, change nothing).

To update, run `git pull`. Rerun `setup.ps1` after changing `time`, `python`, `browser_fallback` or `claude_ai_page`, or after moving the folder: only setup moves the scheduled task and writes the allow rules, so editing those keys by hand leaves the next run at the wrong time or stuck on permission prompts. With `-Time` and an existing `config.toml`, setup rewrites only its `time` line. After moving the folder, accept the trust prompt again (setup's first run asks for it).

## What a morning looks like

At the set time a Windows Terminal window opens with Claude Code (Sonnet) following the runbook, [docs/daily-run.md](docs/daily-run.md). It collects, summarizes, checks, builds and, if enabled, publishes, then leaves a short report in the window. Closing the window ends the session. Nothing runs between mornings.

The page is `ai-news.html` in this folder; open it in any browser. The claude.ai page loads one file per day, so each publish stays small however long the history grows.

If the PC is asleep, the task asks Windows to wake it. Many laptops don't wake for it (Modern Standby, or on battery); the run then starts when you wake the PC. If the PC was off, the run starts after you log on. It runs only while you're logged on.

Every run ends with a line in `data/runs.log` (`published: ...` or `failed: <reason>`), and a day without a digest says why on the page. A second trigger 95 minutes after the set time, and one at logon, mark a run still unfinished after 90 minutes as failed. If every source was unreadable, the run retries once, but only if that second trigger falls on the same day; for set times after about 22:25 it falls after midnight, and the retry happens only at a logon.

Zero new stories is a normal result (Daily Papers is empty on weekends); the page says so.

## Sources and their terms

This is a reading of each source's terms as of 2026-10-27, not legal advice. Read the terms yourself before turning an opt-in source on.

- **Hacker News** (AI stories with 100+ points from the last 48 hours, through the Algolia HN Search API) and **Hugging Face Daily Papers** (public API): public APIs offered for this kind of use. On by default.
- **AlphaSignal**, off by default. Its [terms](https://alphasignal.ai/terms) forbid "robot, spider, scraper, crawler, or other automated means" without written permission (section 6) and allow "personal, non-commercial use" (section 7.2). Its `robots.txt` allows all crawlers, with `ai-input=yes`. The collector reads its feed and fetches each article page for the citations, which is the automated access section 6 forbids.
- **Telegram** (the public `t.me/s` previews of the channels in `telegram_channels`), off by default. Its [terms](https://telegram.org/tos) ban scraping, and its [AI scraping terms](https://telegram.org/tos/content-licensing) forbid "scraping, indexing, harvesting, aggregation or use of data" for AI without the content owners' explicit consent, with no personal-use exception. The collector scrapes the previews and passes each post's first line to the summarizer as a headline.
- **Primary sources** (the pages the stories link to): fetched one page at a time, saved locally and quoted only as short facts with links. The digest is for your own reading and is never republished.
- **Browser fallback**, off by default. It opens pages that block the collector (403/429/503, bot checks, JavaScript-only pages) in a real browser, which gets around bot protection, and tries WebFetch if that fails. With it off, blocked pages are skipped and logged.

X (Twitter) posts are read through fxtwitter's public embed API. [twitterapi.io](https://twitterapi.io) is used only if you set your own `TWITTERAPI_IO_KEY` environment variable.

## Settings

Settings live in `config.toml`, which setup creates from [config.example.toml](config.example.toml). Every key is optional.

| Key | Default | What it does |
|---|---|---|
| `time` | `"08:00"` | Start time (24-hour HH:MM). Change it with `setup.ps1 -Time HH:MM`. |
| `max_stories_per_day` | `25` | Most stories per day, across all sources. |
| `browser_fallback` | `false` | Fetch blocked pages in a real browser. Needs the Playwright MCP server. Change it with `setup.ps1`. |
| `claude_ai_page` | `false` | Also publish a private claude.ai page. Change it with `setup.ps1`. |
| `python` | set by setup | Path to `python.exe`; empty means `py -3`. Change it with `setup.ps1`. |
| `claude` | set by setup | Path to `claude.exe`, used in the task's launch command. |
| `task_name` | `"AI Morning Wire"` | Default task name for `setup.ps1`. |
| `[sources] hacker_news`, `daily_papers` | `true` | The default sources. |
| `[sources] alphasignal`, `telegram` | `false` | The opt-in sources (see the terms above). |
| `[sources] telegram_channels` | `["neuraldvig", "whackdoor"]` | Channels read when `telegram` is on. |
| `[sources] hacker_news_max`, `daily_papers_max` | `12`, `8` | Most stories per day from each. |

You can edit `max_stories_per_day` and the `[sources]` table by hand at any time. Setup shows the terms warning when you turn on an opt-in source or the browser fallback.

### What the unattended run is allowed to do

The morning session must never stall on a permission prompt. The rules ship in `.claude/settings.json`, and setup writes the ones that depend on your machine and choices to `.claude/settings.local.json` (out of git). Your global `~/.claude/settings.json` is not touched.

- **Always allowed:** the folder's scripts (`collect.py`, `summarize.py`, `assemble.py`, `build.py`, `runstate.py`), run with your interpreter exactly as the runbook types them, and reading the date.
- **Turned off:** Claude Code's PowerShell tool, so commands run through Git Bash. On some PCs (for example with PowerShell 7 from the Microsoft Store) that tool asks before every command, whatever the allow rules say.
- **With `browser_fallback` on:** `browser_source.py` (which refuses any file outside `work/<date>/sources/<story>/`), WebFetch, and four Playwright tools (navigate, wait for, evaluate, close).
- **With `claude_ai_page` on:** the Artifact and ArtifactData tools and the two artifact skills. Deleting or pinning a page and every ArtifactData write stay denied.
- **Always denied:** editing Python and PowerShell files, `scripts/`, `template/`, `.claude/`, `docs/`, `README.md`, the config files, and `CLAUDE.md` or `CLAUDE.local.md` at any depth. The session can write its run files (`work/`, `data/`) but not the code it runs.

**Hostile web pages.** The session reads untrusted text: story titles, source text when it fixes a number, and, with the browser fallback on, browser and WebFetch output. It can write files and run commands, so it isn't isolated from that text. What limits the damage: only the read-only `news-summarizer` agent (Read, Grep, Glob, no MCP servers) reads the sources in full; commands are limited to the allow list; the deny rules protect the code; and `assemble.py` checks every number against the saved sources. This reduces the risk but doesn't remove it. Keep the browser fallback off if you don't want the session fetching arbitrary pages.

## What it costs

Nothing beyond your Claude plan. The run uses your plan's usage, on Sonnet throughout. `max_stories_per_day` caps the work (stories go in batches of 3 to 4, up to 3 batches at once). Lower it or turn sources off to use less.

## Customize the page

Both pages are built from [template/page.html](template/page.html), [template/page.css](template/page.css) and [template/page.js](template/page.js). Colors and fonts are CSS variables on `:root` at the top of `template/page.css`:

```css
/* excerpt: the light-theme values; --focus-surface and --on-ink are also defined */
:root {
  --canvas: #f7f7f4;   /* page background */
  --ink: #26251e;      /* headings */
  --body: #3d3d3a;     /* running text */
  --muted: #6c6a64;    /* labels and dates */
  --hairline: #e6e5e0; /* row dividers */
  --accent: #f54e00;   /* unread dots and the Today marker */
  --font-display: "Inter Tight", "Inter", system-ui, sans-serif;
  --font-body: "Inter", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, Consolas, monospace;
}
```

A second set covers dark mode (the page follows the system theme and has a toggle). Run `py -3 build.py` to rebuild both pages after editing.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File uninstall.ps1
```

It shows the `Unregister-ScheduledTask` command and removes the task if you say yes (`-TaskName <name>` for another name, `-Yes` to skip the question). It deletes no files: delete the folder yourself, including your `config.toml` and `data/`.

## How it works

1. **Collect** (`collect.py`): finds stories from the enabled sources, drops duplicates and anything already published, applies the caps, and saves each story's primary-source pages under `work/<date>/`.
2. **Summarize** (`summarize.py`): sends the stories in batches to the read-only `news-summarizer` agent (`.claude/agents/news-summarizer.md`), run headless with Read, Grep and Glob only. Nothing that can write, run commands or browse reads the source files.
3. **Check** (`assemble.py`, `ainews/verify.py`): an item goes back for a fix if it has a number not in its sources, cites a source that was never read, or has a "Without the PR" pitch not quoted word for word.
4. **Build** (`build.py`): writes `ai-news.html` and the claude.ai page files in `work/publish/`. The feed keeps 7 days, the archive 30.

## Limitations

- Windows only.
- Needs Claude Code. A window opens each morning; closing it ends the run.
- Runs only while you're logged on.
- Only as complete as its sources: with the defaults (Hacker News and Daily Papers), quiet days and weekends have fewer stories.
- The summarizer is a language model. The checks catch numbers that aren't in the sources, not every misreading, so follow the links for anything that matters.
- Pages behind bot protection are skipped unless the browser fallback is on.

## Roadmap

Planned, most likely first:

- Your own sites or journals as sources.
- The digest by email or to a channel of your choice.
- An API edition (Python calling the Messages API, no terminal needed).
- Mac and Linux.
- Codex and agy versions, and a Claude Code plugin.
- A desktop app.

## License

[MIT](LICENSE).
