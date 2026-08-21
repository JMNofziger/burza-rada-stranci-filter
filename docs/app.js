const I18N = {
  en: {
    title: "Matching jobs",
    subtitle: "Grad Zagreb · open to third-country nationals",
    method: "Method",
    listings: "listings",
    filters: "Filters",
    done: "Done",
    search: "Search title, employer, keywords",
    sort: "Sort",
    sortDeadline: "Expiry (soonest)",
    sortNewest: "Newest seen",
    sortScore: "Foreign score",
    sortTitle: "Title A–Z",
    updated: "Updated",
    expiring: "Expiring",
    u48h: "Within 48 hours",
    u7d: "Within 7 days",
    ulater: "Later",
    uopen: "Open-ended",
    uexpired: "Expired",
    location: "Location",
    locCentre: "City centre",
    locZagreb: "Zagreb",
    telegram: "Telegram",
    tgAny: "Any",
    tgNo: "Not sent yet",
    tgYes: "Already sent",
    tgSent: "Telegram",
    employer: "Employer",
    clear: "Clear filters",
    empty: "No listings match these filters.",
    emptyNone: "No matching jobs yet. The board updates after each successful daily collect.",
    expires: "Expires",
    openEnded: "open-ended",
    score: "score",
    theme: "Switch color theme",
    tipScore: "Weighted phrase hits in title, employer, and detail; threshold 2 to appear on this board.",
    tipKeyword: "Phrase that contributed to the foreigner score.",
    tipUrgency48h: "Application deadline within 48 hours (listing deadline vs generated_at).",
    tipUrgency7d: "Application deadline within 7 days (listing deadline vs generated_at).",
    tipUrgencyLater: "Application deadline more than 7 days away.",
    tipUrgencyOpen: "No application deadline on the listing.",
    tipUrgencyExpired: "Deadline has passed; kept for 3 days.",
    tipLocCentre: "Location score 2: Zagreb city centre.",
    tipLocZagreb: "Location score 1: Zagreb, not city centre.",
    tipTelegram: "Already included in a Telegram digest.",
  },
  hr: {
    title: "Odgovarajući poslovi",
    subtitle: "Grad Zagreb · otvoreno državljanima trećih zemalja",
    method: "Metoda",
    listings: "oglasi",
    filters: "Filtri",
    done: "Gotovo",
    search: "Pretraži naslov, poslodavca, ključne riječi",
    sort: "Razvrstaj",
    sortDeadline: "Istek (najranije)",
    sortNewest: "Najnovije",
    sortScore: "Strani rezultat",
    sortTitle: "Naslov A–Z",
    updated: "Ažurirano",
    expiring: "Istek",
    u48h: "Unutar 48 sati",
    u7d: "Unutar 7 dana",
    ulater: "Kasnije",
    uopen: "Bez roka",
    uexpired: "Isteklo",
    location: "Lokacija",
    locCentre: "Centar grada",
    locZagreb: "Zagreb",
    telegram: "Telegram",
    tgAny: "Sve",
    tgNo: "Još nije poslano",
    tgYes: "Već poslano",
    tgSent: "Telegram",
    employer: "Poslodavac",
    clear: "Očisti filtre",
    empty: "Nijedan oglas ne odgovara filterima.",
    emptyNone: "Još nema odgovarajućih oglasa. Ploča se ažurira nakon svakog uspješnog dnevnog prikupljanja.",
    expires: "Istek",
    openEnded: "bez roka",
    score: "rezultat",
    theme: "Promijeni temu",
    tipScore: "Ponderirani pogoci fraza u naslovu, poslodavcu i opisu; prag 2 za prikaz na ovoj ploči.",
    tipKeyword: "Fraza koja je pridonijela stranom rezultatu.",
    tipUrgency48h: "Rok prijave unutar 48 sati (rok oglasa naspram generated_at).",
    tipUrgency7d: "Rok prijave unutar 7 dana (rok oglasa naspram generated_at).",
    tipUrgencyLater: "Rok prijave kasnije od 7 dana.",
    tipUrgencyOpen: "Oglas nema rok prijave.",
    tipUrgencyExpired: "Rok je prošao; ostaje 3 dana.",
    tipLocCentre: "Lokacijski rezultat 2: centar Zagreba.",
    tipLocZagreb: "Lokacijski rezultat 1: Zagreb, nije centar.",
    tipTelegram: "Već uključeno u Telegram sažetak.",
  },
};

