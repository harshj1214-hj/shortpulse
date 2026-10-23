/* Renders an /api/analytics/* report. Used by the dashboard and the link page. */
const Report = (() => {
  const { h, fmt } = LP;
  const charts = new WeakMap();

  function pulse(canvas, report, { dark = true } = {}) {
    if (!canvas) return;
    if (!window.Chart) { // CDN blocked: keep the rest of the page working
      canvas.replaceWith(h("p", { class: "small px-4", style: dark ? "color:rgba(255,255,255,.7)" : "",
        text: "The chart library didn't load. The numbers below are still live." }));
      return;
    }
    const hourly = report.bucket === 3600;
    const labels = report.series.map((p) => (hourly ? fmt.hour(p.t) : fmt.day(p.t)));
    const human = report.series.map((p) => p.human);
    const bots = report.series.map((p) => p.bots);
    const onDark = dark || LP.isDark(); // dark panel, or a light panel in the dark theme
    const line = onDark ? "#6FB8EC" : "#0A6FB8";
    const grid = onDark ? "rgba(255,255,255,0.08)" : "#EDF1F5";
    const tick = onDark ? "rgba(255,255,255,0.55)" : "#5B6B7F";
    const old = charts.get(canvas);
    if (old) old.destroy();
    charts.set(canvas, new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "People", data: human, borderColor: line, borderWidth: 2.25, pointRadius: 0,
            pointHoverRadius: 4, tension: 0, fill: true,
            backgroundColor: (ctx) => {
              const { chartArea, ctx: c } = ctx.chart;
              if (!chartArea) return "transparent";
              const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
              g.addColorStop(0, onDark ? "rgba(111,184,236,0.28)" : "rgba(10,111,184,0.16)");
              g.addColorStop(1, "rgba(10,111,184,0)");
              return g;
            } },
          { label: "Bots (filtered out)", data: bots, borderColor: onDark ? "rgba(255,255,255,0.35)" : "#AAB6C3",
            borderDash: [4, 4], borderWidth: 1.5, pointRadius: 0, tension: 0, fill: false },
        ],
      },
      options: {
        maintainAspectRatio: false,
        animation: { duration: 900, easing: "easeOutQuart" },
        interaction: { mode: "index", intersect: false },
        layout: { padding: { left: 12, right: 12 } },
        plugins: { legend: { display: true, align: "end", labels: { color: tick, boxWidth: 18, boxHeight: 2 } } },
        scales: {
          x: { grid: { display: false }, ticks: { color: tick, maxTicksLimit: 8, maxRotation: 0 } },
          y: { beginAtZero: true, grid: { color: grid }, border: { display: false },
               ticks: { color: tick, precision: 0, maxTicksLimit: 4 } },
        },
      },
    }));
  }

  function stats(container, report, { showLinks = true } = {}) {
    const c = report.clicks, l = report.links;
    const items = [[c.unique_visitors, "unique visitors"], [c.qr_scans, "QR scans"], [c.bots, "bot visits filtered"]];
    if (c.app_opens) items.push([c.app_opens, "sent to the app"]);
    if (showLinks) items.push([l.active, "active links"], [l.expired, "expired"], [l.limit_reached, "at click limit"], [l.disabled, "disabled"]);
    const keys = items.map((i) => i[1]).join("|");
    if (container.dataset.keys !== keys) { // rebuild only when the set of numbers changes, so values animate
      container.replaceChildren(...items.map(([, label]) =>
        h("div", { class: "lp-stat" }, h("div", { class: "v" }), h("div", { class: "l", text: label }))));
      container.dataset.keys = keys;
    }
    [...container.children].forEach((el, i) => LP.countUp(el.querySelector(".v"), items[i][0] || 0));
  }

  function alerts(list, report, emptyText = "Nothing needs your attention.") {
    list.replaceChildren();
    if (!report.alerts.length) { list.append(h("li", {}, h("span"), h("span", { class: "text-secondary", text: emptyText }))); return; }
    for (const a of report.alerts.slice(0, 12)) {
      list.append(h("li", { class: a.severity }, h("span", { class: "bar", "aria-hidden": "true" }),
        h("div", {},
          h("a", { href: `/links/${a.link_id}`, class: "fw-semibold text-decoration-none" }, a.title || `/${a.code}`),
          " ", h("span", { class: "code small", text: `/${a.code}` }),
          h("div", { class: "small text-secondary", text: a.message }))));
    }
  }

  function breakdowns(report, prefix = "b-") {
    for (const name of ["device", "browser", "os", "country", "referrer", "source"]) {
      const el = document.getElementById(prefix + name);
      if (!el) continue;
      const rows = (report.breakdowns[name] || []).map((r) =>
        ({ ...r, label: name === "source" ? (r.label === "qr" ? "QR code" : "Link") : r.label }));
      LP.bars(el, rows);
    }
  }

  function changeText(pct, period) {
    if (pct === null || pct === undefined) return "";
    if (pct === 0) return `Same as the previous ${period}.`;
    return `${Math.abs(pct)}% ${pct > 0 ? "more" : "fewer"} than the previous ${period}.`;
  }

  const PERIOD_WORDS = { "24h": "24 hours", "7d": "7 days", "30d": "30 days", "90d": "90 days" };

  return { pulse, stats, alerts, breakdowns, changeText, PERIOD_WORDS };
})();
