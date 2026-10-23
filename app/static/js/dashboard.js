(() => {
  const { h, api, fmt } = LP;
  const state = { period: "7d", campaign: "", scope: document.getElementById("scope")?.value || "" };
  let prevHuman = null, prevKey = "";

  async function loadCampaigns() {
    const { ok, data } = await api("GET", "/api/campaigns");
    if (!ok) return;
    const sel = document.getElementById("campaign");
    for (const c of data.campaigns) sel.append(h("option", { value: c.id, text: c.name }));
  }

  async function load() {
    const q = new URLSearchParams({ period: state.period, tz: LP.tz() });
    if (state.campaign) q.set("campaign_id", state.campaign);
    if (state.scope) q.set("scope", state.scope);
    const { ok, data: r } = await api("GET", `/api/analytics/overview?${q}`);
    if (!ok) return;
    const words = Report.PERIOD_WORDS[r.period];
    const who = state.scope === "all" ? "platform links" : "your links";

    const headline = document.getElementById("headline");
    if (!r.links.total) headline.replaceChildren(state.campaign ? "This campaign has no links yet." : "You haven't created any links yet.");
    else if (!r.clicks.human) headline.replaceChildren(`Nobody has opened ${who} in the last ${words}.`);
    else {
      const strong = h("strong");
      headline.replaceChildren(`People opened ${who} `, strong, ` ${r.clicks.human === 1 ? "time" : "times"} in the last ${words}.`);
      const key = `${state.period}|${state.campaign}|${state.scope}`;
      strong.dataset.value = key === prevKey && prevHuman !== null ? prevHuman : 0;
      LP.countUp(strong, r.clicks.human);
      if (key === prevKey && prevHuman !== null && r.clicks.human > prevHuman) {
        const live = document.getElementById("live");
        live.classList.remove("flash"); void live.offsetWidth; live.classList.add("flash");
        LP.toast(`${r.clicks.human - prevHuman} new ${r.clicks.human - prevHuman === 1 ? "visit" : "visits"} just now.`);
      }
      prevKey = key;
    }
    prevHuman = r.clicks.human;
    document.getElementById("subline").textContent =
      [Report.changeText(r.clicks.human_change_pct, words),
       r.clicks.bots ? `${fmt.num(r.clicks.bots)} automated visits were left out.` : ""].filter(Boolean).join(" ");

    Report.pulse(document.getElementById("pulse"), r, { dark: true });
    Report.stats(document.getElementById("stats"), r);
    Report.alerts(document.getElementById("alerts"), r);
    Report.breakdowns(r);
    LP.bars(document.getElementById("campaigns"),
      r.campaigns.map((c) => ({ label: `${c.name} (${c.links} ${c.links === 1 ? "link" : "links"})`, n: c.clicks })),
      "Create a campaign to group related links.");

    const top = document.getElementById("top");
    top.replaceChildren();
    if (!r.top_links.length) {
      top.append(h("tr", {}, h("td", { colspan: 5, class: "text-secondary" },
        "No clicks in this period yet. ", h("a", { href: "/links/new", text: "Create a link" }), " and share it.")));
    }
    for (const l of r.top_links) {
      top.append(h("tr", {},
        h("td", {}, h("a", { href: `/links/${l.id}`, class: "text-decoration-none" }, h("span", { class: "code", text: `/${l.code}` })),
          l.title ? h("div", { class: "small text-secondary", text: l.title }) : null),
        h("td", {}, h("div", { class: "lp-dest", title: l.destination, text: l.destination })),
        h("td", {}, LP.status(l.status)),
        h("td", { class: "lp-num", text: fmt.num(l.clicks) }),
        h("td", { class: "lp-num", text: fmt.num(l.unique_visitors) })));
    }
    const f = r.filters;
    document.getElementById("filters").textContent =
      `Bot filter: ${f.bot_filter}. URL checker: ${f.url_checker}. Location lookup: ${f.geo}. Updated ${fmt.date(r.generated_at)}.`;
  }

  document.getElementById("period").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-period]");
    if (!b) return;
    document.querySelectorAll("#period button").forEach((x) => x.classList.toggle("active", x === b));
    state.period = b.dataset.period;
    load();
  });
  document.getElementById("campaign").addEventListener("change", (e) => { state.campaign = e.target.value; load(); });
  document.getElementById("scope")?.addEventListener("change", (e) => { state.scope = e.target.value; load(); });

  document.addEventListener("lp:link-created", () => setTimeout(load, 500));
  loadCampaigns();
  load();
  setInterval(() => { if (!document.hidden) load(); }, 30000); // live data, refreshed every 30 s
})();