const URGENCY_LABEL = {
  en: { "48h": "48h", "7d": "7d", later: "later", open: "open", expired: "expired" },
  hr: { "48h": "48h", "7d": "7d", later: "kasnije", open: "bez roka", expired: "isteklo" },
};

const URGENCY_TIP = {
  "48h": "tipUrgency48h",
  "7d": "tipUrgency7d",
  later: "tipUrgencyLater",
  open: "tipUrgencyOpen",
  expired: "tipUrgencyExpired",
};

const TITLE_CACHE_KEY = "hzz-title-en";
const LANG_KEY = "hzz-lang";
const THEME_KEY = "hzz-theme";

const state = {
  jobs: [],
  generatedAt: null,
  lang: "en",
  translating: false,
};

function $(id) {
  return document.getElementById(id);
}

const IS_BOARD = Boolean($("results"));

function t(key) {
  return (I18N[state.lang] && I18N[state.lang][key]) || I18N.en[key] || key;
}

function loadTitleCache() {
  try {
    return JSON.parse(localStorage.getItem(TITLE_CACHE_KEY) || "{}");
  } catch (err) {
    return {};
  }
}

function saveTitleCache(cache) {
  try {
    localStorage.setItem(TITLE_CACHE_KEY, JSON.stringify(cache));
  } catch (err) {
    /* quota */
  }
}

function titleCacheKey(job) {
  return job.web_sifra + "\n" + (job.title || "");
}

function applyCachedTitles(jobs) {
  const cache = loadTitleCache();
  for (const job of jobs) {
    const cached = cache[titleCacheKey(job)];
    if (cached) job.title_en = cached;
  }
}

function displayTitle(job) {
  if (state.lang === "en" && job.title_en) return job.title_en;
  return job.title || "";
}

function listingCountText(shown, total) {
  if (state.lang === "hr") {
    if (shown === total) {
      return total === 1 ? "1 oglas" : `${total} oglasa`;
    }
    return `${shown} od ${total} oglasa`;
  }
  if (shown === total) {
    return `${total} listing${total === 1 ? "" : "s"}`;
  }
  return `${shown} of ${total} listings`;
}

function applyI18n() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key && I18N[state.lang][key]) el.textContent = I18N[state.lang][key];
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key && I18N[state.lang][key]) el.setAttribute("placeholder", I18N[state.lang][key]);
  });
  document.querySelectorAll("[data-lang-panel]").forEach((el) => {
    el.hidden = el.getAttribute("data-lang-panel") !== state.lang;
  });
  const themeToggle = $("theme-toggle");
  if (themeToggle) themeToggle.setAttribute("aria-label", t("theme"));
  const langEn = $("lang-en");
  const langHr = $("lang-hr");
  if (langEn) langEn.setAttribute("aria-pressed", state.lang === "en" ? "true" : "false");
  if (langHr) langHr.setAttribute("aria-pressed", state.lang === "hr" ? "true" : "false");
}

function setLang(lang) {
  state.lang = lang === "hr" ? "hr" : "en";
  try {
    localStorage.setItem(LANG_KEY, state.lang);
  } catch (err) {
    /* ignore */
  }
  applyI18n();
  if (!IS_BOARD) return;
  render();
  if (state.lang === "en") translateVisibleTitles();
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function setTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (err) {
    /* ignore */
  }
}

function openDrawer() {
  const filters = $("filters");
  const backdrop = $("backdrop");
  const openBtn = $("filter-open");
  if (filters) filters.classList.add("open");
  if (backdrop) backdrop.hidden = false;
  if (openBtn) openBtn.setAttribute("aria-expanded", "true");
}

function closeDrawer() {
  const filters = $("filters");
  const backdrop = $("backdrop");
  const openBtn = $("filter-open");
  if (filters) filters.classList.remove("open");
  if (backdrop) backdrop.hidden = true;
  if (openBtn) openBtn.setAttribute("aria-expanded", "false");
}

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(
    (el) => el.value
  );
}

