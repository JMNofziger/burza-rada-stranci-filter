const state = {
  jobs: [],
  generatedAt: null,
};

function $(id) {
  return document.getElementById(id);
}

function openDrawer() {
  $("filters").classList.add("open");
  $("backdrop").hidden = false;
  $("filter-open").setAttribute("aria-expanded", "true");
}

function closeDrawer() {
  $("filters").classList.remove("open");
  $("backdrop").hidden = true;
  $("filter-open").setAttribute("aria-expanded", "false");
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
  if (on.length === boxes.length) return null;
  return new Set(on);
}

function applyFilters(jobs) {
  const q = $("search").value.trim().toLowerCase();
  const urgency = new Set(checkedValues("urgency"));
  const location = new Set(checkedValues("location"));
  const notified = document.querySelector("input[name=notified]:checked").value;
  const employers = selectedEmployers();
  return jobs.filter((job) => {
    if (!urgency.has(job.urgency)) return false;
    if (!location.has(job.location_label)) return false;
    if (notified === "yes" && !job.notified) return false;
    if (notified === "no" && job.notified) return false;
    if (employers && !employers.has(job.employer || "(unknown)")) return false;
    if (!q) return true;
    const blob = [
      job.title,
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
  const mode = $("sort").value;
  const copy = [...jobs];
  copy.sort((a, b) => {
    if (mode === "title") return (a.title || "").localeCompare(b.title || "");
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

function renderEmployers(jobs) {
  const box = $("employer-filters");
  const names = [...new Set(jobs.map((j) => j.employer || "(unknown)"))].sort();
  box.innerHTML = names
    .map(
      (name) =>
        `<label><input type="checkbox" value="${escapeHtml(name)}" checked> ${escapeHtml(name)}</label>`
    )
    .join("");
}

function render() {
  const filtered = sortJobs(applyFilters(state.jobs));
  const total = state.jobs.length;
  $("row-count").textContent =
    filtered.length === total
      ? `${total} listing${total === 1 ? "" : "s"}`
      : `${filtered.length} of ${total} listings`;
  $("empty").hidden = filtered.length > 0 || total === 0;
  const list = $("results");
  list.innerHTML = filtered
    .map((job) => {
      const expiry = job.deadline_date || "open-ended";
      const days =
        job.days_until_deadline === null || job.days_until_deadline === undefined
          ? ""
          : ` · ${job.days_until_deadline}d`;
      return `<li>
        <a class="card" href="${escapeHtml(job.detail_url)}" target="_blank" rel="noopener">
          <h3>${escapeHtml(job.title)}</h3>
          <p>${escapeHtml(job.employer)} · ${escapeHtml(job.location_label)}</p>
          <p>Expires ${escapeHtml(expiry)}${escapeHtml(days)}</p>
          <div class="badges">
            <span class="badge u-${escapeHtml(job.urgency)}">${escapeHtml(job.urgency)}</span>
            <span class="badge">score ${escapeHtml(job.foreign_score)}</span>
            ${job.matched_keywords ? `<span class="badge">${escapeHtml(job.matched_keywords)}</span>` : ""}
          </div>
        </a>
      </li>`;
    })
    .join("");
}

function bind() {
  $("filter-open").addEventListener("click", openDrawer);
  $("filter-close").addEventListener("click", closeDrawer);
  $("backdrop").addEventListener("click", closeDrawer);
  $("search").addEventListener("input", render);
  $("sort").addEventListener("change", render);
  $("filter-form").addEventListener("change", render);
  $("filter-reset").addEventListener("click", () => {
    $("filter-form").reset();
    $("search").value = "";
    renderEmployers(state.jobs);
    render();
  });
}

bind();

fetch("./jobs.json", { cache: "no-store" })
  .then((res) => {
    if (!res.ok) throw new Error("Could not load jobs.json (" + res.status + ")");
    return res.json();
  })
  .then((payload) => {
    state.jobs = payload.jobs || [];
    state.generatedAt = payload.generated_at;
    $("generated").textContent = payload.generated_at || "not yet collected";
    renderEmployers(state.jobs);
    render();
    if (!state.jobs.length) {
      $("empty").hidden = false;
      $("empty").textContent = "No matching jobs yet. The board updates after each successful daily collect.";
    }
  })
  .catch((err) => {
    const box = $("error");
    box.hidden = false;
    box.textContent = err.message || String(err);
  });
