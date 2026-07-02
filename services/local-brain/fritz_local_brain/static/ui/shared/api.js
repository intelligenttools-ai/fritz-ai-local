// Fritz Local Brain — shared client toolbox (#220).
//
// Extracted VERBATIM from the old single-file dashboard.html so behaviour is
// preserved exactly: token auth (sessionStorage bearer + the POST token flow),
// the apiFetch/postAction wrappers, theme toggle, rendering helpers, toast,
// auto-refresh (#195) and the auth overlay. Every page loads this module, then
// defines its own window.loadAll(); the shared init() below calls it.
//
// AUTH STRATEGY (unchanged): each page is an unauthenticated shell. Every fetch
// to /v1/* requires a Bearer token, stored in sessionStorage so it survives page
// refreshes within the tab but is discarded when the tab closes. On a 401 the
// stored token is cleared and the auth overlay re-appears.

const TOKEN_KEY = "fritz_dashboard_token";

// SECURITY: every untrusted value (agent ids, vault names, event types, query
// text, status labels — all agent-supplied and telemetry-stored) MUST be passed
// through esc() before it enters an innerHTML string, in BOTH text and attribute
// (title="...") contexts. Numbers are safe.
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

function getToken() { return sessionStorage.getItem(TOKEN_KEY); }
function setToken(t) { sessionStorage.setItem(TOKEN_KEY, t); }
function clearToken() { sessionStorage.removeItem(TOKEN_KEY); }

function showAuthOverlay(showError) {
  document.getElementById("auth-overlay").style.display = "flex";
  document.getElementById("auth-error").style.display = showError ? "block" : "none";
  document.getElementById("token-input").value = "";
  document.getElementById("token-input").focus();
}

function hideAuthOverlay() {
  document.getElementById("auth-overlay").style.display = "none";
}

function saveToken() {
  const t = document.getElementById("token-input").value.trim();
  if (!t) return;
  setToken(t);
  // Dismiss the overlay optimistically; a wrong token re-shows it via the 401
  // path in apiFetch (clearToken + showAuthOverlay(true)).
  hideAuthOverlay();
  loadAll();
  if (window.usesSSE !== false) setupSSE();  // #198: live updates (best-effort; polling is the backstop)
}

// ---- theme toggle (#197) ----------------------------------------------------

const THEME_KEY = "fritz_dashboard_theme";

function applyTheme(theme) {
  if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = "☀";
  } else {
    document.documentElement.removeAttribute("data-theme");
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.textContent = "🌙";
  }
}

function toggleTheme() {
  const cur = localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}