function selectedEmployers() {
  const boxes = [...document.querySelectorAll("#employer-filters input")];
  if (!boxes.length) return null;
  const on = boxes.filter((el) => el.checked).map((el) => el.value);
  if (!on.length || on.length === boxes.length) return null;
  return new Set(on);
}

function applyFilters(jobs) {
  const q = ($("search") && $("search").value.trim().toLowerCase()) || "";
  const urgency = new Set(checkedValues("urgency"));
  const location = new Set(checkedValues("location"));
  const notifiedEl = document.querySelector("input[name=notified]:checked");
  const notified = notifiedEl ? notifiedEl.value : "any";
  const employers = selectedEmployers();
  return jobs.filter((job) => {
    if (urgency.size && !urgency.has(job.urgency)) return false;
    if (location.size && !location.has(job.location_label)) return false;
    if (notified === "yes" && !job.notified) return false;
    if (notified === "no" && job.notified) return false;
    if (employers && !employers.has(job.employer || "(unknown)")) return false;
    if (!q) return true;
    const blob = [
      job.title,
      job.title_en,
      displayTitle(job),
      job.employer,
      job.matched_keywords,
      job.location_raw,
      job.web_sifra,
    ]
      .join(" ")
      .toLowerCase();
    return blob.includes(q);
  });
}

