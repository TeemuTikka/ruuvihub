(function () {
  const root = document.getElementById("sensor-page");
  if (!root) return;

  const mac = root.dataset.mac;
  const range = root.dataset.range;
  const statusEl = document.getElementById("history-status");

  const charts = [
    {
      field: "temperature",
      label: "Temperature (°C)",
      canvasId: "chart-temperature",
      color: "#ff8a65",
    },
    {
      field: "humidity",
      label: "Humidity (%)",
      canvasId: "chart-humidity",
      color: "#4fc3f7",
    },
    {
      field: "pressure",
      label: "Pressure (hPa)",
      canvasId: "chart-pressure",
      color: "#81c784",
    },
  ];

  const instances = new Map();
  let loading = false;

  function showLatest(records) {
    const latest = records[records.length - 1];
    for (const [field, unit] of [["temperature", "°C"], ["humidity", "%"], ["pressure", "hPa"]]) {
      const value = latest?.[field];
      document.getElementById(`weather-${field}`).textContent =
        typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)} ${unit}` : `— ${unit}`;
    }
    document.getElementById("weather-seen").textContent = latest
      ? `${formatTimestamp(latest.timestamp)} · ${timeAgo(latest.timestamp)}` : "No readings in this window";
  }

  function renderChart(config, history) {
    const points = [];
    let previous = null;
    for (const record of history.records) {
      const x = Date.parse(record.timestamp);
      if (!Number.isFinite(x)) continue;
      const segment = record._chart_segments[config.field];
      if (previous && segment !== previous.segment) {
        // Segment IDs are calculated before downsampling, so missing
        // measurements cannot disappear merely because a row was skipped.
        points.push({ x: (previous.x + x) / 2, y: null });
      }
      const value = record[config.field];
      points.push({ x, y: typeof value === "number" && Number.isFinite(value) ? value : null });
      previous = { x, segment };
    }
    const canvas = document.getElementById(config.canvasId);
    if (!canvas) return;

    const existing = instances.get(config.field);
    if (existing) {
      existing.data.datasets[0].data = points;
      existing.options.scales.x.min = Date.parse(history.since);
      existing.options.scales.x.max = Date.parse(history.until);
      existing.options.scales.x.ticks.callback = value => relativeLabel(value, history.until);
      existing.update("none");
      return;
    }
    instances.set(config.field, new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        datasets: [
          {
            label: config.label,
            data: points,
            borderColor: config.color,
            backgroundColor: config.color,
            tension: 0,
            spanGaps: false,
            pointRadius: points.length > 100 ? 1 : 2,
          },
        ],
      },
      options: {
        animation: false,
        // Numeric epoch x and numeric/null y already match linear scales.
        // Duplicate timestamps are allowed, so do not enable normalized.
        parsing: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { ticks: { color: "#9aa8b6" }, grid: { color: "#2a3746" } },
          x: {
            grid: { color: "#2a3746" },
            type: "linear",
            reverse: true,
            min: Date.parse(history.since),
            max: Date.parse(history.until),
            ticks: {
              color: "#9aa8b6",
              autoSkip: true,
              maxTicksLimit: 8,
              maxRotation: 0,
              callback(value) {
                return relativeLabel(value, history.until);
              },
            },
          },
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            callbacks: {
              title(items) {
                return items.length ? formatTimestamp(items[0].parsed.x) : "";
              },
            },
          },
        },
      },
    }));
  }

  function relativeLabel(value, until) {
    const hours = (Date.parse(until) - value) / 3600000;
    if (hours <= 0) return "Now";
    if (hours < 24) return `${Number(hours.toFixed(1))}h ago`;
    return `${Number((hours / 24).toFixed(1))}d ago`;
  }

  async function loadHistory() {
    if (loading) return;
    loading = true;
    try {
      const history = await fetchJSON(
        `/api/history/${encodeURIComponent(mac)}?range=${encodeURIComponent(range)}&chart=true`
      );
      const records = history.records;
      showLatest(records);
      if (statusEl) {
        statusEl.textContent = records.length
          ? `${records.length} record${records.length === 1 ? "" : "s"}`
          : "No history for the selected range.";
      }
      charts.forEach((chart) => renderChart(chart, history));
    } catch (err) {
      console.error("Failed to load sensor history:", err);
      if (statusEl) {
        statusEl.textContent = "Failed to load history.";
      }
    } finally {
      loading = false;
    }
  }

  startPolling(loadHistory, Number(root.dataset.refresh) || 300);
})();
