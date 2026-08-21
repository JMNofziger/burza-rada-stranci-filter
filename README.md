# HZZ Zagreb foreign-friendly job digest

Automated daily aggregation of Burza rada (HZZ) listings in Zagreb that look
open to hiring third-country nationals, ranked by application urgency.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # local secrets; never commit .env
# edit .env: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

python main.py bootstrap      # one-time: queue existing matches into a 6-day review
python main.py daily          # collect new matches (cron target)
python main.py full-scrape --phase status   # resumable one-off complete scrape
```

`.env` is gitignored. GitHub Actions uses repo secrets with the same names
instead of that file.

## Jobs board (phone + desktop)

The matching-jobs board is a **public GitHub Pages site**, not a local server.
Filters sit in a side drawer (shopping-style): expiry window, location,
employer, Telegram-sent, plus search and sort. The page is rebuilt from
SQLite after every successful `daily` / full-scrape notify (`docs/jobs.json`).

After merge:

1. Make the repo **public** (Pages is free on public repos; the HZZ ads are
   already public). Keep Telegram secrets in Actions / `.env`.
2. Settings → Pages → Source **GitHub Actions**.
3. URL: `https://jmnofziger.github.io/burza-rada-stranci-filter/`

The first collect (or this PR’s Pages workflow) publishes the board. Later
days overwrite `docs/jobs.json` and redeploy.

## Telegram chat ID

The bot is **send-only**. It will not reply when you type in the chat. Silence
after Start is normal.

