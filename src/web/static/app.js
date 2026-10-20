// Shared JS helpers for RuuviHub's web UI.
// No build step / bundler / framework — plain browser JS, loaded via
// <script src="/static/app.js"> on pages that need it (dashboard.js and
// sensor.js, added by later todos, both depend on the helpers below).

const settingsMenu = document.querySelector('.settings-menu');
if (settingsMenu) {
  document.addEventListener('click', event => {
    if (!settingsMenu.contains(event.target)) settingsMenu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && settingsMenu.open) {
      settingsMenu.open = false;
      settingsMenu.querySelector('summary').focus();
    }
  });
}

/**
 * Fetch JSON from an API endpoint, throwing with a readable message on
 * a non-2xx response instead of silently returning bad data.
 */
async function fetchJSON(url) {
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`${url} → HTTP ${response.status}`);
  }
  return response.json();
}

/**
 * Re-run `fn` every `intervalSeconds`, immediately and then on a timer.
 * Returns a cancel function. Errors from `fn` are logged but never stop
 * the polling loop (a single failed fetch shouldn't end live updates).
 */
function startPolling(fn, intervalSeconds) {
  let cancelled = false;
  const tick = async () => {
    if (cancelled) return;
    try {
      await fn();
    } catch (err) {
      console.error("RuuviHub poll failed:", err);
    }
  };
  tick();
  const handle = setInterval(tick, Math.max(1, intervalSeconds) * 1000);
  return () => {
    cancelled = true;
    clearInterval(handle);
  };
}

/** Render in browser local time using the configured 24-hour format. */
function formatTimestamp(iso) {
  if (iso === null || iso === undefined || iso === "") return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const pad = value => String(value).padStart(2, "0");
  const year = String(date.getFullYear()).padStart(4, "0");
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return window.RUUVIHUB_DATETIME_FORMAT === "iso"
    ? `${year}-${month}-${day} ${time}`
    : `${day}.${month}.${year} ${time}`;
}

/** Human "how long ago" for a last-seen timestamp, e.g. "12s ago". */
function timeAgo(iso) {
  if (!iso) return "never";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
