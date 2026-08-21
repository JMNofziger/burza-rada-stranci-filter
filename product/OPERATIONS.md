# Operations

How this repo is run. End-user overview: [README](../README.md). Inclusion rules: [METHOD.md](METHOD.md).

HZZ ads are public. Telegram tokens stay in gitignored `.env` / Actions secrets. Do not commit `.env`.

## Secrets

| Name | Where | Used by |
|------|--------|---------|
| `TELEGRAM_BOT_TOKEN` | `.env` locally; repo **Actions secret** | scrape/alert workflows, `telegram-check` |
| `TELEGRAM_CHAT_ID` | same | same |

The bot is **send-only**. Silence after Start in Telegram is expected.

1. [@BotFather](https://t.me/BotFather) → `/newbot` → token.
2. Open **your** bot, tap Start, send `hi`.
3. `python main.py chat-id` prints `TELEGRAM_CHAT_ID=…` and sends a ping.
4. Paste both values into `.env` **and** Settings → Secrets and variables → Actions.

A user chat id is a positive integer. A channel/group id is usually negative (often `-100…`). For a channel, add the bot as admin, post once, re-run `chat-id`.

`python main.py telegram-check` calls Telegram `getMe` + `getChat` only.

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env
# set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

python main.py chat-id
python main.py telegram-check
python main.py smoke
```

Unattended collection is **GitHub Actions**, not a laptop cron. There is no `python main.py serve`. Pages: Settings → Source **GitHub Actions**. Public repo recommended.

## GitHub Actions

Five workflows. Scheduled jobs only run from `main`.

| Workflow | When | Manual? |
|----------|------|---------|
| `test.yml` | every push and PR | no |
| `daily.yml` | cron `06:00 UTC` and manual | yes |
| `full-scrape.yml` | manual | yes |
| `pages.yml` | push to `main` that touches `docs/**` | no |
| `uv-list.yml` | Monday `08:00 UTC`, UV file push, manual | yes |

**Shared**

- Runner `ubuntu-latest`, Python 3.12.
- Concurrency `hzz-pipeline`: daily and full-scrape **queue** (they share SQLite).
- Concurrency `github-pages`: newer Pages deploy can cancel an older one.
- Persist: `.github/scripts/persist-state.sh` commits `data/hzz_jobs.sqlite3` + `docs/jobs.json` with `[skip ci]`. If `main` moved, persist rebases and retries.
- Smoke is silent on success. Telegram only on failure (CRITICAL).

### Tests — `test.yml`

`unittest` + `telegram-check`. No scrape.

### Daily digest — `daily.yml`

Main product path. Cron is always **full**. Manual input `run_size`: `smoke` (probe only) or `full`.

Smoke first. If it fails: CRITICAL Telegram, no collect. If it passes: `python main.py daily`, persist, deploy Pages.

Empty DB on first full collect: matches go to a **6-day backlog**, not one “new today” flood. If the last success was **2+ days** ago, the new-matches title is a catch-up.

Timeouts: smoke 10 min, collect 90 min, Pages 10 min, alerts 5 min.

### Full scrape — `full-scrape.yml`

Complete Grad Zagreb pass (~1,000 detail pages). Resume from what is **on GitHub**.

| Input | Default | Meaning |
|-------|---------|---------|
| `start_phase` | `all` | `all` / `list` / `details` / `notify` |
| `detail_batch_size` | `40` | Detail pages between git commits |
| `reset_list` | `false` | `true` starts a new category walk |

On failure, the log and Telegram print **Resume settings**. Copy those three inputs into Run workflow.

- List never landed on `main` → `start_phase = all`, `reset_list = false`
- List on `main`, details pending → `start_phase = details`
- Details done, Telegram not sent → `start_phase = notify`

Timeout 180 min. `python main.py full-scrape --phase status --resume-help` prints the same card locally.

### Pages — `pages.yml`

Redeploys the board when `docs/` HTML/CSS/JS changes. Data-only persist commits skip this workflow; daily/full-scrape deploy Pages themselves.

### UV list — `uv-list.yml`

Downloads the official MUP PDF and fails if SHA-256 ≠ `listMeta.sourceSha256`. Unit tests fail if `verifiedDate` is older than 90 days. Refresh: [METHOD.md](METHOD.md).

## CLI

`python main.py <mode>`. No `serve` mode. `.env` does not override variables already in the environment.

| Mode | What it does |
|------|----------------|
| `bootstrap` | Score matches; 6-day backlog. No Telegram. |
| `daily` | Collect, Telegram, prune, export board. |
| `full-scrape` | `--phase` `status`/`list`/`details`/`notify`/`all`; `--limit`; `--reset-list`; `--resume-help` |
| `export-web` | Promote UV title matches; rewrite `docs/jobs.json` |
| `smoke` | Tiny live probe. No digest. |
| `telegram-check` | `getMe` + `getChat` |
| `chat-id` | Print chats; ping |
| `alert-critical` | Send CRITICAL Telegram |

```bash
python main.py full-scrape --phase status --resume-help
python main.py full-scrape --phase details --limit 40
```

| Env | Default | Effect |
|-----|---------|--------|
| `HZZ_DB_PATH` | `data/hzz_jobs.sqlite3` | SQLite file |
| `HZZ_MAX_CATEGORIES` | `0` (all) | Cap occupation categories |
| `HZZ_MAX_LISTINGS` | `0` (all) | Cap detail fetches |
| `HZZ_SMOKE` | unset | Prefix Telegram titles with `SMOKE TEST —` |

Retention (`config.py`): dated ads gone **3 days** after deadline; open-ended gone **90 days** after first seen.

## Storage

| Table / file | Role |
|--------------|------|
| `jobs` | Matches (Telegram track A + board) |
| `translations` | Cached HR→EN for Telegram |
| `inspected` | Every listed `WebSifra` |
| `scrape_runs` / `scrape_categories` | Full-scrape checkpoints |
| `meta` | e.g. `last_successful_collect_on` |
| `data/uv-occupations.json` | Official UV occupation list |
| `docs/jobs.json` | Public board payload |

SQLite and `docs/jobs.json` are committed after each successful collect. Stay on git-as-SQLite until history is painful (~1 GB). Public repo Actions minutes are free; do not use macOS/Windows runners.

## Scraper notes (verified 2026-08-21)

- Session warm-up + cookies. Bare `requests.get()` hits a redirect loop.
- County radio `rblZupanija` value **`4` = GRAD ZAGREB** (not ZAGREBAČKA `21`).
- Walk `lnkKategorija`. “Svi poslovi” is capped at 300 rows.
- Never POST `btnTrazilica`. Delay `REQUEST_DELAY_SECONDS = 1`.
- Dedup: `WebSifra`. Telegram: MyMemory HR→EN, cache in `translations`, fail open.

Expiry-soon Telegram and weekly publish cadence are reserved; collection stays daily.
