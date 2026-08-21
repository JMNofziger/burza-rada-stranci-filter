# HZZ Zagreb foreign-friendly job digest

Personal pipeline that scrapes [Burza rada (HZZ)](https://burzarada.hzz.hr)
for **GRAD ZAGREB** listings that look open to third-country nationals, then:

1. Sends **English Telegram** digests (new matches only after the first fill).
2. Publishes a **phone-friendly jobs board** on GitHub Pages, updated after
   each successful collect.

HZZ ads are public. Telegram bot tokens stay in gitignored `.env` / Actions
secrets. Do not commit `.env`.

## Contents

- [Quick start](#quick-start)
- [Secrets](#secrets)
- [GitHub Actions workflows](#github-actions-workflows)
  - [Shared behaviour](#shared-behaviour)
  - [Tests](#1-tests--githubworkflowstestyml)
  - [HZZ Zagreb daily digest](#2-hzz-zagreb-daily-digest--githubworkflowsdailyyml)
  - [HZZ one-off full scrape](#3-hzz-one-off-full-scrape--githubworkflowsfull-scrapeyml)
  - [Deploy jobs board](#4-deploy-jobs-board--githubworkflowspagesyml)
- [CLI reference](#cli-reference)
- [Jobs board](#jobs-board)
- [What is stored](#what-is-stored)
- [GitHub limits](#github-limits)
- [Live site behaviour](#live-site-behaviour-verified-2026-08-21)
- [Design notes](#design-notes)

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # never commit .env
# set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

python main.py chat-id        # find your chat id, send a ping
python main.py telegram-check # secrets only, no scrape
python main.py smoke          # cheap live probe, no Telegram
```

Unattended collection is **GitHub Actions**, not a laptop cron. There is no
`python main.py serve` — the board is GitHub Pages (`docs/`).

One-time Pages setup: Settings → Pages → Source **GitHub Actions**. Public
repo is recommended (free Pages; HZZ listings are already public). Telegram
tokens stay in Actions secrets / gitignored `.env`.

After Pages is enabled and a collect deploys, the board is
`https://jmnofziger.github.io/burza-rada-stranci-filter/`.

## Secrets

| Name | Where | Used by |
|------|--------|---------|
| `TELEGRAM_BOT_TOKEN` | `.env` locally; repo **Actions secret** in CI | all scrape/alert workflows, `telegram-check` |
| `TELEGRAM_CHAT_ID` | same | same |

The bot is **send-only**. Silence after Start in Telegram is expected.

1. [@BotFather](https://t.me/BotFather) → `/newbot` → token.
2. Open **your** bot, tap Start, send `hi`.
3. `python main.py chat-id` prints `TELEGRAM_CHAT_ID=…` and sends a ping.
4. Paste both values into `.env` **and** Settings → Secrets and variables → Actions.

A user chat id is a positive integer. A channel/group id is usually negative
(often `-100…`). For a channel, add the bot as admin, post once, re-run
`chat-id`. Or inspect `https://api.telegram.org/botTOKEN/getUpdates`.

`python main.py telegram-check` calls Telegram `getMe` + `getChat` only — no
digest, no scrape.

## GitHub Actions workflows

There are **four** workflow files. Open **Actions** in the GitHub UI to run
the two that have a **Run workflow** button.

| Workflow file | UI name | When it runs | Manual? |
|---------------|---------|--------------|---------|
| `.github/workflows/test.yml` | Tests | every push and pull request | no |
| `.github/workflows/daily.yml` | HZZ Zagreb daily digest | cron `06:00 UTC` **and** manual | yes |
| `.github/workflows/full-scrape.yml` | HZZ one-off full scrape | manual only | yes |
| `.github/workflows/pages.yml` | Deploy jobs board | push to `main` that touches `docs/` | no |

### Shared behaviour

- **Runner:** `ubuntu-latest` (Linux, 1× minutes). Do not switch to Windows/macOS.
- **Python:** 3.12 via `actions/setup-python@v6`; `checkout@v5`.
- **Secrets:** scrape/alert jobs inject `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID`. Missing secrets fail `telegram-check` / `daily`.
- **Concurrency group `hzz-pipeline`:** daily and full-scrape **queue**, they
  do not cancel each other (`cancel-in-progress: false`). They share the
  SQLite file; overlapping would corrupt git history.
- **Concurrency group `github-pages`:** Pages deploys; newer deploy can
  cancel an older one.
- **Persist:** `.github/scripts/persist-state.sh` commits
  `data/hzz_jobs.sqlite3` + `docs/jobs.json` with `[skip ci]` (Tests does
  not re-run). Collect/scrape checkouts use `fetch-depth: 0`. If `main`
  moved, persist **fetches, rebases** onto origin, and retries the push.
  A checkpoint exists on GitHub only after that push succeeds. Daily and
  full-scrape still deploy Pages **in the same run** (they do not rely on
  `pages.yml` for data updates).
- **Scheduled workflows** only fire from the **default branch** (`main`)
  after the file is merged.
- **Smoke** is silent on success. Telegram only on failure (CRITICAL).

---

### 1. Tests — `.github/workflows/test.yml`

**UI name:** Tests  
**Trigger:** `push`, `pull_request` (no inputs).  
**Purpose:** unit tests + prove Telegram secrets are valid. No HZZ scrape.

| Job | Timeout | What it does | On failure |
|-----|---------|--------------|------------|
| `unittest` | default | `python -m unittest discover -s tests -v` | PR/push is red |
| `telegram-check` | default | `python main.py telegram-check` | PR/push is red; scrape workflows would fail too |

No `workflow_dispatch` options.

---

### 2. HZZ Zagreb daily digest — `.github/workflows/daily.yml`

**UI name:** HZZ Zagreb daily digest  
**How to run:** Actions → this workflow → **Run workflow**.  
**Also:** cron `0 6 * * *` UTC ≈ 08:00 CEST / 07:00 CET.

This is the **main product path**: health probe, then (unless you asked for
smoke-only) a full Grad Zagreb collect of **new** matches, Telegram, prune,
rewrite `docs/jobs.json`, commit state, publish Pages.

#### Inputs (`workflow_dispatch`)

| Input | Type | Default | Options | Meaning |
|-------|------|---------|---------|---------|
| **run_size** | choice | `smoke` | `smoke`, `full` | See below. Cron ignores this and always behaves like **full**. |

**`run_size = smoke`**

- Runs **Smoke scrape** only (plus alert if it fails).
- Does **not** run Full collect, does **not** Telegram a digest, does **not**
  update SQLite or the board.
- Use this to check “is the site + secrets OK?” in a few minutes.

**`run_size = full`**

- Smoke first. If smoke fails, collect is skipped and you get a CRITICAL
  Telegram.
- If smoke passes: `python main.py daily` (all Grad Zagreb categories,
  skip already-inspected `WebSifra`s, Telegram new matches or a zero
  notice, backlog day if any remain, prune, export board JSON).
- Commits `data/hzz_jobs.sqlite3` + `docs/jobs.json`, deploys Pages.

**Cron (`schedule`)** is always the full path (smoke gate, then collect).
There is no cron-only input.

#### Jobs (in order)

| Job | Needs | Runs when | Timeout | What it does |
|-----|-------|-----------|---------|--------------|
| **Smoke scrape** | — | always | 10 min | `telegram-check`, then `python main.py smoke` (1 category, 5 listings cap) |
| **Critical alert (smoke failed)** | smoke | smoke **failed** | 5 min | Telegram CRITICAL; collect skipped |
| **Full collect** | smoke | smoke **succeeded** **and** (cron **or** `run_size != smoke`) | 90 min | `python main.py daily`; git persist; upload Pages artifact |
| **Publish jobs board** | collect | collect succeeded | 10 min | `actions/deploy-pages` to the `github-pages` environment |
| **Critical alert (full collect failed)** | collect | collect **failed** | 5 min | Telegram CRITICAL. Unseen matches catch up on the next success |

Empty DB on first full collect: existing matches are **backlog** (6-day
review), not “new today”. A zero “new listings” Telegram is sent, then day-1
backlog if any.

If the last successful collect was **2+ calendar days** ago, the new-matches
title is a **catch-up**.

---

### 3. HZZ one-off full scrape — `.github/workflows/full-scrape.yml`

**UI name:** HZZ one-off full scrape  
**How to run:** Actions → this workflow → **Run workflow**.  
**Not on cron.** Use this for a complete Grad Zagreb pass (~1,000 detail
pages) that can **resume** after failure.

Same smoke gate as daily. Then a single long job walks phases with git
checkpoints. Daily and this workflow share `hzz-pipeline`.

#### Inputs (`workflow_dispatch`)

| Input | Type | Default | Options | Meaning |
|-------|------|---------|---------|---------|
| **start_phase** | choice | `all` | `all`, `list`, `details`, `notify` | Where to begin. Finished work is skipped via SQLite checkpoints **that have been pushed**. |
| **detail_batch_size** | string (positive integer) | `40` | e.g. `20`, `40`, `80` | How many detail pages to fetch **between git commits**. Smaller = less lost work if the job dies; more commits = fatter git history. Must be an integer `> 0`. |
| **reset_list** | boolean | `false` | `false` / `true` | Only affects **list**. `true` starts a **new** occupation-category walk and ignores leftover category checkpoints. |

**`start_phase` in detail**

| Value | List walk | Detail fetches | Telegram / backlog | Typical use |
|-------|-----------|----------------|--------------------|-------------|
| **all** | Yes (skips categories already marked complete on the open run) | Yes, until `details_pending = 0` | Yes | First complete scrape, or resume everything left |
| **list** | Yes | No | No | Rebuild the listing index only |
| **details** | No | Yes, remaining pending rows | No (unless you also… you don’t; this value skips notify) | List already saved; continue scoring |
| **notify** | No | No | Yes | Details done; send Telegram / seed backlog / mark success |

After `list` or `details` only, run again with the next phase (or `all`) to
finish. `python main.py full-scrape --phase status` prints JSON
(`suggested_phase`, `details_pending`, `unnotified`, …).

**`reset_list = true`:** new scrape-run id, walk every occupation category
again. Use for a later full re-list, not for resuming a failed list (resume
with `false` so completed categories stay skipped).

**First successful notify** on an empty collect history seeds the **6-day
backlog** instead of flooding Telegram with every match as “new”.

#### Jobs (in order)

| Job | Needs | Runs when | Timeout | What it does |
|-----|-------|-----------|---------|--------------|
| **Smoke scrape** | — | always | 10 min | same as daily smoke |
| **Critical alert (smoke failed)** | smoke | smoke failed | 5 min | Telegram; scrape skipped |
| **Resumable full scrape** | smoke | smoke succeeded | 180 min | phases per `start_phase`; persist SQLite + `jobs.json` after list and after **each** details batch; upload Pages artifact |
| **Publish jobs board** | scrape | scrape succeeded | 10 min | deploy Pages |
| **Critical alert (full scrape failed)** | scrape | scrape failed | 5 min | Telegram with the same **Resume settings** card as the job log |

Resume from what is **on GitHub**, not from the runner. Persist commits
after the list walk and after each details batch. If persist fails before
the push, that checkpoint is gone.

On failure the scrape job prints a **Resume settings** card (Actions log +
job summary) and Telegram repeats it. Copy those three inputs into
**Run workflow**:

- List never landed on `main` → `start_phase = all`, `reset_list = false`
- List is on `main`, details still pending → `start_phase = details`
- Details done, Telegram not sent → `start_phase = notify`
- Keep the same `detail_batch_size` unless you want smaller checkpoints

`python main.py full-scrape --phase status --resume-help` prints the same
card locally. JSON status is still `python main.py full-scrape --phase status`.

If the job dies after a successful details persist, at most
`detail_batch_size` pages are re-fetched. If `main` moved during the run,
persist rebases and retries instead of failing on a non-fast-forward push.

---

### 4. Deploy jobs board — `.github/workflows/pages.yml`

**UI name:** Deploy jobs board  
**Trigger:** push to **`main`** whose changed files include `docs/**` or this
workflow file. **No inputs.**

Used when HTML/CSS/JS of the board changes (a merge). Data-only persist
commits use `[skip ci]` and **do not** start this workflow; daily/full-scrape
deploy Pages themselves.

| Job | Timeout | What it does |
|-----|---------|--------------|
| **deploy** | 10 min | checkout, `configure-pages`, upload `docs/`, `deploy-pages` (`github-pages` environment) |

**One-time GitHub setup**

1. Repo **public** (Pages is free; listings are already public).
2. Settings → Pages → Source **GitHub Actions**.
3. First deploy may create the `github-pages` environment; allow it if
   GitHub asks. The URL 404s until a deploy succeeds.

URL: `https://jmnofziger.github.io/burza-rada-stranci-filter/`

---

## CLI reference

All commands: `python main.py <mode> [options]`. There is no `serve` mode.
Locally, `.env` is loaded and does not override variables already in the
environment (Actions secrets win in CI).

| Mode | Options | What it does | Telegram | Writes DB | Writes `docs/jobs.json` |
|------|---------|--------------|----------|-----------|-------------------------|
| **bootstrap** | — | Score all Zagreb matches; bucket into 6-day backlog. Does not send. | no | yes | no |
| **daily** | — | Collect new matches; Telegram new or zero notice; next backlog day; prune; export board. Empty DB seeds backlog instead of “all new”. | yes | yes | yes (on success) |
| **full-scrape** | `--phase`, `--limit`, `--reset-list`, `--resume-help` | See below | **notify** only | yes (list/details/notify) | yes after details batches and notify |
| **export-web** | — | Rebuild `docs/jobs.json` from current `jobs` table | no | no | yes |
| **smoke** | env caps | 1 category, few rows, one detail page. No digest. | no | no | no |
| **telegram-check** | — | `getMe` + `getChat` | no | no | no |
| **chat-id** | — | Print chats; ping each | ping | no | no |
| **alert-critical** | optional `message` | Send CRITICAL Telegram | yes | no | no |

### `full-scrape` flags

| Flag | Default | Values | Meaning |
|------|---------|--------|---------|
| `--phase` | `status` | `status`, `list`, `details`, `notify`, `all` | Same idea as the workflow `start_phase`. `status` prints JSON to stdout (logs on stderr). `all` is list → remaining details → notify (local; no git checkpoints unless you commit). |
| `--limit` | `0` | integer | Details batch size. `0` = all remaining pending rows. Workflow passes `detail_batch_size`. |
| `--reset-list` | off | flag | Same as workflow `reset_list: true`. |
| `--resume-help` | off | flag | With `--phase status`, print the three workflow inputs to re-run after a failure. |

```bash
python main.py full-scrape --phase status
python main.py full-scrape --phase status --resume-help
python main.py full-scrape --phase list
python main.py full-scrape --phase details --limit 40
python main.py full-scrape --phase notify
python main.py full-scrape --phase all
python main.py full-scrape --phase list --reset-list
```

### Environment knobs (`config.py` / env)

| Variable | Default | Effect |
|----------|---------|--------|
| `HZZ_DB_PATH` | `data/hzz_jobs.sqlite3` | SQLite file |
| `HZZ_MAX_CATEGORIES` | `0` (all) | Cap occupation categories (smoke uses `1`) |
| `HZZ_MAX_LISTINGS` | `0` (all) | Cap detail fetches in collect |
| `HZZ_SMOKE` | unset | If `1`/`true`, Telegram titles get `SMOKE TEST —` |

Retention (constants in `config.py`, not env): dated ads deleted **3
calendar days** after `deadline_date` (`EXPIRED_JOB_RETENTION_DAYS`);
open-ended ads deleted **90 days** after first_seen / listed
(`OPEN_ENDED_JOB_RETENTION_DAYS`); then `VACUUM`. Detail batch size in CI
is the workflow input `detail_batch_size` → `--limit`, not an env var.

---

## Jobs board

Static site in `docs/` (GitHub Pages). Not a local server — there is no
`serve` CLI. **Matching `jobs` only** — inspected non-matches are not listed.

**Phone:** **Filters** opens a drawer (expiry, location, employer,
Telegram-sent). **Desktop:** same filters stay in a left sidebar below the
header; search, sort, and listing count live in the main column. Unchecked
expiry or location (or employer) means **all listings**. The **N of M listings**
count sits above the cards. Nav has an **EN/HR** control (English translates
card titles and searches them), a sun/moon theme toggle (**dark is default**),
and a **Method** page that explains inclusion rules. Tapping a card opens the
HZZ detail URL.

The board lists **matching `jobs`** after a successful collect. It refreshes
when daily or full-scrape finishes and deploys Pages, or when `docs/` HTML
changes merge and `pages.yml` runs.

## What is stored

| Table / file | Role |
|--------------|------|
| `jobs` | Filter **matches** (Telegram + board) |
| `inspected` | Every listed `WebSifra` (match or not) so details are not re-fetched |
| `scrape_runs` / `scrape_categories` | Full-scrape list checkpoints |
| `meta` | e.g. `last_successful_collect_on` |
| `docs/jobs.json` | Public board payload (`generated_at` + jobs) |

GitHub Actions runners are ephemeral, so SQLite **and** `docs/jobs.json`
are committed after each successful collect (`[skip ci]`). First fill has
already landed: `data/hzz_jobs.sqlite3` and a non-empty `docs/jobs.json`
are on `main`. `data/.gitkeep` remains so the folder exists if the DB is
ever missing.

## GitHub limits

Public repo + `ubuntu-latest` → Actions minutes are free. Private Free plan
is **2,000 Linux minutes/month**. A hung job still bills until its timeout
(smoke 10, collect 90, full scrape 180, GitHub hard cap 6 hours).

The limit that grows over time is **git history of binary SQLite**, not live
row count. The live DB is tiny. Each persist is a near-full blob; prune
shrinks today’s file, not old commits. Stay on git-as-SQLite until `.git` is
painful (~1 GB); that is not a planned migration. Do not use macOS/Windows
runners.

## Live site behaviour (verified 2026-08-21)

- Session warm-up + cookies reach `Posloprimac_RadnaMjesta.aspx`.
- County radio `ctl00$MainContent$rblZupanija` value **`4` = GRAD ZAGREB**
  (not **ZAGREBAČKA**, value `21`).
- Grid `#ctl00_MainContent_gwSearch`, `a.TitleLink`, pager `ul.pagination`.
  Detail body `#ctl00_MainContent_pnlAjaxBlock`.
- **"Svi poslovi" is capped at 300 rows.** The crawler walks `lnkKategorija`
  (~1,170 Grad Zagreb jobs).
- Never include `btnTrazilica` in postback payloads.
- **1 s delay** (`REQUEST_DELAY_SECONDS`) between sequential HTTP requests.

```bash
HZZ_MAX_CATEGORIES=1 python -c "
from http_client import build_session
from scraper import iter_zagreb_candidates
s = build_session()
jobs = list(iter_zagreb_candidates(s))
print(len(jobs), jobs[0].title if jobs else None)
"
```

## Design notes

**Cookies:** a bare `requests.get()` hits a redirect loop. Persistent
`Session` + warm-up.

**Pagination:** WebForms postbacks (`__VIEWSTATE` / `__EVENTTARGET`), not
`?page=N`.

**Scoring:** keyword weights + negation window in `config.py` /
`scoring.py`. Detail text is required; list-page titles are not enough.

**Dedup:** stable `WebSifra`, not a title hash.

**Backlog:** sequential urgency chunks + 48h override (`digest.py`), not
`idx % 6`.

**State:** SQLite WAL committed to git after each successful collect. That
is the $0 Actions design. Stay on it until repo history is painful (~1 GB).

Expiry-soon Telegram alerts and weekly publish cadence
(`NEW_MATCH_PUBLISH_CADENCE`) are reserved, not the current path.
Collection stays daily.
