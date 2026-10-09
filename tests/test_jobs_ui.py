from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from scraper import JobListing
from storage import StateStore, job_row_to_view, location_label_for, urgency_for_days
from web.export import jobs_payload, write_jobs_json


TODAY = date(2026, 8, 21)
ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "docs" / "app.js").read_text()


def _extract_js_functions(*names: str) -> str:
    chunks: list[str] = []
    for name in names:
        pattern = rf"function {re.escape(name)}\([^)]*\) \{{"
        match = re.search(pattern, APP_JS)
        if not match:
            raise AssertionError(f"missing function {name}")
        start = match.start()
        if APP_JS[max(0, start - 6) : start] == "async ":
            start -= 6
        depth = 0
        i = APP_JS.find("{", start)
        for j in range(i, len(APP_JS)):
            ch = APP_JS[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    chunks.append(APP_JS[start : j + 1])
                    break
        else:
            raise AssertionError(f"unclosed function {name}")
    return "\n".join(chunks)


TRACKER_FUNCS = (
    "emptyTracker",
    "normalizeTracker",
    "statusRank",
    "mergeTrackers",
    "trackerSignature",
    "countMarks",
    "pruneTracker",
    "getJobStatus",
    "setJobStatus",
    "toggleJobMark",
    "parseBackup",
)
DRIVE_FUNCS = (
    "driveRequest",
    "driveFindFile",
    "driveReadFile",
    "driveCreateFile",
    "driveUpdateFile",
    "driveSyncOnce",
)


def _js_consts(*names: str) -> str:
    lines = []
    for name in names:
        match = re.search(rf"^const {re.escape(name)} = .*;$", APP_JS, re.M)
        if not match:
            raise AssertionError(f"missing const {name}")
        lines.append(match.group(0))
    return "\n".join(lines)


def _run_node(script: str):
    proc = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def _run_tracker_cases(cases: list[dict]) -> list:
    helpers = _extract_js_functions(*TRACKER_FUNCS)
    script = f"""
{_js_consts("TRACKER_STATUSES")}
{helpers}
const cases = {json.dumps(cases)};
const out = [];
for (const c of cases) {{
  if (c.op === "normalize") out.push(normalizeTracker(c.raw, c.now));
  else if (c.op === "prune") out.push(pruneTracker(c.tracker, c.ids));
  else if (c.op === "get") out.push(getJobStatus(c.tracker, c.id));
  else if (c.op === "set") out.push(setJobStatus(c.tracker, c.id, c.status, c.now));
  else if (c.op === "toggle") out.push(toggleJobMark(c.tracker, c.id, c.mark, c.now));
  else if (c.op === "merge") out.push(mergeTrackers(c.a, c.b));
  else if (c.op === "count") out.push(countMarks(c.tracker));
  else if (c.op === "parse") out.push(parseBackup(c.text, c.now));
  else if (c.op === "roundtrip") out.push(parseBackup(JSON.stringify(c.tracker, null, 2) + "\\n"));
  else throw new Error("unknown op " + c.op);
}}
process.stdout.write(JSON.stringify(out));
"""
    return _run_node(script)


def _run_drive_sync(scenario: dict) -> dict:
    """Run driveSyncOnce against an in-memory fake Drive."""
    helpers = _extract_js_functions(*TRACKER_FUNCS, *DRIVE_FUNCS)
    script = f"""
{_js_consts("TRACKER_STATUSES", "DRIVE_SCOPE", "DRIVE_FILE_NAME", "DRIVE_API", "DRIVE_UPLOAD")}
{helpers}
const sc = {json.dumps(scenario)};
const files = {{}};
if (sc.remote) files.f1 = JSON.stringify(sc.remote);
const calls = [];
function reply(status, body) {{
  return {{ ok: status < 400, status, json: async () => JSON.parse(body), text: async () => body }};
}}
async function fakeFetch(url, opts) {{
  const o = opts || {{}};
  const method = o.method || "GET";
  const [path, query] = url.split("?");
  calls.push({{ method, path, query: decodeURIComponent(query || ""), auth: (o.headers || {{}}).Authorization }});
  if (sc.status) return reply(sc.status, "{{}}");
  if (method === "GET" && path === DRIVE_API) {{
    return reply(200, JSON.stringify({{ files: Object.keys(files).map((id) => ({{ id }})) }}));
  }}
  if (method === "GET" && path.startsWith(DRIVE_API + "/")) {{
    const id = path.slice(DRIVE_API.length + 1);
    return id in files ? reply(200, files[id]) : reply(404, "{{}}");
  }}
  if (method === "POST" && path === DRIVE_UPLOAD) {{
    const boundary = o.headers["Content-Type"].split("boundary=")[1];
    const parts = o.body.split("--" + boundary);
    const meta = JSON.parse(parts[1].split("\\r\\n\\r\\n")[1]);
    files.created = parts[2].split("\\r\\n\\r\\n")[1].replace(/\\r\\n$/, "");
    calls[calls.length - 1].meta = meta;
    return reply(200, JSON.stringify({{ id: "created" }}));
  }}
  if (method === "PATCH" && path.startsWith(DRIVE_UPLOAD + "/")) {{
    files[path.slice(DRIVE_UPLOAD.length + 1)] = o.body;
    return reply(200, "{{}}");
  }}
  return reply(500, "{{}}");
}}
driveSyncOnce(sc.local, {{ fetchFn: fakeFetch, token: "tok", fileId: sc.fileId || null, jobIds: sc.jobIds || null }})
  .then((res) => ({{ result: res, error: null }}))
  .catch((err) => ({{ result: null, error: err.status || String(err) }}))
  .then((outcome) => {{
    const stored = {{}};
    for (const [id, text] of Object.entries(files)) stored[id] = JSON.parse(text);
    process.stdout.write(JSON.stringify({{ ...outcome, calls, stored }}));
  }});
"""
    return _run_node(script)


class UrgencyHelperTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(urgency_for_days(None), "open")
        self.assertEqual(urgency_for_days(-1), "expired")
        self.assertEqual(urgency_for_days(0), "48h")
        self.assertEqual(urgency_for_days(2), "48h")
        self.assertEqual(urgency_for_days(3), "7d")
        self.assertEqual(urgency_for_days(7), "7d")
        self.assertEqual(urgency_for_days(8), "later")

    def test_location_label(self):
        self.assertEqual(location_label_for(2), "City centre")
        self.assertEqual(location_label_for(1), "Zagreb")
        self.assertEqual(location_label_for(0), "Zagreb")


class ListJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.tmp.name) / "jobs.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _listing(
        self,
        sifra: str,
        *,
        deadline: date | None,
        location_score: int = 1,
        keywords: list[str] | None = None,
    ) -> JobListing:
        return JobListing(
            web_sifra=sifra,
            title=f"Job {sifra}",
            employer="Acme",
            location_raw="ZAGREB",
            deadline_raw="",
            detail_url=f"https://example.test/{sifra}",
            deadline_date=deadline,
            foreign_score=3,
            location_score=location_score,
            matched_keywords=keywords or ["radna dozvola"],
        )

    def test_empty_database(self):
        self.assertEqual(self.store.list_jobs(today=TODAY), [])

    def test_computed_fields_and_sort(self):
        self.store.upsert_job(self._listing("open", deadline=None), digest_day=None)
        self.store.upsert_job(
            self._listing("soon", deadline=TODAY + timedelta(days=1), location_score=2),
            digest_day=2,
        )
        self.store.upsert_job(
            self._listing("week", deadline=TODAY + timedelta(days=6)),
            digest_day=None,
        )
        self.store.upsert_job(
            self._listing("later", deadline=TODAY + timedelta(days=30)),
            digest_day=None,
        )
        self.store.upsert_job(
            self._listing("expired", deadline=TODAY - timedelta(days=1)),
            digest_day=None,
        )
        self.store.mark_notified("soon")

        rows = {job["web_sifra"]: job for job in self.store.list_jobs(today=TODAY)}
        self.assertEqual(set(rows), {"open", "soon", "week", "later", "expired"})

        self.assertIsNone(rows["open"]["deadline_date"])
        self.assertIsNone(rows["open"]["days_until_deadline"])
        self.assertEqual(rows["open"]["urgency"], "open")
        self.assertFalse(rows["open"]["notified"])

        self.assertEqual(rows["soon"]["deadline_date"], "2026-08-22")
        self.assertEqual(rows["soon"]["days_until_deadline"], 1)
        self.assertEqual(rows["soon"]["urgency"], "48h")
        self.assertEqual(rows["soon"]["location_label"], "City centre")
        self.assertTrue(rows["soon"]["notified"])
        self.assertEqual(rows["soon"]["digest_day"], 2)
        self.assertEqual(rows["soon"]["matched_keywords"], "radna dozvola")

        self.assertEqual(rows["week"]["days_until_deadline"], 6)
        self.assertEqual(rows["week"]["urgency"], "7d")
        self.assertEqual(rows["week"]["location_label"], "Zagreb")

        self.assertEqual(rows["later"]["urgency"], "later")
        self.assertEqual(rows["expired"]["urgency"], "expired")
        self.assertEqual(rows["expired"]["days_until_deadline"], -1)

        ordered = [job["web_sifra"] for job in self.store.list_jobs(today=TODAY)]
        self.assertEqual(ordered, ["expired", "soon", "week", "later", "open"])

    def test_inspected_rows_are_not_listed(self):
        inspected = self._listing("skip-me", deadline=TODAY + timedelta(days=4))
        self.store.record_listing(inspected)
        self.store.upsert_job(self._listing("keep-me", deadline=TODAY + timedelta(days=4)))
        jobs = self.store.list_jobs(today=TODAY)
        self.assertEqual([job["web_sifra"] for job in jobs], ["keep-me"])

    def test_row_view_null_deadline(self):
        view = job_row_to_view(
            {
                "web_sifra": "x",
                "title": "T",
                "employer": None,
                "location_raw": None,
                "deadline_date": None,
                "foreign_score": 2,
                "location_score": 2,
                "matched_keywords": None,
                "detail_url": "https://example.test/x",
                "first_seen_at": "2026-08-21T00:00:00",
                "digest_day": None,
                "notified_at": None,
            },
            today=TODAY,
        )
        self.assertEqual(view["urgency"], "open")
        self.assertEqual(view["location_label"], "City centre")
        self.assertFalse(view["notified"])
        self.assertEqual(view["tracks"], ["foreigner_text"])


class ExportWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "jobs.sqlite3"
        self.store = StateStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_empty_export(self):
        dest = Path(self.tmp.name) / "jobs.json"
        write_jobs_json(self.store, path=dest, now=datetime(2026, 8, 21, 9, 0, 0), today=TODAY)
        payload = json.loads(dest.read_text())
        self.assertEqual(payload["jobs"], [])
        self.assertEqual(payload["generated_at"], "2026-08-21T09:00:00Z")
        self.assertEqual(payload["uv_list"]["edition"], "2023-03")
        self.assertIn("sourceUrl", payload["uv_list"])

    def test_seeded_export_and_payload(self):
        self.store.upsert_job(
            JobListing(
                web_sifra="165734230",
                title="INZENJER",
                employer="Dominus",
                location_raw="ZAGREB",
                deadline_raw="",
                detail_url="https://example.test/165734230",
                deadline_date=TODAY + timedelta(days=2),
                foreign_score=3,
                location_score=2,
                matched_keywords=["radnu dozvolu"],
            )
        )
        dest = Path(self.tmp.name) / "jobs.json"
        write_jobs_json(self.store, path=dest, now=datetime(2026, 8, 21, 9, 0, 0), today=TODAY)
        payload = json.loads(dest.read_text())
        self.assertEqual(len(payload["jobs"]), 1)
        job = payload["jobs"][0]
        self.assertEqual(job["web_sifra"], "165734230")
        self.assertEqual(job["urgency"], "48h")
        self.assertEqual(job["location_label"], "City centre")
        self.assertEqual(job["tracks"], ["foreigner_text"])
        self.assertFalse(job["shortage_match"])
        self.assertNotIn("TELEGRAM_BOT_TOKEN", dest.read_text())
        helper = jobs_payload(self.store, now=datetime(2026, 8, 21, 9, 0, 0), today=TODAY)
        self.assertEqual(helper["jobs"][0]["days_until_deadline"], 2)


class PublicBoardStaticTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.root = root
        self.html = (root / "docs" / "index.html").read_text()
        self.css = (root / "docs" / "styles.css").read_text()
        self.js = (root / "docs" / "app.js").read_text()
        self.method = (root / "product" / "METHOD.md").read_text()

    def test_board_is_a_filter_drawer_not_a_local_server(self):
        self.assertIn('id="filters"', self.html)
        self.assertIn("Expiring", self.html)
        self.assertIn("./jobs.json", self.js)
        self.assertIn(".drawer", self.css)
        self.assertNotIn("tabulator", self.html.lower())
        self.assertNotIn("127.0.0.1", self.html)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", self.html + self.js)
        self.assertNotIn("TELEGRAM_CHAT_ID", self.html + self.js)

    def test_empty_checkbox_filters_mean_show_all(self):
        self.assertIn("if (urgency.size && !urgency.has(job.urgency))", self.js)
        self.assertIn("if (location.size && !location.has(job.location_label))", self.js)
        self.assertIn("if (!on.length || on.length === boxes.length)", self.js)
        self.assertNotIn('name="urgency" value="48h" checked', self.html)
        self.assertNotIn('name="location" value="City centre" checked', self.html)

    def test_listing_count_sits_above_results(self):
        main = self.html.split("<main>", 1)[1].split("</main>", 1)[0]
        self.assertLess(main.find('id="listing-count"'), main.find('id="results"'))
        self.assertNotIn('id="row-count"', self.html)

    def test_nav_has_language_and_dark_default_theme_toggle(self):
        self.assertIn('data-theme="dark"', self.html)
        self.assertIn('id="lang-en"', self.html)
        self.assertIn('id="lang-hr"', self.html)
        self.assertIn('id="theme-toggle"', self.html)
        self.assertIn("Fraunces", self.html)
        self.assertIn("Sora", self.html)
        self.assertIn("--accent: #e0a14a", self.css)
        self.assertIn("title_en", self.js)
        self.assertIn("langpair=hr|en", self.js)

    def test_search_and_tools_live_in_main_not_header(self):
        header = self.html.split("<header", 1)[1].split("</header>", 1)[0]
        main = self.html.split("<main>", 1)[1].split("</main>", 1)[0]
        self.assertNotIn('id="search"', header)
        self.assertNotIn('id="sort"', header)
        self.assertNotIn('id="listing-count"', header)
        self.assertIn('id="search"', main)
        self.assertIn('id="sort"', main)
        self.assertIn('id="listing-count"', main)
        self.assertIn('class="board"', self.html)

    def test_desktop_board_grid_keeps_drawer_below_header(self):
        self.assertIn(".board {", self.css)
        self.assertIn("grid-template-columns: 260px minmax(0, 1fr)", self.css)
        self.assertNotRegex(self.css, r"body\s*\{[^}]*grid-template-columns:\s*260px")
        topbar_z = int(re.search(r"\.topbar\s*\{[^}]*z-index:\s*(\d+)", self.css).group(1))
        self.assertGreaterEqual(topbar_z, 50)
        desktop = self.css.split("@media (min-width: 860px)", 1)[1]
        self.assertIn("z-index: 1", desktop)
        self.assertIn("position: sticky", desktop)
        self.assertIn("top: 5.25rem", desktop)
        self.assertIn("align-self: start", desktop)
        self.assertGreater(topbar_z, 1)

    def test_tooltips_on_score_keywords_and_urgency(self):
        self.assertIn("data-tip", self.js)
        self.assertIn("has-tip", self.js)
        self.assertIn("tipScore", self.js)
        self.assertIn("tipKeyword", self.js)
        self.assertIn("tipUrgency48h", self.js)
        self.assertIn("tipLocCentre", self.js)
        self.assertIn("tipTelegram", self.js)
        self.assertIn('.has-tip', self.css)
        self.assertIn("content: attr(data-tip)", self.css)
        self.assertIn('.split(",")', self.js)

    def test_method_page_linked_and_bilingual(self):
        self.assertTrue((self.root / "product" / "METHOD.md").is_file())
        self.assertFalse((self.root / "METHOD.md").exists())
        self.assertFalse((self.root / "docs" / "method.html").exists())
        self.assertIn(
            "github.com/JMNofziger/burza-rada-stranci-filter/blob/main/product/METHOD.md",
            self.html,
        )
        self.assertNotIn('href="./method.html"', self.html)
        self.assertIn('data-i18n="method"', self.html)
        self.assertIn('method: "Method"', self.js)
        self.assertIn("WebSifra", self.method)
        self.assertIn("FOREIGN_SCORE_THRESHOLD", self.method)
        self.assertIn("Grad Zagreb", self.method)
        self.assertIn("Svi poslovi", self.method)
        self.assertIn("Track A", self.method)
        self.assertIn("Track B", self.method)
        self.assertIn("Kolosijek A", self.method)
        self.assertIn("Kolosijek B", self.method)
        self.assertIn("uv-occupations.json", self.method)

    def test_method_page_documents_foreigner_score_lexicon(self):
        import config

        self.assertIn("Weight 3", self.method)
        self.assertIn("Weight 2", self.method)
        self.assertIn("Weight 1", self.method)
        self.assertIn("Težine 3 / 2 / 1", self.method)
        self.assertIn("radna dozvola", self.method)
        self.assertIn("strani državljani", self.method)
        self.assertIn("NEGATION_WINDOW_CHARS = 25", self.method)
        self.assertIn("FOREIGN_SCORE_THRESHOLD = 2", self.method)
        self.assertIn("isključivo eu", self.method)
        self.assertIn("samo eu državljani", self.method)
        for phrase in config.FOREIGNER_KEYWORDS:
            self.assertIn(f"`{phrase}`", self.method)
        for marker in config.NEGATION_MARKERS:
            self.assertIn(f"`{marker}`", self.method)
        self.assertGreaterEqual(self.method.count("radna dozvola"), 2)
        self.assertGreaterEqual(self.method.count("strani državljani"), 2)

    def test_board_has_shortage_track_filter(self):
        self.assertIn('name="track"', self.html)
        self.assertIn('value="foreigner_text"', self.html)
        self.assertIn('value="shortage_occupation"', self.html)
        self.assertIn("tipShortage", self.js)
        self.assertIn("job.shortage_match", self.js)
        self.assertIn("job.tracks", self.js)

    def test_title_case_helper_keeps_abbreviations(self):
        self.assertIn("function toDisplayCase", self.js)
        self.assertIn("ZDRAVSTVENA USTANOVA", self.js)
        self.assertIn("VPR EVENTS", self.js)
        self.assertIn("Acme d.o.o.", self.js)
        self.assertIn("d.o.o", self.js)
        self.assertIn("letterCount < 2 || letterCount > 5", self.js)
        self.assertIn("toDisplayCase(displayTitle(job))", self.js)
        self.assertIn("toDisplayCase(job.employer)", self.js)
        self.assertIn("toDisplayCase(job.location_raw", self.js)
        self.assertIn("Display-only ALL CAPS", self.js)

    def test_location_is_own_block_on_cards(self):
        self.assertIn('class="card-location"', self.js)
        self.assertIn(".card-location", self.css)
        self.assertRegex(self.css, r"\.card-location\s*\{[^}]*display:\s*block")
        self.assertNotIn("job.employer)} · ${escapeHtml(t(locKey))}", self.js)
        self.assertNotIn("job.employer)} ·", self.js)
        self.assertIn("job.location_raw || t(locKey)", self.js)
        employer_line = [line for line in self.js.splitlines() if "class=\"employer\"" in line]
        self.assertTrue(employer_line)
        self.assertNotIn(" · ", employer_line[0])
        self.assertNotIn("locKey", employer_line[0])

    def test_job_mark_buttons_and_my_jobs_filter(self):
        self.assertIn('name="myjobs"', self.html)
        self.assertIn('value="all" checked', self.html)
        self.assertIn('value="interested"', self.html)
        self.assertIn('value="applied"', self.html)
        self.assertIn('data-i18n="myJobs"', self.html)
        self.assertIn('data-i18n="trackerNote"', self.html)
        self.assertIn('li class="card"', self.js)
        self.assertIn('class="card-link"', self.js)
        self.assertIn('data-mark="interested"', self.js)
        self.assertIn('data-mark="applied"', self.js)
        self.assertIn("aria-pressed", self.js)
        self.assertIn('closest("button[data-mark]")', self.js)
        self.assertIn('input[name=myjobs]:checked', self.js)
        self.assertIn('myJobs === "interested"', self.js)
        self.assertIn(".card-marks", self.css)
        self.assertIn(".filter-note", self.css)
        self.assertIn('markInterested: "Interested"', self.js)
        self.assertIn('markInterested: "Zanima me"', self.js)
        self.assertIn('markApplied: "Applied"', self.js)
        self.assertIn('markApplied: "Prijavljeno"', self.js)
        self.assertIn("Saved in this browser only", self.js + self.html)
        self.assertIn("Samo u ovom pregledniku", self.js)

    def test_tracker_storage_key_and_graceful_failure(self):
        self.assertIn('TRACKER_KEY = "hzz-job-tracker"', self.js)
        self.assertIn("function loadTracker", self.js)
        self.assertIn("function saveTracker", self.js)
        self.assertIn("return emptyTracker()", self.js)
        self.assertIn("return false", self.js)
        self.assertIn("state.tracker = loadTracker()", self.js)
        self.assertIn("pruneTracker(state.tracker, ids)", self.js)
        # Failure paths must not throw out of load/save.
        self.assertRegex(
            self.js,
            r"function loadTracker\(\) \{\s*try \{[\s\S]*?catch \(err\) \{\s*return emptyTracker\(\);\s*\}",
        )
        self.assertRegex(
            self.js,
            r"function saveTracker\(tracker\) \{\s*try \{[\s\S]*?catch \(err\) \{\s*return false;\s*\}",
        )


