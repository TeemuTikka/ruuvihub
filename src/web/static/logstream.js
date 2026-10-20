// Live log stream panel: connects to /ws/logs and renders records into
// the #log-lines drawer added to base.html. No build step, plain IIFE
// like app.js/dashboard.js — guards on missing elements so this is a
// harmless no-op on pages without the panel (e.g. login.html, which
// doesn't include this script at all, but the guard is cheap insurance).

(function () {
  const STORAGE_OPEN_KEY = "ruuvihub.logPanel.open";
  const STORAGE_LEVEL_KEY = "ruuvihub.logPanel.level";
  const STORAGE_WIDTH_KEY = "ruuvihub.logPanel.width";
  const RECONNECT_MIN_MS = 1000;
  const RECONNECT_MAX_MS = 15000;
  const MAX_RENDERED_LINES = 500;
  const MIN_PANEL_WIDTH = 240;

  const panel = document.getElementById("log-panel");
  const toggle = document.getElementById("log-panel-toggle");
  const levelSelect = document.getElementById("log-level-select");
  const statusEl = document.getElementById("log-panel-status");
  const clearButton = document.getElementById("log-panel-clear");
  const linesEl = document.getElementById("log-lines");
  const resizeHandle = document.getElementById("log-panel-resize-handle");

  if (!panel || !toggle || !levelSelect || !linesEl) {
    return;
  }

  let socket = null;
  let reconnectDelayMs = RECONNECT_MIN_MS;
  let reconnectTimer = null;
  let closingIntentionally = false;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function currentLevelNo() {
    return parseInt(levelSelect.value, 10) || 0;
  }

  function applyFilter() {
    const minLevel = currentLevelNo();
    for (const line of linesEl.children) {
      const levelno = parseInt(line.dataset.levelno || "0", 10);
      line.classList.toggle("filtered", levelno < minLevel);
    }
  }

  function appendEntry(entry) {
    const line = document.createElement("div");
    line.className = `log-line level-${(entry.level || "").toLowerCase()}`;
    line.dataset.levelno = String(entry.levelno || 0);
    const ts = entry.ts ? formatTimestamp(entry.ts) : "";
    line.textContent = `${ts} [${entry.level}] ${entry.logger}: ${entry.message}`;
    const minLevel = currentLevelNo();
    if ((entry.levelno || 0) < minLevel) {
      line.classList.add("filtered");
    }
    linesEl.appendChild(line);
    // Keep the drawer bounded during long sessions by dropping the oldest lines.
    while (linesEl.children.length > MAX_RENDERED_LINES) {
      linesEl.removeChild(linesEl.firstChild);
    }
    linesEl.scrollTop = linesEl.scrollHeight;
  }

  function wsUrl() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${location.host}/ws/logs`;
  }

  function scheduleReconnect() {
    if (reconnectTimer !== null) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (toggle.checked) connect();
    }, reconnectDelayMs);
    reconnectDelayMs = Math.min(reconnectDelayMs * 2, RECONNECT_MAX_MS);
  }

  function connect() {
    if (socket) return;
    closingIntentionally = false;
    setStatus("connecting…");
    try {
      socket = new WebSocket(wsUrl());
    } catch (err) {
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      reconnectDelayMs = RECONNECT_MIN_MS;
      setStatus("connected");
    };

    socket.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (err) {
        return;
      }
      if (payload.type === "backlog") {
        linesEl.textContent = "";
        for (const entry of payload.entries || []) {
          appendEntry(entry);
        }
      } else if (payload.type === "log" && payload.entry) {
        appendEntry(payload.entry);
      }
    };

    socket.onclose = () => {
      socket = null;
      if (!closingIntentionally) {
        setStatus("disconnected – reconnecting…");
        scheduleReconnect();
      } else {
        setStatus("disconnected");
      }
    };

    socket.onerror = () => {
      // onclose fires right after -- let it handle reconnect scheduling.
    };
  }

  function disconnect() {
    closingIntentionally = true;
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    reconnectDelayMs = RECONNECT_MIN_MS;
    if (socket) {
      socket.close();
      socket = null;
    }
    setStatus("disconnected");
  }

  function maxPanelWidth() {
    // Leave a little breathing room so the drawer can never eat the
    // whole viewport (mirrors the CSS max-width: 90vw fallback).
    return Math.max(MIN_PANEL_WIDTH, Math.floor(window.innerWidth * 0.9));
  }

  function applyWidth(widthPx) {
    document.documentElement.style.setProperty("--log-panel-width", `${widthPx}px`);
  }

  function initResize() {
    if (!resizeHandle) return;
    let startX = 0;
    let startWidth = 0;
    let dragging = false;

    function onPointerMove(event) {
      if (!dragging) return;
      const dx = startX - event.clientX; // dragging left increases width
      const width = Math.min(maxPanelWidth(), Math.max(MIN_PANEL_WIDTH, startWidth + dx));
      applyWidth(width);
    }

    function onPointerUp() {
      if (!dragging) return;
      dragging = false;
      panel.classList.remove("resizing");
      document.removeEventListener("pointermove", onPointerMove);
      document.removeEventListener("pointerup", onPointerUp);
      const finalWidth = panel.getBoundingClientRect().width;
      try {
        localStorage.setItem(STORAGE_WIDTH_KEY, String(Math.round(finalWidth)));
      } catch (err) {
        // localStorage unavailable (e.g. private browsing) - just skip persisting.
      }
    }

    resizeHandle.addEventListener("pointerdown", (event) => {
      dragging = true;
      startX = event.clientX;
      startWidth = panel.getBoundingClientRect().width;
      panel.classList.add("resizing");
      document.addEventListener("pointermove", onPointerMove);
      document.addEventListener("pointerup", onPointerUp);
      event.preventDefault();
    });
  }

  initResize();

  function setOpen(open, { persist = true } = {}) {
    document.documentElement.classList.remove("log-panel-open");
    panel.classList.toggle("open", open);
    toggle.checked = open;
    if (persist) {
      localStorage.setItem(STORAGE_OPEN_KEY, open ? "1" : "0");
    }
    if (open) {
      connect();
    } else {
      disconnect();
    }
  }

  toggle.addEventListener("change", () => setOpen(toggle.checked));

  levelSelect.addEventListener("change", () => {
    localStorage.setItem(STORAGE_LEVEL_KEY, levelSelect.value);
    applyFilter();
  });

  if (clearButton) {
    clearButton.addEventListener("click", () => {
      linesEl.textContent = "";
    });
  }

  window.addEventListener("storage", (event) => {
    if (event.key === STORAGE_OPEN_KEY) {
      const open = event.newValue === "1";
      if (open !== panel.classList.contains("open")) {
        setOpen(open, { persist: false });
      }
    } else if (event.key === STORAGE_LEVEL_KEY && event.newValue) {
      levelSelect.value = event.newValue;
      applyFilter();
    }
  });

  const storedLevel = localStorage.getItem(STORAGE_LEVEL_KEY);
  if (storedLevel) {
    levelSelect.value = storedLevel;
  }
  const storedWidth = parseInt(localStorage.getItem(STORAGE_WIDTH_KEY), 10);
  if (storedWidth) {
    applyWidth(Math.min(maxPanelWidth(), Math.max(MIN_PANEL_WIDTH, storedWidth)));
  }
  const storedOpen = localStorage.getItem(STORAGE_OPEN_KEY) === "1";
  setOpen(storedOpen, { persist: false });
})();
