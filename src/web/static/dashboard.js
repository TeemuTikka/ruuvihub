// Dashboard live-updating card grid. Keep existing card nodes across polls.
// DASHBOARD_REFRESH_SECONDS is injected by dashboard.html; app.js helpers
// are loaded before this file.

function formatMetric(value, unit, digits) {
  if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits)} ${unit}`;
}

function formatTemperatureRange(low, high) {
  if (low === null || low === undefined || high === null || high === undefined) return "—";
  return `${Number(low).toFixed(1)} / ${Number(high).toFixed(1)} °C`;
}

const dashboardNodes = new Map();
const dashboardMetricKeys = ["temperature", "humidity", "pressure", "battery"];

function rememberCard(node) {
  const stale = node.querySelector(".stale-flag") || document.createElement("span");
  if (!stale.parentNode) {
    stale.className = "stale-flag";
    stale.textContent = "⚠ stale";
    stale.hidden = true;
    node.appendChild(stale);
  }
  return {
    node,
    name: node.querySelector(".card-name"),
    meta: node.querySelector(".card-meta"),
    metrics: Array.from(node.querySelectorAll("dd")),
    labels: Array.from(node.querySelectorAll(".card-metrics dt")),
    stale,
    link: node.querySelector(".card-main"),
    range: node.querySelector(".card-range"),
    window: node.querySelector(".card-window"),
    extremes: node.querySelector(".card-extremes"),
    trend: node.querySelector(".card-trend"),
    svg: node.querySelector("svg"),
    path: node.querySelector("svg path"),
    trendValues: Array.from(node.querySelectorAll(".trend-values text")),
    empty: node.querySelector(".trend-empty"),
    since: node.querySelector(".trend-since"),
  };
}

function setText(node, value) {
  if (node.textContent !== value) node.textContent = value;
}

function setHidden(node, hidden) {
  if (node.hasAttribute("hidden") !== hidden) node.toggleAttribute("hidden", hidden);
}

// Mirrored in _format_bytes (web/dashboard.py) for the server-rendered
// page -- keep both in sync if this formatting ever changes.
function formatBytes(value) {
  if (value === null || value === undefined) return "Unavailable";
  let amount = Number(value);
  for (const unit of ["B", "KB", "MB", "GB", "TB"]) {
    if (amount < 1024 || unit === "TB") {
      return unit === "B" ? `${Math.trunc(amount)} B` : `${amount.toFixed(1)} ${unit}`;
    }
    amount /= 1024;
  }
  return `${amount.toFixed(1)} TB`;
}

function renderStorageStats(stats) {
  let section = document.getElementById("storage-overview");
  if (!stats) {
    if (section) section.remove();
    return;
  }
  if (!section) {
    const footer = document.querySelector("footer.app-footer");
    if (!footer) return;
    section = document.createElement("div");
    section.id = "storage-overview";
    section.className = "storage-footer";
    section.setAttribute("role", "group");
    section.setAttribute("aria-label", "Stored data");
    section.innerHTML = '<span>Oldest reading: <time id="storage-oldest"></time></span>' +
      '<span>Newest reading: <time id="storage-newest"></time></span>' +
      '<span>Readings: <span id="storage-count"></span></span>' +
      '<span>Database: <span id="storage-size"></span></span>' +
      '<span>Disk free: <span id="storage-free"></span></span>';
    footer.appendChild(section);
  }
  for (const [id, timestamp] of [
    ["storage-oldest", stats.oldest_timestamp], ["storage-newest", stats.newest_timestamp],
  ]) {
    const node = document.getElementById(id);
    setText(node, formatTimestamp(timestamp));
    node.dateTime = timestamp || "";
  }
  setText(document.getElementById("storage-count"), Number(stats.reading_count).toLocaleString());
  setText(document.getElementById("storage-size"), formatBytes(stats.database_size_bytes));
  setText(document.getElementById("storage-free"), formatBytes(stats.disk_free_bytes));
}

function renderCards(cards, options = window.DASHBOARD_OPTIONS || {}) {
  window.DASHBOARD_OPTIONS = options;
  const visibleMetrics = new Set(options.visible_metrics || [...dashboardMetricKeys, "temperature_range", "temperature_trend"]);
  const historyHours = options.history_hours || 24;
  const grid = document.getElementById("card-grid");
  if (!grid) return;

  if (dashboardNodes.size === 0) {
    for (const node of grid.querySelectorAll("[data-mac]")) {
      dashboardNodes.set(node.dataset.mac, rememberCard(node));
    }
  }

  const active = new Set(cards.map((card) => card.mac));
  for (const [mac, entry] of dashboardNodes) {
    if (!active.has(mac)) {
      entry.node.remove();
      dashboardNodes.delete(mac);
    }
  }
  if (cards.length === 0) {
    if (!grid.querySelector(":scope > p")) {
      const empty = document.createElement("p");
      empty.textContent = "No tags seen yet. Once the scanner picks up a RuuviTag, it will show up here.";
      grid.appendChild(empty);
    }
    return;
  }
  const empty = grid.querySelector(":scope > p");
  if (empty) empty.remove();

  let position = grid.firstElementChild;
  for (const card of cards) {
    let entry = dashboardNodes.get(card.mac);
    if (!entry) {
      const node = document.createElement("article");
      node.dataset.mac = card.mac;
      node.innerHTML = '<a class="card-main"><div class="card-name"></div><div class="card-meta"></div>' +
        '<dl class="card-metrics"><dt>Now</dt><dd></dd><dt>Humidity</dt><dd></dd>' +
        '<dt>Pressure</dt><dd></dd><dt>Battery</dt><dd></dd></dl>' +
        '<div class="card-extremes"><span class="card-window"></span><strong class="card-range"></strong></div>' +
        '<div class="card-trend"><svg viewBox="-64 -8 304 80" role="img"><g class="trend-grid" aria-hidden="true"><line x1="2" x2="238" y1="2" y2="2" /><line x1="2" x2="238" y1="32" y2="32" /><line x1="2" x2="238" y1="62" y2="62" /></g><g class="trend-values"><text x="-6" y="2"></text><text x="-6" y="32"></text><text x="-6" y="62"></text></g><path /></svg>' +
        '<p class="trend-empty">Temperature trend unavailable in this window</p>' +
        '<div class="trend-axis"><span>Now</span><span class="trend-since"></span></div></div>' +
        '<span class="card-history-link">View history →</span></a>';
      entry = rememberCard(node);
      dashboardNodes.set(card.mac, entry);
    }
    const className = card.stale ? "card stale" : "card";
    if (entry.node.className !== className) entry.node.className = className;
    setText(entry.name, card.name);
    setText(entry.meta, `Last seen: ${card.timestamp ? formatTimestamp(card.timestamp) : "never"}`);
    const values = [
      formatMetric(card.temperature, "°C", 1),
      formatMetric(card.humidity, "%", 1),
      formatMetric(card.pressure, "hPa", 1),
      formatMetric(card.battery, "mV", 0),
    ];
    values.forEach((value, index) => {
      setText(entry.metrics[index], value);
      const hidden = !visibleMetrics.has(dashboardMetricKeys[index]);
      setHidden(entry.metrics[index], hidden);
      setHidden(entry.labels[index], hidden);
    });
    const href = `/sensor/${encodeURIComponent(card.mac)}?range=${historyHours}h`;
    if (entry.link.getAttribute("href") !== href) entry.link.setAttribute("href", href);
    setText(entry.window, `Last ${historyHours}h · Low / high`);
    setText(entry.range, formatTemperatureRange(card.temperature_low, card.temperature_high));
    setHidden(entry.extremes, !visibleMetrics.has("temperature_range"));
    setHidden(entry.trend, !visibleMetrics.has("temperature_trend"));
    const low = card.temperature_low, high = card.temperature_high;
    const midpoint = low == null || high == null ? null : (low + high) / 2;
    [high, midpoint, low].forEach((value, index) => {
      setText(entry.trendValues[index], formatMetric(value, "°C", 1));
    });
    const path = card.trend_path || "";
    if (entry.path.getAttribute("d") !== path) entry.path.setAttribute("d", path);
    const label = `Temperature over the last ${historyHours} hours; now on the left`;
    if (entry.svg.getAttribute("aria-label") !== label) entry.svg.setAttribute("aria-label", label);
    setHidden(entry.svg, !path);
    setHidden(entry.empty, !!path);
    setText(entry.since, `${historyHours}h ago`);
    if (entry.stale.hidden !== !card.stale) entry.stale.hidden = !card.stale;
    if (entry.node !== position) grid.insertBefore(entry.node, position);
    position = entry.node.nextElementSibling;
  }
}

let dashboardRefreshSeconds = DASHBOARD_REFRESH_SECONDS || 300;
let stopDashboardPolling;
async function refreshDashboard() {
  const data = await fetchJSON(`/api/current?hours=${window.DASHBOARD_OPTIONS?.history_hours || 24}`);
  renderCards(data.cards, data.dashboard_options);
  renderStorageStats(data.storage);
  const refreshSeconds = data.dashboard_options?.refresh_seconds;
  if (refreshSeconds && refreshSeconds !== dashboardRefreshSeconds) {
    stopDashboardPolling();
    dashboardRefreshSeconds = refreshSeconds;
    stopDashboardPolling = startPolling(refreshDashboard, dashboardRefreshSeconds);
  }
}
stopDashboardPolling = startPolling(refreshDashboard, dashboardRefreshSeconds);