def _entry(s, t):
    return {"s": s, "t": t}


class JobTrackerHelperTests(unittest.TestCase):
    def test_normalize_rejects_malformed_storage(self):
        results = _run_tracker_cases(
            [
                {"op": "normalize", "raw": None},
                {"op": "normalize", "raw": []},
                {"op": "normalize", "raw": {"version": 3, "statuses": {}}},
                {"op": "normalize", "raw": {"version": 2, "statuses": "nope"}},
                {
                    "op": "normalize",
                    "raw": {
                        "version": 2,
                        "statuses": {
                            "ok": _entry("interested", 5),
                            "cleared": _entry(None, 6),
                            "bad-status": _entry("maybe", 1),
                            "no-time": {"s": "applied"},
                            "flat": "applied",
                            "": _entry("applied", 1),
                        },
                    },
                },
            ]
        )
        empty = {"version": 2, "statuses": {}}
        self.assertEqual(results[:4], [empty, empty, empty, empty])
        self.assertEqual(
            results[4],
            {"version": 2, "statuses": {"ok": _entry("interested", 5), "cleared": _entry(None, 6)}},
        )

    def test_v1_migrates_with_migration_time(self):
        migrated = _run_tracker_cases(
            [
                {
                    "op": "normalize",
                    "now": 1000,
                    "raw": {"version": 1, "statuses": {"a": "interested", "b": "applied", "c": "maybe"}},
                }
            ]
        )[0]
        self.assertEqual(
            migrated,
            {"version": 2, "statuses": {"a": _entry("interested", 1000), "b": _entry("applied", 1000)}},
        )

    def test_status_transitions_set_clear_and_supersede(self):
        base = {"version": 2, "statuses": {}}
        interested = _run_tracker_cases(
            [{"op": "toggle", "tracker": base, "id": "j1", "mark": "interested", "now": 10}]
        )[0]
        self.assertEqual(interested, {"version": 2, "statuses": {"j1": _entry("interested", 10)}})

        applied = _run_tracker_cases(
            [{"op": "toggle", "tracker": interested, "id": "j1", "mark": "applied", "now": 20}]
        )[0]
        self.assertEqual(applied, {"version": 2, "statuses": {"j1": _entry("applied", 20)}})

        cleared = _run_tracker_cases(
            [{"op": "toggle", "tracker": applied, "id": "j1", "mark": "applied", "now": 30}]
        )[0]
        self.assertEqual(cleared, {"version": 2, "statuses": {"j1": _entry(None, 30)}})

        never_set = _run_tracker_cases(
            [{"op": "set", "tracker": base, "id": "j9", "status": None, "now": 40}]
        )[0]
        self.assertEqual(never_set, base)

        statuses = _run_tracker_cases(
            [
                {"op": "get", "tracker": applied, "id": "j1"},
                {"op": "get", "tracker": cleared, "id": "j1"},
                {"op": "count", "tracker": cleared},
            ]
        )
        self.assertEqual(statuses, ["applied", None, 0])

    def test_merge_keeps_newest_entry_and_deletion_markers(self):
        laptop = {
            "version": 2,
            "statuses": {
                "a": _entry("interested", 10),
                "b": _entry("applied", 50),
                "c": _entry(None, 40),
                "tie": _entry("interested", 5),
            },
        }
        phone = {
            "version": 2,
            "statuses": {
                "a": _entry("applied", 20),
                "b": _entry(None, 30),
                "c": _entry("interested", 35),
                "d": _entry("interested", 1),
                "tie": _entry("applied", 5),
            },
        }
        merged, swapped = _run_tracker_cases(
            [{"op": "merge", "a": laptop, "b": phone}, {"op": "merge", "a": phone, "b": laptop}]
        )
        expected = {
            "version": 2,
            "statuses": {
                "a": _entry("applied", 20),
                "b": _entry("applied", 50),
                "c": _entry(None, 40),
                "d": _entry("interested", 1),
                "tie": _entry("applied", 5),
            },
        }
        self.assertEqual(merged, expected)
        self.assertEqual(swapped, expected)

    def test_prune_drops_ids_absent_from_feed_including_markers(self):
        tracker = {
            "version": 2,
            "statuses": {
                "keep": _entry("interested", 1),
                "keep-cleared": _entry(None, 2),
                "gone": _entry("applied", 3),
                "gone-cleared": _entry(None, 4),
            },
        }
        pruned = _run_tracker_cases(
            [{"op": "prune", "tracker": tracker, "ids": ["keep", "keep-cleared", "other"]}]
        )[0]
        self.assertEqual(
            pruned,
            {"version": 2, "statuses": {"keep": _entry("interested", 1), "keep-cleared": _entry(None, 2)}},
        )

    def test_backup_parse_rejects_bad_files_and_round_trips(self):
        tracker = {"version": 2, "statuses": {"a": _entry("applied", 7), "b": _entry(None, 8)}}
        results = _run_tracker_cases(
            [
                {"op": "parse", "text": "not json"},
                {"op": "parse", "text": "[]"},
                {"op": "parse", "text": '{"version": 9, "statuses": {}}'},
                {"op": "parse", "text": '{"version": 2}'},
                {"op": "parse", "text": '{"version": 1, "statuses": {"x": "interested"}}', "now": 99},
                {"op": "roundtrip", "tracker": tracker},
            ]
        )
        self.assertEqual(results[:4], [None, None, None, None])
        self.assertEqual(results[4], {"version": 2, "statuses": {"x": _entry("interested", 99)}})
        self.assertEqual(results[5], tracker)


