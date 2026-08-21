# Method

Subset of public [HZZ Burza rada](https://burzarada.hzz.hr) listings for **Grad Zagreb**. Not the full catalogue. Not a work-permit or sponsorship guarantee.

Two **independent** inclusion tracks. A listing is on the board if **location_score > 0** and **at least one** track hits. Telegram digests still publish **track A only**.

Official occupation titles live in [`data/uv-occupations.json`](../data/uv-occupations.json). Keyword weights live in [`config.py`](../config.py) (`FOREIGNER_KEYWORDS`). Matching code: [`scoring.py`](../scoring.py), [`shortage.py`](../shortage.py).

---

## Shared scrape constraints

1. **County.** Grad Zagreb radio value `4`. Occupation categories are walked one by one; “Svi poslovi” is capped at 300 rows.
2. **Location.** `location_score > 0` (2 = city centre, 1 = Zagreb, 0 = drop).
3. Other counties are not scraped.

IDs are HZZ `WebSifra`. Dated ads: kept 3 calendar days after `deadline_date`. Open-ended ads: 90 days after first seen. Refresh: GitHub Actions daily 06:00 UTC plus manual full scrape. `generated_at` on `docs/jobs.json` is the last successful export. Operator runbook: [OPERATIONS.md](OPERATIONS.md).

---

## Track A — foreigner-text score (willingness signal)

Weighted phrases in **title + employer + detail description**. Threshold `FOREIGN_SCORE_THRESHOLD = 2`. This is a text score over public HZZ copy. It is not evidence the employer has sponsored before or will pass Art. 99 fitness rules.

**Counting.** Each lexicon string is matched once (`str.find` on NFC-normalized, casefolded text). Repeating the same string does not add weight again. Distinct inflections are separate entries.

**Negation.** A hit is discarded if any marker appears in the 25 characters immediately before it (`NEGATION_WINDOW_CHARS = 25`): `ne `, `nije `, `nema `, `bez `, `isključivo eu`, `samo eu državljani`. Markers after the phrase are not checked.

**Fuzzy fallback.** If exact matching totals 0 and `rapidfuzz` is installed, near-inflections (`partial_ratio ≥ 90`) each add `FUZZY_WEIGHT` 1. Fuzzy hits skip the negation window. A single fuzzy hit is not enough.

### Weight 3 — permit and third-country legal wording

One hit includes the ad on this track (3 ≥ 2). Statutory labels for residence-and-work permits and third-country nationals.

Permit / obtaining a permit:

- `dozvola za boravak i rad`
- `dozvolu za boravak i rad`
- `dozvole za boravak i rad`
- `radna dozvola`
- `radnu dozvolu`
- `radne dozvole`
- `ishođenje dozvole`
- `ishođenje radne dozvole`

Third-country nationals:

- `državljani trećih zemalja`
- `državljanima trećih zemalja`
- `državljana trećih zemalja`

### Weight 2 — foreign nationals, not third-country-specific

One hit includes (2 ≥ 2). Wording is relevant but not legally specific, so the weight is 2 rather than 3.

- `strani državljani`
- `strane radnike`
- `strani radnici`
- `strana radna snaga`
- `sponzorstvo`
- `pomoć oko dozvole`
- `posredovanje pri ishođenju`

### Weight 1 — weak corroboration

A single weight-1 phrase is not enough.

- `osiguran smještaj`
- `smještaj osiguran`
- `smještaj obezbijeđen`
- `besplatan smještaj`
- `terenski rad`

Ads below threshold 2 on this track are still inspected. They appear on the board only if track B also hits.

---

## Track B — UV shortage occupation (procedure / capability signal)

Match the listing **title** to the official HZZ Upravno vijeće list of occupations that may **skip the labour-market test (TTR)** when hiring a third-country national.

- Source: MUP/HZZ PDF *Lista zanimanja (izuzetak od provedbe testa tržišta rada)*, edition stored in `data/uv-occupations.json` `listMeta`.
- This is not a company-level foreign-hire database. There is no public OIB-level foreign/domestic ratio.
- Title match only (HZZ ads here have no NKZ codes). Browse category labels are too coarse and are not used.
- Occupations whose `regions` are neither `all` nor `grad_zagreb` are ignored.
- Gendered HZZ titles (`PROGRAMER / KA`, `ZAVARIVAČ / ICA`) are normalized before match.
- A hit does **not** mean the employer will sponsor or that Art. 99 (turnover, domestic-staff ratio) is satisfied. It means the **occupation title** is on the TTR-exemption list.

Shortage-only listings are on the board and tagged. They are **not** sent on Telegram.

---

## Keeping the UV list from going stale

Same pattern as [residency-runbook](https://github.com/JMNofziger/residency-runbook) (`data/uv-occupations.json` + `listMeta`), plus checks this repo actually enforces:

| Check | When | What |
|-------|------|------|
| `verifiedDate` age | unit tests, every CI | Fail if older than `UV_STALE_AFTER_DAYS` (90) |
| PDF SHA-256 | weekly workflow + manual `python shortage.py --check-pdf` | Fail if `sourceUrl` bytes ≠ `listMeta.sourceSha256` |

**When the PDF changes**

1. Open `listMeta.hubUrl` and `sourceUrl`. Confirm a new UV decision/PDF.
2. Re-transcribe `occupations[]` (`id`, `titleHr`, `titleEn`, `regions`).
3. Set `edition`, `verifiedDate` (ISO date), and `sourceSha256` (`sha256sum` of the new PDF).
4. Run `python -m unittest tests.test_shortage tests.test_uv_list -v`.
5. PR. Do not edit occupation titles to “help” matching; match the PDF.

**When the PDF is unchanged**

Bump `verifiedDate` only, after a human re-check of the hub. Keep `sourceSha256`.

The HZZ mirror PDF is not byte-identical to the MUP host; hashing uses `sourceUrl` (MUP) only.

---

## HR — Metoda

Podskup javnih oglasa HZZ Burze rada za **Grad Zagreb**. Nije cijeli katalog. Nije jamstvo dozvole ni sponzorstva.

Dva **odvojena** kolosijeka. Oglas je na ploči ako je `location_score > 0` i pogodi **barem jedan** kolosijek. Telegram šalje samo **kolosijek A**.

### Kolosijek A — strani rezultat (signal iz teksta oglasa)

Ponderirane fraze u naslovu + poslodavcu + opisu. Prag `FOREIGN_SCORE_THRESHOLD = 2`. Težine 3 / 2 / 1. Jedna fraza težine 1 nije dovoljna. Pogoci u 25 znakova nakon oznaka negacije (`ne `, `nije `, `nema `, `bez `, `isključivo eu`, `samo eu državljani`) se odbacuju. `NEGATION_WINDOW_CHARS = 25`. Primjeri: `radna dozvola` (težina 3), `strani državljani` (težina 2).

### Kolosijek B — deficitarno zanimanje UV (signal postupka)

Naslov oglasa se uspoređuje sa službenom listom zanimanja Upravnog vijeća HZZ-a za koja se **ne provodi test tržišta rada**. Transkripcija: `data/uv-occupations.json`. Nije baza poslodavaca koji su već zapošljavali strance.

### Ažuriranje liste

`verifiedDate` ne smije biti stariji od 90 dana (CI). Tjedni posao uspoređuje SHA-256 PDF-a na `sourceUrl` s `sourceSha256`. Novi PDF → ponovna transkripcija, ne pogađanje naslova.
