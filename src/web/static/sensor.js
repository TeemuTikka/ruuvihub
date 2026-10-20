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
    {
      field: "battery",
      label: "Battery (mV)",
      canvasId: "chart-battery",
      color: "#ba68c8",
    },
  ];

  function formatLabel(iso) {
    return formatTimestamp(iso);
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

    new Chart(canvas.getContext("2d"), {
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
        maintainAspectRatio: true,
        scales: {
          x: {
            type: "linear",
            min: Date.parse(history.since),
            max: Date.parse(history.until),
            ticks: {
              autoSkip: true,
              maxTicksLimit: 8,
              maxRotation: 0,
              callback(value) {
                return formatLabel(value);
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
    });
  }

  async function loadHistory() {
    try {
      const history = await fetchJSON(
        `/api/history/${encodeURIComponent(mac)}?range=${encodeURIComponent(range)}&chart=true`
      );
      const records = history.records;
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
    }
  }

  loadHistory();
})();