function sortJobs(jobs) {
  const sortEl = $("sort");
  const mode = sortEl ? sortEl.value : "deadline";
  const copy = [...jobs];
  copy.sort((a, b) => {
    if (mode === "title") {
      return displayTitle(a).localeCompare(displayTitle(b), state.lang === "hr" ? "hr" : "en");
    }
    if (mode === "score") return (b.foreign_score || 0) - (a.foreign_score || 0);
    if (mode === "newest") return (b.first_seen_at || "").localeCompare(a.first_seen_at || "");
    const ad = a.deadline_date || "9999-12-31";
    const bd = b.deadline_date || "9999-12-31";
    return ad.localeCompare(bd);
  });
  return copy;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function tipSpan(className, label, tipKey) {
  const tip = t(tipKey);
  return `<span class="${className} has-tip" data-tip="${escapeHtml(tip)}" title="${escapeHtml(tip)}">${escapeHtml(label)}</span>`;
}

function keywordPills(job) {
  const raw = job.matched_keywords || "";
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((kw) => tipSpan("badge muted-pill", kw, "tipKeyword"))
    .join("");
}

function renderEmployers(jobs) {
  const box = $("employer-filters");
  if (!box) return;
  const names = [...new Set(jobs.map((j) => j.employer || "(unknown)"))].sort();
  box.innerHTML = names
    .map(
      (name) =>
        `<label><input type="checkbox" value="${escapeHtml(name)}"> ${escapeHtml(name)}</label>`
    )
    .join("");
}

function render() {
  if (!IS_BOARD) return;
  const filtered = sortJobs(applyFilters(state.jobs));
  const total = state.jobs.length;
  $("listing-count").textContent = listingCountText(filtered.length, total);
  $("empty").hidden = filtered.length > 0 || total === 0;
  if (total === 0) {
    $("empty").hidden = false;
    $("empty").textContent = t("emptyNone");
  } else if (!filtered.length) {
    $("empty").textContent = t("empty");
  }
  const list = $("results");
  const urgencyLabels = URGENCY_LABEL[state.lang] || URGENCY_LABEL.en;
  list.innerHTML = filtered
    .map((job) => {
      const expiry = job.deadline_date || t("openEnded");
      const days =
        job.days_until_deadline === null || job.days_until_deadline === undefined
          ? ""
          : ` · ${job.days_until_deadline}d`;
      const locKey = job.location_label === "City centre" ? "locCentre" : "locZagreb";
      const locTip = job.location_label === "City centre" ? "tipLocCentre" : "tipLocZagreb";
      const urgencyTip = URGENCY_TIP[job.urgency] || "tipUrgencyLater";
      return `<li>
        <a class="card" href="${escapeHtml(job.detail_url)}" target="_blank" rel="noopener">
          <h3>${escapeHtml(displayTitle(job))}</h3>
          <p class="employer">${escapeHtml(job.employer)} · ${escapeHtml(t(locKey))}</p>
          <p>${escapeHtml(t("expires"))} ${escapeHtml(expiry)}${escapeHtml(days)}</p>
          <div class="badges">
            ${tipSpan("badge u-" + escapeHtml(job.urgency), urgencyLabels[job.urgency] || job.urgency, urgencyTip)}
            ${tipSpan("badge muted-pill", t("score") + " " + (job.foreign_score ?? ""), "tipScore")}
            ${keywordPills(job)}
            ${job.location_label ? tipSpan("badge muted-pill", t(locKey), locTip) : ""}
            ${job.notified ? tipSpan("badge muted-pill", t("tgSent"), "tipTelegram") : ""}
          </div>
        </a>
      </li>`;
    })
    .join("");
}

async function translateOne(job) {
  if (job.title_en || !job.title) return;
  const cache = loadTitleCache();
  const key = titleCacheKey(job);
  if (cache[key]) {
    job.title_en = cache[key];
    return;
  }
  const url =
    "https://api.mymemory.translated.net/get?q=" +
    encodeURIComponent(job.title.slice(0, 450)) +
    "&langpair=hr|en";
  const res = await fetch(url);
  if (!res.ok) return;
  const data = await res.json();
  const translated = (data && data.responseData && data.responseData.translatedText) || "";
  if (!translated || translated.toLowerCase() === job.title.toLowerCase()) return;
  job.title_en = translated;
  cache[key] = translated;
  saveTitleCache(cache);
}

async function translateVisibleTitles() {
  if (!IS_BOARD || state.lang !== "en" || state.translating) return;
  const pending = state.jobs.filter((job) => job.title && !job.title_en);
  if (!pending.length) return;
  state.translating = true;
  try {
    for (const job of pending) {
      if (state.lang !== "en") break;
      try {
        await translateOne(job);
      } catch (err) {
        /* keep Croatian title */
      }
      render();
      await new Promise((resolve) => setTimeout(resolve, 160));
    }
  } finally {
    state.translating = false;
  }
}

function bind() {
  const filterOpen = $("filter-open");
  const filterClose = $("filter-close");
  const backdrop = $("backdrop");
  const search = $("search");
  const sort = $("sort");
  const filterForm = $("filter-form");
  const filterReset = $("filter-reset");
  const langEn = $("lang-en");
  const langHr = $("lang-hr");
  const themeToggle = $("theme-toggle");
  if (filterOpen) filterOpen.addEventListener("click", openDrawer);
  if (filterClose) filterClose.addEventListener("click", closeDrawer);
  if (backdrop) backdrop.addEventListener("click", closeDrawer);
  if (search) search.addEventListener("input", render);
  if (sort) sort.addEventListener("change", render);
  if (filterForm) filterForm.addEventListener("change", render);
  if (filterReset) {
    filterReset.addEventListener("click", () => {
      filterForm.reset();
      if (search) search.value = "";
      renderEmployers(state.jobs);
      render();
    });
  }
  if (langEn) langEn.addEventListener("click", () => setLang("en"));
  if (langHr) langHr.addEventListener("click", () => setLang("hr"));
  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      setTheme(currentTheme() === "dark" ? "light" : "dark");
    });
  }
}

try {
  const savedLang = localStorage.getItem(LANG_KEY);
  if (savedLang === "hr" || savedLang === "en") state.lang = savedLang;
} catch (err) {
  /* ignore */
}

bind();
applyI18n();

if (IS_BOARD) {
  fetch("./jobs.json", { cache: "no-store" })
    .then((res) => {
      if (!res.ok) throw new Error("Could not load jobs.json (" + res.status + ")");
      return res.json();
    })
    .then((payload) => {
      state.jobs = payload.jobs || [];
      state.generatedAt = payload.generated_at;
      const generated = $("generated");
      if (generated) generated.textContent = payload.generated_at || "not yet collected";
      applyCachedTitles(state.jobs);
      renderEmployers(state.jobs);
      render();
      if (state.lang === "en") translateVisibleTitles();
    })
    .catch((err) => {
      const box = $("error");
      if (!box) return;
      box.hidden = false;
      box.textContent = err.message || String(err);
    });
}
