(() => {
  const { h, api, fmt } = LP;
  const $ = (id) => document.getElementById(id);
  const params = new URLSearchParams(location.search);

  async function loadFilters() {
    const [c, t] = await Promise.all([api("GET", "/api/campaigns"), api("GET", "/api/tags")]);
    for (const x of c.data.campaigns || []) $("campaign").append(h("option", { value: x.id, text: x.name }));
    for (const x of t.data.tags || []) $("tag").append(h("option", { value: x.name, text: `${x.name} (${x.links})` }));
    for (const k of ["campaign", "tag", "status"]) if (params.get(k)) $(k).value = params.get(k);
  }

  async function load() {
    const q = new URLSearchParams();
    if ($("q").value.trim()) q.set("q", $("q").value.trim());
    if ($("status").value) q.set("status", $("status").value);
    if ($("campaign").value) q.set("campaign_id", $("campaign").value);
    if ($("tag").value) q.set("tag", $("tag").value);
    if ($("scope")?.value) q.set("scope", $("scope").value);
    if (params.get("owner")) q.set("owner_id", params.get("owner"));
    const { ok, data } = await api("GET", `/api/links?${q}`);
    if (!ok) return;
    const rows = $("rows");
    rows.replaceChildren();
    if (!data.links.length) {
      rows.append(h("tr", {}, h("td", { colspan: 8, class: "text-secondary p-4" },
        "No links match. ", h("a", { href: "/links/new", text: "Create a link" }), " or clear the filters.")));
      return;
    }
    for (const [i, l] of data.links.entries()) {
      rows.append(h("tr", { class: "lp-row-in", style: `animation-delay:${Math.min(i, 15) * 25}ms` },
        h("td", {}, h("a", { href: `/links/${l.id}`, class: "text-decoration-none" }, h("span", { class: "code", text: `/${l.code}` })),
          l.title ? h("div", { class: "small text-secondary", text: l.title }) : null,
          l.owner_email ? h("div", { class: "small text-secondary", text: l.owner_email }) : null),
        h("td", {}, h("div", { class: "lp-dest", title: l.destination, text: l.destination })),
        h("td", { class: "small" }, l.campaign_name || h("span", { class: "text-secondary", text: "None" }),
          l.tags.length ? h("div", { class: "text-secondary", text: l.tags.join(", ") }) : null),
        h("td", {}, LP.status(l.effective_status),
          l.has_password ? h("div", { class: "small text-secondary", text: "Password" }) : null,
          l.max_clicks ? h("div", { class: "small text-secondary", text: `${fmt.num(l.click_count)} of ${fmt.num(l.max_clicks)} clicks` }) : null,
          l.flagged ? h("div", { class: "small text-danger", text: "Awaiting review" }) : null),
        h("td", { class: "lp-num", text: fmt.num(l.human_clicks) }),
        h("td", { class: "lp-num text-secondary", text: fmt.num(l.bot_clicks) }),
        h("td", { class: "small text-secondary", text: fmt.day(l.created_at) }),
        h("td", { class: "text-nowrap" },
          h("button", { class: "btn btn-sm btn-outline-primary me-1", type: "button", "data-copy": l.short_url,
            "aria-label": `Copy ${l.short_url}` }, "Copy"),
          h("a", { class: "btn btn-sm btn-outline-primary", href: `/links/${l.id}/studio`, "aria-label": `QR code for /${l.code}` }, "QR"))));
    }
  }

  $("filters").addEventListener("submit", (e) => e.preventDefault());
  $("q").addEventListener("input", LP.debounce(load, 300));
  for (const id of ["status", "campaign", "tag", "scope"]) $(id)?.addEventListener("change", load);
  document.addEventListener("lp:link-created", () => setTimeout(load, 300));
  loadFilters().then(load);
})();