function restoreTheme() {
  applyTheme(localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark");
}

// ---- fetch wrapper ----------------------------------------------------------

let _loading = 0;
function showLoader(on) {
  _loading += on ? 1 : -1;
  const el = document.getElementById("loading");
  if (el) el.style.display = _loading > 0 ? "block" : "none";
}

async function apiFetch(path, params) {
  const token = getToken();
  const url = new URL(path, window.location.href);
  if (params) Object.entries(params).forEach(([k, v]) => { if (v != null) url.searchParams.set(k, v); });

  showLoader(true);
  try {
    const resp = await fetch(url.toString(), {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (resp.status === 401) {
      clearToken();
      showAuthOverlay(true);
      return null;
    }
    if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
    return await resp.json();
  } catch (e) {
    console.error("Fetch error:", path, e);
    return null;
  } finally {
    showLoader(false);
  }
}

/**
 * POST helper — mirrors apiFetch but for mutating operations.
 * Returns { status, json } on success; null if 401 (auth overlay shown).
 * Throws on network error.
 */
async function postAction(path, body, method = "POST") {
  const token = getToken();
  showLoader(true);
  try {
    const resp = await fetch(path, {
      method: method,
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });
    if (resp.status === 401) {
      clearToken();
      showAuthOverlay(true);
      return null;
    }
    const json = await resp.json().catch(() => null);
    return { status: resp.status, json };
  } catch (e) {
    console.error("Action error:", path, e);
    throw e;
  } finally {
    showLoader(false);
  }
}

// ---- rendering helpers ------------------------------------------------------

const PALETTE = [
  "#6c8bef","#a78bfa","#4ade80","#fbbf24","#f87171",
  "#38bdf8","#fb923c","#e879f9","#34d399","#f472b6"
];

function pctBadge(rate) {
  if (rate == null) return '<span class="pct-badge" style="color:var(--muted)">—</span>';
  const p = Math.round(rate * 100);
  const cls = p >= 80 ? "pct-green" : p >= 50 ? "pct-yellow" : "pct-red";
  return `<span class="pct-badge ${cls}">${p}%</span>`;
}

function fmtMs(v) { return v == null ? "—" : `${v} ms`; }
function fmtBytes(b) {
  if (b == null) return "—";
  if (b < 1024) return `${b} B`;
  if (b < 1024*1024) return `${(b/1024).toFixed(1)} KB`;
  return `${(b/(1024*1024)).toFixed(1)} MB`;
}

/** Render an error card into a container (endpoint returned null / 500). */
function renderError(containerId, msg) {
  const el = document.getElementById(containerId);
  if (el) el.innerHTML = `<div class="error-state">⚠ ${esc(msg || "Could not load data.")}</div>`;
}

/** cards = [{label, value, cls?, spark?:[numbers]}] */
function renderCards(containerId, cards) {
  document.getElementById(containerId).innerHTML = cards.map(c => `
    <div class="card">
      <div class="label">${esc(c.label)}</div>
      <div class="value ${c.cls||''}">${c.value}</div>
      ${c.spark && c.spark.length ? `<div class="spark">${sparkline(c.spark, c.sparkColor)}</div>` : ""}
    </div>`).join("");
}

/** Tiny inline-SVG sparkline (area + line) for a numeric series. Numbers only. */
function sparkline(values, color) {
  const vals = values.filter(v => typeof v === "number" && isFinite(v));
  if (vals.length < 2) return "";
  const W = 120, H = 26, P = 2;
  const max = Math.max(...vals), min = Math.min(...vals);
  const span = (max - min) || 1;
  const stepX = (W - P * 2) / (vals.length - 1);
  const pts = vals.map((v, i) => {
    const x = P + i * stepX;
    const y = H - P - ((v - min) / span) * (H - P * 2);
    return [x, y];
  });
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `M${pts[0][0].toFixed(1)},${H} ` +
    pts.map(p => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") +
    ` L${pts[pts.length-1][0].toFixed(1)},${H} Z`;
  const c = color || "var(--accent)";
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="chart-svg" aria-hidden="true">
    <path d="${area}" fill="${c}" class="area-fill"/>
    <path d="${line}" stroke="${c}" class="data-line"/>
  </svg>`;
}

/** Horizontal bar chart. items = [{label, count}] */
function renderBarChart(containerId, items, color) {
  const el = document.getElementById(containerId);
  if (!items || !items.length) { el.innerHTML = '<div class="no-data">No data yet.</div>'; return; }
  const max = Math.max(...items.map(i => i.count), 1);
  el.innerHTML = '<div class="bar-chart">' + items.map((item, idx) => {
    const w = Math.round((item.count / max) * 100);
    const c = color || PALETTE[idx % PALETTE.length];
    return `<div class="bar-row">
      <div class="bar-label" title="${esc(item.label)}">${esc(item.label)}</div>
      <div class="bar-track"><div class="bar-fill" style="width:${w}%;background:${c}"></div></div>
      <div class="bar-count">${item.count}</div>
    </div>`;
  }).join("") + '</div>';
}

// ---- SVG line/area time-series chart ----------------------------------------

let _tipBound = false;
function bindChartTip() {
  if (_tipBound) return;
  _tipBound = true;
  const tip = document.getElementById("chart-tip");
  if (!tip) return;
  document.addEventListener("mouseover", (e) => {
    const dot = e.target.closest && e.target.closest(".data-dot");
    if (!dot) return;
    tip.textContent = dot.getAttribute("data-tip") || "";
    const r = dot.getBoundingClientRect();
    tip.style.left = (window.scrollX + r.left + r.width / 2) + "px";
    tip.style.top  = (window.scrollY + r.top) + "px";
    tip.style.display = "block";
  });
  document.addEventListener("mouseout", (e) => {
    if (e.target.closest && e.target.closest(".data-dot")) tip.style.display = "none";
  });
}

/** Time-series stacked-area chart. buckets = {"YYYY-MM-DD": {key:count}}
 *
 * Renders one stacked area per distinct key so toggling by type/agent/vault
 * produces visibly different charts. Top MAX_SERIES keys by total are shown;
 * the remainder are folded into an "other" series. A colour legend is rendered
 * below the chart. All key strings are esc()'d before entering innerHTML/SVG.
 */
function renderTimeChart(containerId, buckets) {
  const el = document.getElementById(containerId);
  if (!buckets || !Object.keys(buckets).length) {
    el.innerHTML = '<div class="no-data">No activity recorded yet.</div>'; return;
  }
  bindChartTip();

  const MAX_SERIES = 8;
  const days = Object.keys(buckets).sort();

  // Collect totals per key across all days.
  const keyTotals = {};
  for (const day of days) {
    for (const [k, v] of Object.entries(buckets[day])) {
      keyTotals[k] = (keyTotals[k] || 0) + v;
    }
  }

  // Sort keys: desc by total, then by name for determinism. Cap at MAX_SERIES.
  const sortedKeys = Object.keys(keyTotals).sort((a, b) =>
    keyTotals[b] - keyTotals[a] || a.localeCompare(b)
  );
  const topKeys = sortedKeys.slice(0, MAX_SERIES);
  const hasOther = sortedKeys.length > MAX_SERIES;
  const seriesKeys = hasOther ? [...topKeys, "other"] : topKeys;

  const topSet = new Set(topKeys);
  const seriesData = seriesKeys.map(sk =>
    days.map(day => {
      if (sk === "other") {
        return Object.entries(buckets[day])
          .filter(([k]) => !topSet.has(k))
          .reduce((s, [, v]) => s + v, 0);
      }
      return (buckets[day][sk] || 0);
    })
  );

  const stackedTotals = days.map((_, di) =>
    seriesData.reduce((s, sd) => s + sd[di], 0)
  );
  const maxTotal = Math.max(...stackedTotals, 1);

  const W = 600, H = 160, PL = 34, PR = 8, PT = 10, PB = 22;
  const plotW = W - PL - PR, plotH = H - PT - PB;
  const n = days.length;
  const xAt = i => n === 1 ? PL + plotW / 2 : PL + (i / (n - 1)) * plotW;
  const yAt = v => PT + plotH - (v / maxTotal) * plotH;

  const ticks = [0, Math.round(maxTotal / 2), maxTotal];
  const grid = ticks.map(t => {
    const y = yAt(t).toFixed(1);
    return `<line class="grid-line" x1="${PL}" y1="${y}" x2="${W - PR}" y2="${y}"/>
            <text class="axis-label" x="${PL - 5}" y="${(yAt(t) + 3).toFixed(1)}" text-anchor="end">${t}</text>`;
  }).join("");

  const cumBottom = days.map(() => 0);
  const seriesBottomTop = [];

  let areasSVG = "";
  for (let si = 0; si < seriesData.length; si++) {
    const sd   = seriesData[si];
    const col  = PALETTE[si % PALETTE.length];
    const bottoms = days.map((_, di) => yAt(cumBottom[di]));
    const tops    = days.map((_, di) => yAt(cumBottom[di] + sd[di]));
    seriesBottomTop.push({ bottoms, tops });

    const topPts    = tops.map((y, di) => [xAt(di), y]);
    const bottomPts = bottoms.map((y, di) => [xAt(di), y]);
    const areaPath =
      `M${topPts[0][0].toFixed(1)},${topPts[0][1].toFixed(1)} ` +
      topPts.slice(1).map(p => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") +
      " " +
      [...bottomPts].reverse().map((p, i) => `${i === 0 ? "L" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") +
      " Z";
    const linePath = topPts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");

    areasSVG += `<path d="${areaPath}" fill="${col}" class="area-fill"/>`;
    areasSVG += `<path d="${linePath}" stroke="${col}" class="data-line"/>`;

    for (let di = 0; di < days.length; di++) cumBottom[di] += sd[di];
  }

  const dots = days.map((day, di) => {
    const breakdown = seriesKeys.map((k, si) => {
      const v = seriesData[si][di];
      return v > 0 ? `${esc(k)}: ${v}` : "";
    }).filter(Boolean).join(", ");
    const tip = `${esc(day)} — ${stackedTotals[di]}${breakdown ? " (" + breakdown + ")" : ""}`;
    const cx = xAt(di).toFixed(1);
    const cy = yAt(stackedTotals[di]).toFixed(1);
    return `<circle class="data-dot" cx="${cx}" cy="${cy}" r="3"
      fill="var(--accent)" data-tip="${tip}"/>`;
  }).join("");

  const stride = Math.max(1, Math.ceil(n / 8));
  const xLabels = days.map((d, i) => (i % stride === 0 || i === n - 1)
    ? `<text class="axis-label" x="${xAt(i).toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(d.slice(5))}</text>`
    : "").join("");

  const legendItems = seriesKeys.map((sk, si) => {
    const col = PALETTE[si % PALETTE.length];
    const label = sk === "other" ? "other" : esc(sk);
    return `<span class="tc-legend-item">
      <span class="tc-legend-swatch" style="background:${col}"></span>${label}</span>`;
  }).join("");

  el.innerHTML =
    `<div class="chart-wrap"><svg viewBox="0 0 ${W} ${H}" class="chart-svg" preserveAspectRatio="xMidYMid meet" role="img">
      ${grid}
      <line class="axis-line" x1="${PL}" y1="${PT + plotH}" x2="${W - PR}" y2="${PT + plotH}"/>
      ${areasSVG}
      ${dots}
      ${xLabels}
    </svg></div>
    <div class="tc-legend">${legendItems}</div>`;
}

// ---- toast ------------------------------------------------------------------

let _toastTimer = null;

function showToast(msg, isError) {
  const el  = document.getElementById("action-toast");
  const txt = document.getElementById("action-toast-msg");
  txt.innerHTML = msg; // msg is already esc()-escaped by callers
  el.className = isError ? "error" : "success";
  el.style.display = "block";
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(dismissToast, isError ? 8000 : 5000);
}

function dismissToast() {
  const el = document.getElementById("action-toast");
  if (el) el.style.display = "none";
  if (_toastTimer) { clearTimeout(_toastTimer); _toastTimer = null; }
}

// ---- auto-refresh (#195) ----------------------------------------------------

const REFRESH_INTERVAL_KEY = "fritz_dashboard_refresh_interval";
const DEFAULT_INTERVAL_S   = 30;

let _lastUpdatedAt  = null;  // Date or null
let _refreshTimer   = null;  // setInterval handle for auto-refresh ticks
let _tickTimer      = null;  // setInterval handle for the 1s countdown ticker
let _inFlight       = false; // guard against overlapping loadAll calls

/** Read the persisted interval (seconds); returns 0 for "off". */
function getSavedInterval() {
  const raw = localStorage.getItem(REFRESH_INTERVAL_KEY);
  const n   = parseInt(raw, 10);
  const valid = [0, 15, 30, 60];
  return valid.includes(n) ? n : DEFAULT_INTERVAL_S;
}

/** Restore the select value from localStorage, selecting the matching option. */
function restoreIntervalSelect() {
  const saved = getSavedInterval();
  const sel   = document.getElementById("auto-refresh-select");
  if (!sel) return;
  const opt = [...sel.options].find(o => parseInt(o.value, 10) === saved);
  if (opt) sel.value = String(saved);
}

/** Update #last-updated text once. Called by the 1s ticker. */
function renderLastUpdated() {
  const el  = document.getElementById("last-updated");
  const sel = document.getElementById("auto-refresh-select");
  if (!el || !sel) return;
  const intervalS = parseInt(sel.value, 10);

  if (!_lastUpdatedAt) {
    el.textContent = "";
    return;
  }

  const timeStr = _lastUpdatedAt.toLocaleTimeString();
  if (intervalS === 0) {
    el.textContent = "Updated " + timeStr;
    return;
  }

  const elapsedS  = Math.round((Date.now() - _lastUpdatedAt.getTime()) / 1000);
  const nextInS   = Math.max(0, intervalS - elapsedS);
  el.textContent  = `Updated ${timeStr} · next in ${nextInS}s`;
}

/** Start the 1s display ticker (idempotent). */
function startTicker() {
  if (_tickTimer) return;
  _tickTimer = setInterval(renderLastUpdated, 1000);
}

/** Stop and clear the auto-refresh timer (does not touch the ticker). */
function clearRefreshTimer() {
  if (_refreshTimer) {
    clearInterval(_refreshTimer);
    _refreshTimer = null;
  }
}

/** Start (or restart) the auto-refresh timer for the current interval. */
function startRefreshTimer() {
  clearRefreshTimer();
  const sel = document.getElementById("auto-refresh-select");
  if (!sel) return;
  const intervalS = parseInt(sel.value, 10);
  if (intervalS === 0) return; // off
  _refreshTimer = setInterval(() => {
    if (_inFlight) return; // skip tick if a load is already running
    if (document.hidden) return;
    loadAll();
  }, intervalS * 1000);
}

/** Called when the user changes the interval select. */
function onIntervalChange() {
  const sel = document.getElementById("auto-refresh-select");
  localStorage.setItem(REFRESH_INTERVAL_KEY, sel.value);
  startRefreshTimer();
  renderLastUpdated();
}

/** Manual refresh button — calls loadAll and resets the countdown. */
function manualRefresh() {
  if (_inFlight) return;
  loadAll();
  startRefreshTimer();
}

// Pause when hidden; resume immediately + restart timer when visible again
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearRefreshTimer();
  } else {
    if (!_inFlight) loadAll();
    startRefreshTimer();
  }
});

// ---- shared startup ---------------------------------------------------------
//
// Each page defines its own window.loadAll(); this shared init wires the token
// prompt, theme, auto-refresh timers and (if the page opts in) SSE. Pages that
// have no live-updating data can set window.usesSSE = false to skip setupSSE.

// Allow pressing Enter in the token input (added once the DOM is ready).
function bindTokenInput() {
  const input = document.getElementById("token-input");
  if (input) {
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") saveToken(); });
  }
}

function initShell() {
  bindTokenInput();
  restoreTheme();
  restoreIntervalSelect();
  startTicker();
  startRefreshTimer();
  if (getToken()) {
    hideAuthOverlay();
    loadAll();
    if (window.usesSSE !== false) setupSSE();
  } else {
    showAuthOverlay(false);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initShell);
} else {
  initShell();
}
