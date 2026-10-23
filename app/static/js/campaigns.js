(() => {
  const { h, api, fmt } = LP;
  const $ = (id) => document.getElementById(id);

  async function load() {
    const { ok, data } = await api("GET", "/api/campaigns");
    if (!ok) return;
    const rows = $("rows");
    rows.replaceChildren();
    if (!data.campaigns.length) rows.append(h("tr", {}, h("td", { colspan: 5, class: "text-secondary p-4", text: "No campaigns yet. Create one, then pick it when you make a link." })));
    for (const c of data.campaigns) {
      rows.append(h("tr", {},
        h("td", {}, h("a", { href: `/links?campaign=${c.id}`, class: "fw-semibold text-decoration-none", text: c.name }),
          c.description ? h("div", { class: "small text-secondary", text: c.description }) : null),
        h("td", { class: "lp-num", text: fmt.num(c.links) }),
        h("td", { class: "lp-num", text: fmt.num(c.clicks_30d) }),
        h("td", { class: "small" }, c.top_link ? [h("span", { class: "code", text: `/${c.top_link.code}` }), ` ${fmt.num(c.top_link.clicks)} clicks`] : h("span", { class: "text-secondary", text: "No clicks yet" })),
        h("td", {}, h("button", { class: "btn btn-sm btn-outline-danger", type: "button", onclick: () => remove(c) }, "Delete"))));
    }
  }

  async function remove(c) {
    if (!confirm(`Delete "${c.name}"? Its ${c.links} links stay and keep their analytics; they just won't be in a campaign.`)) return;
    const { ok } = await api("DELETE", `/api/campaigns/${c.id}`);
    if (ok) { LP.toast("Campaign deleted."); load(); }
  }

  $("create").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("create-error").replaceChildren();
    const { ok, data } = await api("POST", "/api/campaigns", { name: $("name").value.trim(), description: $("description").value.trim() });
    if (!ok) { $("create-error").append(h("div", { class: "lp-issue error", text: data.message })); return; }
    e.target.reset();
    LP.toast("Campaign created.");
    load();
  });
  load();
})();