class DriveSyncTests(unittest.TestCase):
    LOCAL = {"version": 2, "statuses": {"a": _entry("interested", 10)}}

    def test_creates_file_in_app_folder_when_missing(self):
        out = _run_drive_sync({"local": self.LOCAL, "remote": None})
        self.assertIsNone(out["error"])
        self.assertTrue(out["result"]["wrote"])
        self.assertEqual(out["result"]["fileId"], "created")
        self.assertEqual(out["stored"]["created"], self.LOCAL)
        listing = out["calls"][0]
        self.assertEqual(listing["method"], "GET")
        self.assertIn("spaces=appDataFolder", listing["query"])
        self.assertIn("name='hzz-job-tracker.json'", listing["query"])
        create = out["calls"][-1]
        self.assertEqual(create["method"], "POST")
        self.assertIn("uploadType=multipart", create["query"])
        self.assertEqual(create["meta"]["parents"], ["appDataFolder"])
        self.assertEqual(create["meta"]["name"], "hzz-job-tracker.json")
        self.assertTrue(all(call["auth"] == "Bearer tok" for call in out["calls"]))

    def test_reads_merges_prunes_and_writes(self):
        remote = {
            "version": 2,
            "statuses": {"a": _entry("applied", 20), "b": _entry("interested", 5), "gone": _entry("applied", 1)},
        }
        local = {"version": 2, "statuses": {"a": _entry("interested", 10), "c": _entry("applied", 30)}}
        out = _run_drive_sync({"local": local, "remote": remote, "jobIds": ["a", "b", "c"]})
        self.assertIsNone(out["error"])
        expected = {
            "version": 2,
            "statuses": {"a": _entry("applied", 20), "b": _entry("interested", 5), "c": _entry("applied", 30)},
        }
        self.assertEqual(out["result"]["tracker"], expected)
        self.assertTrue(out["result"]["wrote"])
        self.assertEqual(out["stored"]["f1"], expected)
        patch = out["calls"][-1]
        self.assertEqual(patch["method"], "PATCH")
        self.assertIn("uploadType=media", patch["query"])

    def test_no_write_when_unchanged(self):
        out = _run_drive_sync({"local": self.LOCAL, "remote": self.LOCAL, "fileId": "f1"})
        self.assertIsNone(out["error"])
        self.assertFalse(out["result"]["wrote"])
        self.assertEqual([call["method"] for call in out["calls"]], ["GET"])

    def test_stale_device_does_not_overwrite_newer_marks(self):
        remote = {"version": 2, "statuses": {"a": _entry(None, 50)}}
        out = _run_drive_sync({"local": self.LOCAL, "remote": remote, "fileId": "f1"})
        self.assertEqual(out["result"]["tracker"], remote)
        self.assertFalse(out["result"]["wrote"])

    def test_missing_cached_file_is_found_again(self):
        out = _run_drive_sync({"local": self.LOCAL, "remote": self.LOCAL, "fileId": "deleted"})
        self.assertIsNone(out["error"])
        self.assertEqual(out["result"]["fileId"], "f1")

    def test_expired_token_surfaces_401(self):
        out = _run_drive_sync({"local": self.LOCAL, "remote": self.LOCAL, "status": 401})
        self.assertEqual(out["error"], 401)
        self.assertIsNone(out["result"])


