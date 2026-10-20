#!/usr/bin/env python3
"""Optional headless Chromium dashboard rendering benchmark and checks.

Install playwright and its Chromium browser separately; neither is an app
dependency. Measures renderCards plus forced layout, without HTTP or BLE.
Use --dashboard-js to compare another revision; --skip-checks supports the
old renderer that replaced all nodes. Timings are local browser results.
"""

import argparse
import json
import math
from pathlib import Path
import platform
import statistics

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def load(page, script):
    page.set_content('<div id="card-grid" class="card-grid"></div>')
    page.add_style_tag(path=str(ROOT / "src/web/static/style.css"))
    page.add_script_tag(path=str(ROOT / "src/web/static/app.js"))
    page.evaluate("window.startPolling = () => {}; window.DASHBOARD_REFRESH_SECONDS = 10; Date.now = () => Date.parse('2026-10-04T00:00:00Z')")
    page.add_script_tag(path=str(script))


def verify(page):
    page.evaluate("""() => {
      const check = (condition, message) => { if (!condition) throw Error(message); };
      const a = {mac: 'AA:BB:CC:DD:EE:FF', name: '<img src=x onerror=alert(1)>',
        timestamp: '2026-10-03T23:59:55Z', temperature: 20, humidity: null,
        pressure: 1000, battery: 2900, stale: false};
      const b = {...a, mac: '112233445566', name: 'Second'};
      // Adopt the server-rendered anchor rather than replacing it.
      const grid = document.getElementById('card-grid');
      grid.innerHTML = '<a class="card" data-mac="AA:BB:CC:DD:EE:FF" href="/sensor/AA:BB:CC:DD:EE:FF">' +
        '<div class="card-name">Initial</div><div class="card-meta"></div>' +
        '<dl class="card-metrics"><dt>Temp</dt><dd></dd><dt class="hour-change-label">Change / 1h</dt>' +
        '<dd class="hour-change"></dd><dt>Day</dt><dd></dd><dt>Night</dt><dd></dd>' +
        '<dt>Humidity</dt><dd></dd><dt>Pressure</dt><dd></dd><dt>Battery</dt><dd></dd></dl></a>';
      const initial = grid.firstElementChild;
      renderCards([a, b]);
      check(grid.firstElementChild === initial, 'SSR anchor replaced');
      check(!grid.querySelector('img'), 'Name interpreted as HTML');
      check(initial.querySelector('.card-name').textContent === a.name, 'Name mismatch');
      check(initial.querySelectorAll('dd')[1].textContent === '—', 'Null metric mismatch');
      check(getComputedStyle(initial.querySelector('.stale-flag')).display === 'none', 'Fresh card shows stale flag');
      initial.focus();
      const observer = new MutationObserver(() => {});
      observer.observe(grid, {subtree: true, childList: true, attributes: true, characterData: true});
      renderCards([a, b]);
      check(observer.takeRecords().length === 0, 'Unchanged refresh mutates DOM');
      check(document.activeElement === initial, 'Unchanged refresh loses focus');
      renderCards([{...a, temperature: 21, stale: true}, b]);
      check(initial.querySelector('dd').textContent === '21.0 °C', 'Metric update missing');
      check(!initial.querySelector('.stale-flag').hidden, 'Stale flag missing');
      check(getComputedStyle(initial.querySelector('.stale-flag')).display !== 'none', 'Stale flag invisible');
      check(initial.classList.contains('stale'), 'Stale class missing');
      renderCards([b, {...a, name: 'Renamed', stale: false}]);
      check(grid.children[1] === initial, 'Reorder replaced anchor');
      check(initial.querySelector('.stale-flag').hidden, 'Stale flag not cleared');
      check(getComputedStyle(initial.querySelector('.stale-flag')).display === 'none', 'Cleared stale flag visible');
      renderCards([a]);
      check(grid.children.length === 1 && grid.firstElementChild === initial, 'Removal mismatch');
      renderCards([]);
      check(grid.querySelector('p') && !grid.querySelector('a'), 'Empty state mismatch');
      renderCards([b]);
      check(!grid.querySelector('p') && grid.querySelector('a'), 'Empty recovery mismatch');
      check(grid.querySelector('a').getAttribute('href') === '/sensor/112233445566', 'Link mismatch');
      observer.disconnect();
    }""")


def benchmark(args):
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            if not args.skip_checks:
                page = browser.new_page()
                load(page, args.dashboard_js)
                verify(page)
                page.close()
            for count in args.tags:
                for workload in ["unchanged", "one_changed", "all_changed"]:
                    page = browser.new_page(viewport={"width": 1280, "height": 900})
                    load(page, args.dashboard_js)
                    samples = page.evaluate("""({count, repetitions, workload}) => {
                      const cards = Array.from({length: count}, (_, i) => ({mac: i.toString(16).padStart(12, '0'),
                        name: `Tag ${i}`, timestamp: '2026-10-03T23:59:55Z',
                        temperature: 20, humidity: 50, pressure: 1000, battery: 2900, stale: false}));
                      const grid = document.getElementById('card-grid');
                      renderCards(cards); void grid.offsetHeight;
                      const samples = [];
                      for (let i = 0; i < repetitions; i++) {
                        const updated = cards.map((card, index) => ({...card,
                          temperature: workload === 'all_changed' || (workload === 'one_changed' && index === 0)
                            ? 20 + (i + 1) / 10 : 20}));
                        const started = performance.now();
                        renderCards(updated); void grid.offsetHeight;
                        samples.push(performance.now() - started);
                      }
                      return samples;
                    }""", {"count": count, "repetitions": args.repetitions, "workload": workload})
                    results.append({"tags": count, "workload": workload,
                                    "median_ms": statistics.median(samples),
                                    "p95_ms": sorted(samples)[math.ceil(len(samples) * .95) - 1]})
                    page.close()
            return {"platform": platform.platform(), "browser": browser.version,
                    "script": str(args.dashboard_js), "repetitions": args.repetitions,
                    "checks_passed": not args.skip_checks, "render_and_layout": results}
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-js", type=Path, default=ROOT / "src/web/static/dashboard.js")
    parser.add_argument("--tags", type=int, nargs="+", default=[1, 10, 50])
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--skip-checks", action="store_true")
    args = parser.parse_args()
    if min(*args.tags, args.repetitions) < 1:
        parser.error("tags and repetitions must be positive")
    print(json.dumps(benchmark(args), indent=2))