1. Talk to [@BotFather](https://t.me/BotFather), `/newbot`, copy the token into `TELEGRAM_BOT_TOKEN`.
2. Open **your** bot (not BotFather), tap **Start**, send `hi`.
3. Run:

```bash
python main.py chat-id
```

That prints `TELEGRAM_CHAT_ID=…` and sends one confirmation message so you
know it works. Paste the id into `.env` and into the GitHub Actions secret.

To confirm GitHub Actions secrets without scraping HZZ:

```bash
python main.py telegram-check   # local .env, or CI injects repo secrets
```

That calls Telegram `getMe` + `getChat` only — no digest, no chat message.
CI runs the same check on every push/PR and before the daily scrape.

A user chat id is a positive integer. A channel/group id is usually negative (often starts with `-100`). For a channel, add the bot as an admin, post a message in the channel, then re-run `chat-id`.

Alternatively open this URL in a browser after messaging the bot (replace `TOKEN`):

`https://api.telegram.org/botTOKEN/getUpdates`

Look for `"chat":{"id": 123456789`.

## What runs when

| Mode | Purpose |
|------|---------|
| **daily** (main feature) | Scrape every day. Publish **only brand-new** filter matches. If none, send an explicit "no new ads" Telegram. |
| **bootstrap** | One-time backlog: existing matches paced across 6 days so reviewing current posts is sane. `daily` sends the next bucket until that queue is empty. |
| **full-scrape** | Manual complete Grad Zagreb pass, split into **list → details → notify** so a failure resumes from the last checkpoint instead of starting over. |
| **export-web** | Rewrite `docs/jobs.json` from the current DB (also runs at the end of a successful collect). |

Collection stays daily even if you later switch publishing to weekly
(`NEW_MATCH_PUBLISH_CADENCE = "weekly"` in `config.py`). Expiry-soon
alerts are not part of this path yet.

## Unattended daily run

The GitHub Actions workflow `.github/workflows/daily.yml` already runs
`python main.py daily` on a cron (`06:00 UTC`) with no human prompt.

For that schedule to actually fire:

1. **Merge this workflow to `main`.** GitHub only runs `on.schedule` from the
   default branch.
2. **Add repo secrets** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
   (Settings → Secrets and variables → Actions).
3. **Allow Actions** on the repo (Settings → Actions → General).
4. Click **Run workflow** and leave **run_size = smoke** for a silent health
   probe (Telegram only if it fails). Cron and **full** always run that probe
   first; on failure a **CRITICAL** alert is sent and the large collect is
   skipped. Missed days are not dropped: the next successful collect publishes
   all unseen matches and labels a catch-up if the last success was 2+ days ago.

The first daily run seeds ~1,000 Grad Zagreb list rows into SQLite so they
are not mistaken for "new today". Later days only fetch unseen `WebSifra`s
and Telegram the new filter matches (or a zero notice). The SQLite state
file is committed back to the repo so dedup survives ephemeral runners.
Daily collect also records every listed `WebSifra` in `inspected`, including
non-matches, so later runs do not re-fetch those detail pages.

Expired ads (jobs **and** inspected) are deleted **3 calendar days after**
`deadline_date`. Open-ended ads are deleted **90 days after** first seen /
listed. Then the file is `VACUUM`ed. That keeps the *current* DB small; it
does **not** shrink git history (see below).

## GitHub limits

The HZZ ads themselves are public, so **making this repo public is fine**
and is the simplest way off the private-repo 2,000-minute cap. Keep
`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in Actions secrets and `.env`
(already gitignored). Do not commit `.env`.

| Constraint | Our plan | Verdict |
|---|---|---|
| **Public repo + `ubuntu-latest`** | Minutes are free | Preferred if you want zero Actions billing |
| **Private + Free plan** | 2,000 Linux min/month | One full scrape ~40–70 min; hung jobs still bill until timeout |
| **Job cap 6h** / our timeouts 90–180 min | Full scrape should finish in <90 min | OK |
| **Repo size ~1 GB warn / 5 GB hard** | SQLite is tiny; **git history of binary SQLite is not** | Real risk either way |
| **100 MB file cap** | Unlikely at this volume | OK |
| **API 1,000 req/hour** | ~27 `git push` checkpoints per full scrape | OK |

**The SQLite-in-git pattern is the limit we will hit first**, not Actions minutes and not row count. Each daily persist and each details batch commits the *whole* DB as a new blob. Git barely deltas binary SQLite, so history grows with **number of persists**, not live rows. Pruning shrinks today's file; old checkpoints stay forever unless we rewrite history.

Do **not** use macOS/Windows runners or raise `timeout-minutes` toward 360. If `.git` gets large, stop committing the DB and move state off git.

## Manual one-off full scrape

Use this when you want every Grad Zagreb listing scored in one sitting (~1,000
detail pages). It is **workflow_dispatch only** — not on the daily cron.

Phases (each is restartable):

1. **list** — walk occupation categories and record every `WebSifra` in
   `inspected`. Finished categories are checkpointed per scrape run, so a
   mid-walk failure skips them on retry.
2. **details** — fetch + score pending inspected rows in batches (default 40).
   Each batch is committed back to git. Failed fetches stay pending.
3. **notify** — Telegram. If this is the first successful collect, matches are
   seeded into the 6-day backlog (same as an empty-DB daily) instead of one
   flood of "new" ads.

```bash
python main.py full-scrape --phase status
python main.py full-scrape --phase list
python main.py full-scrape --phase details --limit 40
python main.py full-scrape --phase notify
python main.py full-scrape --phase all          # local: remaining work end-to-end
```

On GitHub: **Actions → HZZ one-off full scrape → Run workflow**.

| Input | Resume after a failure |
|-------|------------------------|
| `start_phase = all` | Default. List skips completed categories; details skip fetched ads. |
| `start_phase = details` | List already saved; continue remaining detail pages. |
| `start_phase = notify` | Details done; send Telegram / backlog only. |
| `reset_list = true` | New occupation walk (use for a later complete re-scrape). |

Smoke still runs first and stays silent unless it fails. Daily cron and this
workflow share a concurrency group so they cannot clobber the SQLite file.

## Live site behaviour (verified 2026-08-21)

- Session warm-up + cookie persistence reaches the occupation-browse page
  (`Posloprimac_RadnaMjesta.aspx`).
- County filter is radio `ctl00$MainContent$rblZupanija`; **value `4` is
  GRAD ZAGREB** (do not confuse with **ZAGREBAČKA**, value `21`, the county
  around the city).
- Result grid is `#ctl00_MainContent_gwSearch` with `a.TitleLink` rows and
  `ul.pagination` postbacks. Detail body is `#ctl00_MainContent_pnlAjaxBlock`.
- **"Svi poslovi" is capped at 300 rows.** Occupation categories on the
  browse page listed ~1,080 Grad Zagreb jobs, so the crawler iterates
  `lnkKategorija` (then paginates inside each category) instead of the
  capped dump.
- Postback payloads must **not** include the `btnTrazilica` submit button or
  ASP.NET treats the request as "back to search" and the grid disappears.

Smoke-test a single category against the live site:

```bash
HZZ_MAX_CATEGORIES=1 python -c "
from http_client import build_session
from scraper import iter_zagreb_candidates
s = build_session()
jobs = list(iter_zagreb_candidates(s))
print(len(jobs), jobs[0].title if jobs else None)
"
```

## Critique of the original design (summary)

- **Cookie handling**: a plain `requests.get()` with no session reuse hits
  the redirect loop described above and never reaches content. Fixed via a
  persistent `requests.Session` + explicit warm-up request.
- **Pagination/filtering**: WebForms grids are typically driven by postback
  (`__VIEWSTATE`/`__EVENTTARGET`), not a `?page=N` query string. A row/column
  scrape that assumes simple query-string pagination will silently only ever
  see page 1.
- **Description completeness**: keyword matching against only the list page
  will miss most true positives, since the real ad text (where "osiguran
  smještaj" etc. actually appear) lives on the detail page only. This
  package does a two-stage crawl for that reason.
- **Dedup ID**: `MD5(title + employer + deadline)` breaks the moment an
  employer edits a title or extends a deadline (both common), and can
  collide across the gendered job-title pairs common in Croatian ads (e.g.
  "STOLAR-ICA"). The site already assigns a stable `WebSifra` -- use it.
- **Digest bucketing bug**: the reference code's `idx % 6` round-robin
  interleaves urgency tiers across all six launch days instead of
  front-loading the most urgent jobs into Day 1, risking a job being shown
  for the first time *after* its deadline already passed. Fixed in
  `digest.py` with sequential chunking plus a hard 48h override.
- **State file fragility**: flat JSON has no atomicity and no useful
  indexing at this scale. Replaced with SQLite (WAL mode).
- **No error isolation / logging / rate limiting**: one malformed row or one
  slow/failing detail request shouldn't kill an unattended cron job. Every
  per-row/per-page operation here is wrapped and logged individually.
- **Scale**: ~7,900 active national listings; Grad Zagreb is ~1,080. The
  crawler filters županija server-side and walks occupation categories
  (the unfiltered "Svi poslovi" grid is capped at 300 rows).
- **Regex looseness**: `\b10000\b` will match a postal code appearing
  *anywhere* in free text (e.g. a salary figure), not just an actual
  location field -- lower risk once matching happens against a structured
  location field rather than the whole blob, but worth being aware of.
- **Alternative/supplementary source**: EURES (the EU-wide job mobility
  portal) syndicates some HZZ postings and has an open API, but it's not a
  substitute for this specific need -- EURES targets EU/EEA worker mobility,
  so third-country-national-focused ads are under-represented there. Worth
  cross-referencing, not replacing HZZ as the primary source.

## Enhancements: beyond substring keyword matching

Croatian is heavily inflected (7 grammatical cases + gender agreement), so a
literal keyword list under-matches. In rough order of effort vs. payoff:

1. **Curated inflected variants** (what this package does in `config.py`):
   cheap, no new dependency, works well because the domain vocabulary is
   narrow -- there are only a handful of core legal phrases, and their most
   common case forms can be enumerated by hand.
2. **Weighted scoring + a threshold** instead of a boolean `any()`, so a weak
   signal like "terenski rad" doesn't get equal footing with an
   unambiguous phrase like "dozvola za boravak i rad" (implemented).
3. **Negation guard**: skip a keyword hit if a negation marker appears just
   before it in the text, catching ads that explicitly rule out sponsorship
   (implemented, intentionally simple -- a proximity window, not real parsing).
4. **Fuzzy matching** (`rapidfuzz`) as a fallback when no exact/curated hit
   is found, to catch inflected forms outside the curated list, at lower
   confidence (implemented, optional dependency).
5. **Full lemmatization** via a Croatian NLP pipeline (e.g. `classla`) if
   false negatives from (1) turn out to matter in practice -- heavier
   dependency, worth it only once you have real missed-match examples to
   justify it.
6. **LLM second-pass verification**: run only on the shortlist that already
   passed the cheap keyword filter (keeps cost/latency low), prompting for
   structured extraction (`sponsors_permit: yes/no/unclear`, `evidence
   quote`, `confidence`). This is the most robust option for negation and
   context ("ne nudimo radnu dozvolu" vs "nudimo radnu dozvolu") that regex
   fundamentally can't reason about, and is cheap at this volume with a
   small/fast model.

## Architecture recommendations

**State storage**: SQLite (implemented) -- zero infra, atomic, indexable,
more than sufficient for a few thousand rows. Only reach for Postgres
(Supabase/Neon free tier) if you need multi-writer access or a hosted
dashboard on top of this data later.

**Delivery**: Telegram Bot API (implemented) -- free, push-based, ideal for
urgency-driven alerts, trivial to call via plain HTTP. Email (Resend/SES/
SMTP) is a reasonable alternative if you want a nicer-formatted daily digest
rather than push notifications; a Slack/Discord incoming webhook is the
lowest-effort option if you already live in one of those.

**Scheduling**: start with **GitHub Actions** (implemented,
`.github/workflows/daily.yml`) -- free for a low-frequency personal job, no
server to maintain, secrets managed in repo settings. The one real gotcha is
that Actions runners are ephemeral, so the SQLite file must be committed back
to the repo (done in the workflow) or moved to an external store. Move to
**AWS Lambda + EventBridge** only if this grows into something needing
sub-minute latency, VPC access, or multi-source aggregation -- for a single
daily scrape it adds IAM/networking/state-storage complexity (Lambda has no
persistent local disk either, so you'd still need DynamoDB/S3 for the state
DB) without a clear benefit at this scale. A small always-on box (Fly.io/
Railway free tier) is a reasonable middle ground if you'd rather avoid the
git-commit-as-database pattern entirely.