class SyncConfigTests(unittest.TestCase):
    def _evaluate(self, client_id: str) -> str:
        from web.sync_config import write_sync_config

        with tempfile.TemporaryDirectory() as tmp:
            path = write_sync_config(client_id, Path(tmp) / "sync-config.js")
            script = (
                "global.window = {};\n"
                + path.read_text()
                + "\nprocess.stdout.write(JSON.stringify(window.HZZ_GOOGLE_CLIENT_ID));"
            )
            return _run_node(script)

    def test_writes_valid_js_for_empty_normal_and_quoted_values(self):
        self.assertEqual(self._evaluate(""), "")
        self.assertEqual(self._evaluate("  123-abc.apps.googleusercontent.com \n"), "123-abc.apps.googleusercontent.com")
        tricky = 'a"b\\c</script>'
        self.assertEqual(self._evaluate(tricky), tricky)

    def test_committed_config_has_no_client_id(self):
        committed = (ROOT / "docs" / "sync-config.js").read_text()
        self.assertEqual(committed, 'window.HZZ_GOOGLE_CLIENT_ID = "";\n')


class GoogleSyncStaticTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "docs" / "index.html").read_text()
        self.js = APP_JS
        self.guide = (ROOT / "product" / "GOOGLE_SYNC_SETUP.md").read_text()

    def test_scope_is_app_folder_only(self):
        self.assertIn('DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.appdata"', self.js)
        scopes = set(re.findall(r"https://www\.googleapis\.com/auth/[\w.]+", self.js))
        self.assertEqual(scopes, {"https://www.googleapis.com/auth/drive.appdata"})

    def test_block_hidden_without_client_id(self):
        self.assertIn('id="sync-box" class="sync-box" hidden', self.html)
        self.assertIn("box.hidden = !GOOGLE_CLIENT_ID", self.js)
        self.assertLess(self.html.find("./sync-config.js"), self.html.find("./app.js"))
        self.assertIn("window.HZZ_GOOGLE_CLIENT_ID", self.js)

    def test_token_never_persisted(self):
        for line in self.js.splitlines():
            if "sync.token" in line or "access_token" in line:
                self.assertNotIn("Storage", line)
                self.assertNotIn("setItem", line)
        self.assertNotIn("sessionStorage", self.js)

    def test_expired_session_shows_reconnect(self):
        self.assertRegex(
            self.js,
            r'if \(err\.status === 401\) \{\s*sync\.token = null;\s*setSyncStatus\("expired"\);',
        )
        self.assertIn('t(reconnect ? "syncReconnect" : "syncSignIn")', self.js)
        self.assertIn('syncReconnect: "Reconnect to Google"', self.js)

    def test_plain_language_and_in_app_notice(self):
        for text in (
            'syncSignIn: "Save my list to Google"',
            "It can only see this one list, not your files.",
            "To save to Google, open this page in Safari or Chrome.",
            'syncSignOut: "Stop saving to Google"',
            'backupDownload: "Download backup file"',
            'syncSignIn: "Spremi moj popis na Google"',
        ):
            self.assertIn(text, self.js)
        self.assertIn("Telegram", self.js)
        self.assertIn('"popup_failed_to_open"', self.js)
        self.assertIn('<details class="more-options">', self.html)

    def test_deploy_workflows_write_config_before_upload(self):
        for name in ("daily.yml", "full-scrape.yml", "pages.yml"):
            text = (ROOT / ".github" / "workflows" / name).read_text()
            self.assertIn("GOOGLE_CLIENT_ID: ${{ vars.GOOGLE_CLIENT_ID }}", text, name)
            write_at = text.find("python3 -m web.sync_config")
            self.assertGreater(write_at, 0, name)
            self.assertLess(write_at, text.find("upload-pages-artifact"), name)
            persist_at = text.find("persist-state.sh")
            if persist_at >= 0:
                self.assertLess(persist_at, write_at, name)
        pages = (ROOT / ".github" / "workflows" / "pages.yml").read_text()
        self.assertIn("workflow_dispatch:", pages)

    def test_setup_guide_has_exact_values(self):
        for value in (
            "https://jmnofziger.github.io",
            "GOOGLE_CLIENT_ID",
            "drive.appdata",
            "**Variables** tab (not Secrets)",
            "Publish app",
            "origin_mismatch",
            "Deploy jobs board",
        ):
            self.assertIn(value, self.guide)
        self.assertNotIn("https://jmnofziger.github.io/\n", self.guide)


if __name__ == "__main__":
    unittest.main()
