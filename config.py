"""
config.py
Central, single-source-of-truth configuration for the HZZ Zagreb job pipeline.

NOTE ON UNVERIFIED FIELDS
--------------------------
The live search form's exact ASP.NET control names (for filtering server-side
by županija/grad, or for postback pagination) could NOT be confirmed at the
time this package was written -- direct fetches of
https://burzarada.hzz.hr/Posloprimac_RadnaMjesta.aspx redirected in a loop,
which is itself diagnostic (see README "Known unknowns"). Anything below
marked TODO/VERIFY must be confirmed once against the live DOM using your
browser's dev tools (Network tab) before first real run.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Site endpoints
# ---------------------------------------------------------------------------
BASE_URL = "https://burzarada.hzz.hr"
LIST_URL = f"{BASE_URL}/Posloprimac_RadnaMjesta.aspx"
# Confirmed live pattern from real job postings (see README): detail pages are
# addressed by a stable numeric "WebSifra" -- use this as the canonical job ID,
# NOT a derived hash.
DETAIL_URL_TEMPLATE = f"{BASE_URL}/RadnoMjesto_Ispis.aspx?WebSifra={{web_sifra}}&AspxAutoDetectCookieSupport=1"

# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------
USER_AGENT = "hzz-zagreb-job-digest/1.0 (personal job search tool; contact: you@example.com)"
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_DELAY_SECONDS = 1.0        # politeness delay between sequential requests
DETAIL_FETCH_MAX_WORKERS = 4       # bounded concurrency for detail-page fetches
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 1.5

# ---------------------------------------------------------------------------
# Storage / delivery
# ---------------------------------------------------------------------------
DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "hzz_jobs.sqlite3"

TELEGRAM_ENV_TOKEN = "TELEGRAM_BOT_TOKEN"
TELEGRAM_ENV_CHAT_ID = "TELEGRAM_CHAT_ID"

# ---------------------------------------------------------------------------
# Digest / urgency
# ---------------------------------------------------------------------------
DIGEST_DAYS = 6
URGENT_WITHIN_HOURS = 48

# ---------------------------------------------------------------------------
# Foreign-worker-friendliness keyword lexicon (weighted, not boolean)
# ---------------------------------------------------------------------------
# Weight rationale:
#   3 = near-unambiguous legal/administrative phrasing specific to
#       third-country-national hiring
#   2 = strong supporting signal, occasionally used in unrelated contexts
#   1 = weak/ambiguous signal, only meaningful combined with other hits
#
# Each entry lists the canonical (nominative/dictionary) form plus the most
# common inflected forms we expect to see in real ad text. This is a cheap,
# high-value substitute for full lemmatization (see README "Enhancements").
FOREIGNER_KEYWORDS: dict[str, int] = {
    # dozvola za boravak i rad ("residence and work permit") + case variants
    "dozvola za boravak i rad": 3,
    "dozvolu za boravak i rad": 3,
    "dozvole za boravak i rad": 3,
    "radna dozvola": 3,
    "radnu dozvolu": 3,
    "radne dozvole": 3,
    # third-country nationals
    "državljani trećih zemalja": 3,
    "državljanima trećih zemalja": 3,
    "državljana trećih zemalja": 3,
    "strani državljani": 2,
    "strane radnike": 2,
    "strani radnici": 2,
    "strana radna snaga": 2,
    # employer-side sponsorship / support language
    "sponzorstvo": 2,
    "ishođenje dozvole": 3,
    "ishođenje radne dozvole": 3,
    "pomoć oko dozvole": 2,
    "posredovanje pri ishođenju": 2,
    # accommodation offers (strong correlate with recruiting non-local/foreign
    # labour, but weaker signal on its own -- keep weight lower)
    "osiguran smještaj": 1,
    "smještaj osiguran": 1,
    "smještaj obezbijeđen": 1,
    "besplatan smještaj": 1,
    # field/seasonal work often paired with foreign labour recruitment
    "terenski rad": 1,
}

# Phrases that, if found within a short window before/after a keyword hit,
# should suppress that hit (cheap negation guard -- see scoring.py).
NEGATION_MARKERS = ["ne ", "nije ", "nema ", "bez ", "isključivo eu", "samo eu državljani"]

FOREIGN_SCORE_THRESHOLD = 2  # minimum accumulated weight to flag a job

# ---------------------------------------------------------------------------
# Zagreb / city-centre location signals
# ---------------------------------------------------------------------------
ZAGREB_PATTERN = r"\bzagreb\w*\b"

CENTAR_PATTERNS = [
    r"\b10000\b",
    r"\bcentar\b",
    r"\bdonji\s+grad\b",
    r"\bgornji\s+grad\b",
    r"\bmedveščak\b",
    r"\btrg\s+(bana\s+)?jelačića\b",   # "Trg bana Jelačića" is the official name
    r"\bilica\b",
]
