# HZZ Zagreb foreign-friendly job digest

Automated daily aggregation of Burza rada (HZZ) listings in Zagreb that look
open to hiring third-country nationals, ranked by application urgency.

## Quick start

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="..."     # from @BotFather
export TELEGRAM_CHAT_ID="..."       # your user id or a channel id

python main.py bootstrap   # one-time: build the 6-day launch digest
python main.py daily       # run once a day thereafter (see .github/workflows/daily.yml)
```

## Unattended daily run

The GitHub Actions workflow `.github/workflows/daily.yml` already runs
`python main.py daily` on a cron (`06:00 UTC`) with no human prompt.

For that schedule to actually fire:

1. **Merge this workflow to `main`.** GitHub only runs `on.schedule` from the
   default branch.
2. **Add repo secrets** `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
   (Settings → Secrets and variables → Actions).
3. **Allow Actions** on the repo (Settings → Actions → General).
4. Optionally click **Run workflow** once on the Actions tab to smoke-test
   `workflow_dispatch` before waiting for 06:00 UTC.

The first daily run will scrape ~1,000 Grad Zagreb list rows and fetch
detail pages for unseen jobs (tens of minutes). Later days only fetch new
`WebSifra`s. The SQLite state file is committed back to the repo so dedup
survives ephemeral runners.

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
