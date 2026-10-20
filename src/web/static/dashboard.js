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

function formatTemperatureChange(value) {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(1)} °C`;
}

const dashboardNodes = new Map();
const dashboardMetricKeys = ["temperature", "temperature_change", "day_temperature_range",
  "night_temperature_range", "humidity", "pressure", "battery"];

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
  };
}

function setText(node, value) {
  if (node.textContent !== value) node.textContent = value;
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
  const visibleMetrics = new Set(options.visible_metrics || dashboardMetricKeys);
  const changeHours = options.change_hours || 1;
  const grid = document.getElementById("card-grid");
  if (!grid) return;

  if (dashboardNodes.size === 0) {
    for (const node of grid.querySelectorAll("a[data-mac]")) {
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
    if (!grid.querySelector("p")) {
      const empty = document.createElement("p");
      empty.textContent = "No tags seen yet. Once the scanner picks up a RuuviTag, it will show up here.";
      grid.appendChild(empty);
    }
    return;
  }
  const empty = grid.querySelector("p");
  if (empty) empty.remove();

  let position = grid.firstElementChild;
  for (const card of cards) {
    let entry = dashboardNodes.get(card.mac);
    if (!entry) {
      const node = document.createElement("a");
      node.dataset.mac = card.mac;
      node.href = `/sensor/${encodeURIComponent(card.mac)}`;
      node.innerHTML = '<div class="card-name"></div><div class="card-meta"></div>' +
        '<dl class="card-metrics"><dt>Temp</dt><dd></dd>' +
        '<dt class="hour-change-label" title="Compared with the closest reading to one hour ago (±10 minutes)">Change / 1h</dt><dd class="hour-change"></dd>' +
        '<dt>Day low / high</dt><dd></dd><dt>Night low / high</dt><dd></dd>' +
        '<dt>Humidity</dt><dd></dd><dt>Pressure</dt><dd></dd><dt>Battery</dt><dd></dd></dl>';
      entry = rememberCard(node);
      dashboardNodes.set(card.mac, entry);
    }
    const className = card.stale ? "card stale" : "card";
    if (entry.node.className !== className) entry.node.className = className;
    setText(entry.name, card.name);
    setText(entry.meta, `Last seen: ${card.timestamp ? formatTimestamp(card.timestamp) : "never"}`);
    const values = [
      formatMetric(card.temperature, "°C", 1),
      formatTemperatureChange(card.temperature_change),
      formatTemperatureRange(card.day_temperature_low, card.day_temperature_high),
      formatTemperatureRange(card.night_temperature_low, card.night_temperature_high),
      formatMetric(card.humidity, "%", 1),
      formatMetric(card.pressure, "hPa", 1),
      formatMetric(card.battery, "mV", 0),
    ];
    values.forEach((value, index) => {
      setText(entry.metrics[index], value);
      const hidden = !visibleMetrics.has(dashboardMetricKeys[index]);
      entry.metrics[index].hidden = hidden;
      entry.labels[index].hidden = hidden;
    });
    setText(entry.labels[1], `Change / ${changeHours}h`);
    entry.labels[1].title = `Compared with the closest reading to ${changeHours} hours ago (±10 minutes)`;
    if (entry.stale.hidden !== !card.stale) entry.stale.hidden = !card.stale;
    if (entry.node !== position) grid.insertBefore(entry.node, position);
    position = entry.node.nextElementSibling;
  }
}

let dashboardRefreshSeconds = DASHBOARD_REFRESH_SECONDS || 300;
let stopDashboardPolling;
async function refreshDashboard() {
  const data = await fetchJSON("/api/current");
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
