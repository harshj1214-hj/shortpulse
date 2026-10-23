(() => {
  const { h, api, fmt } = LP;
  const $ = (id) => document.getElementById(id);
  const empty = (cols, text) => h("tr", {}, h("td", { colspan: cols, class: "text-secondary p-4", text }));
  const linkCell = (l) => h("td", {}, h("a", { href: `/links/${l.id}`, class: "text-decoration-none" }, h("span", { class: "code", text: `/${l.code}` })),
    l.title ? h("div", { class: "small text-secondary", text: l.title }) : null);
  const destCell = (l) => h("td", {}, h("div", { class: "lp-dest", title: l.destination, text: l.destination }));
  const btn = (label, cls, fn) => h("button", { class: `btn btn-sm ${cls}`, type: "button", onclick: fn }, label);

  async function disable(l) {
    const reason = window.prompt(`Disable /${l.code}? Reason shown to the owner:`, "Phishing or malware");
    if (reason === null) return;
    const { ok } = await api("POST", `/api/admin/links/${l.id}/disable`, { reason });
    if (ok) { LP.toast(`/${l.code} disabled. Redirects stopped.`); refresh(); }
  }
  async function enable(l) {
    const { ok } = await api("POST", `/api/admin/links/${l.id}/enable`);
    if (ok) { LP.toast(`/${l.code} restored.`); refresh(); }
  }
  async function dismiss(l) {
    const { ok } = await api("POST", `/api/admin/links/${l.id}/dismiss-flag`);
    if (ok) { LP.toast("Marked as safe."); refresh(); }
  }

  async function loadReview() {
    const { ok, data } = await api("GET", "/api/links?scope=all&flagged=1");
    if (!ok) return;
    $("flag-count").textContent = data.links.length || "";
    const body = $("review");
    body.replaceChildren(...(data.links.length ? data.links.map((l) => h("tr", {}, linkCell(l), destCell(l),
      h("td", { class: "small", text: `${Math.round((l.risk_score || 0) * 100)}% risk. ${l.risk_reasons || ""}` }),
      h("td", { class: "small", text: l.owner_email }),
      h("td", { class: "text-nowrap" }, btn("Disable", "btn-danger me-1", () => disable(l)), btn("Mark safe", "btn-outline-primary", () => dismiss(l)))))
      : [empty(5, "Nothing to review. Links the URL checker rates as high risk appear here.")]));
  }

  async function loadAll() {
    const q = $("q").value.trim();
    const { ok, data } = await api("GET", `/api/links?scope=all${q ? `&q=${encodeURIComponent(q)}` : ""}`);
    if (!ok) return;
    $("all").replaceChildren(...(data.links.length ? data.links.map((l) => h("tr", {}, linkCell(l), destCell(l),
      h("td", { class: "small", text: l.owner_email }), h("td", {}, LP.status(l.effective_status)),
      h("td", { class: "lp-num", text: fmt.num(l.human_clicks) }),
      h("td", {}, l.status === "disabled" ? btn("Restore", "btn-outline-primary", () => enable(l)) : btn("Disable", "btn-outline-danger", () => disable(l)))))
      : [empty(6, "No links match.")]));
  }

  async function loadBlocklist() {
    const { ok, data } = await api("GET", "/api/admin/blocklist");
    if (!ok) return;
    $("blocklist").replaceChildren(...(data.domains.length ? data.domains.map((d) => h("tr", {},
      h("td", {}, h("span", { class: "code", text: d.domain })), h("td", { class: "small", text: d.reason }),
      h("td", { class: "small", text: d.added_by || "" }), h("td", { class: "small", text: fmt.day(d.created_at) }),
      h("td", {}, btn("Remove", "btn-outline-danger", async () => {
        if (!confirm(`Allow links to ${d.domain} again? Links that were disabled stay disabled.`)) return;
        if ((await api("DELETE", `/api/admin/blocklist/${d.id}`)).ok) loadBlocklist();
      }))))
      : [empty(5, "No blocked domains.")]));
  }

  const ACTIONS = { link_disabled: "Disabled link", link_restored: "Restored link", flag_dismissed: "Marked link safe",
    domain_blocked: "Blocked domain", domain_unblocked: "Unblocked domain", user_updated: "Changed account",
    password_set: "Set new password", links_disabled: "Disabled user's links", user_deleted: "Deleted account",
    campaign_updated: "Renamed campaign", campaign_deleted: "Deleted campaign" };
  const STATUS = { active: "lp-status active", suspended: "lp-status expired", deleted: "lp-status disabled" };
  let allUsers = [];

  function renderUsers() {
    const q = $("user-q").value.trim().toLowerCase();
    const rows = allUsers.filter((u) => !q || u.email.toLowerCase().includes(q));
    $("users").replaceChildren(...(rows.length ? rows.map((u, i) => h("tr", { class: "lp-row-in", style: `animation-delay:${Math.min(i, 15) * 25}ms` },
      h("td", {}, h("a", { href: `/admin/users/${u.id}`, class: "fw-semibold text-decoration-none", text: u.email })),
      h("td", { text: u.role === "admin" ? "Admin" : "User" }),
      h("td", {}, h("span", { class: STATUS[u.status] || "lp-status", text: u.status[0].toUpperCase() + u.status.slice(1) })),
      h("td", { class: "lp-num", text: fmt.num(u.links) }),
      h("td", { class: "lp-num", text: fmt.num(u.human_clicks) }),
      h("td", { class: "small", text: u.last_login_at ? fmt.rel(u.last_login_at) : "Never" }),
      h("td", { class: "small", text: fmt.day(u.created_at) }),
      h("td", {}, h("a", { class: "btn btn-sm btn-outline-primary", href: `/admin/users/${u.id}` }, "Manage"))))
      : [empty(8, "No users match.")]));
  }

  async function loadUsers() {
    const { ok, data } = await api("GET", `/api/admin/users${$("show-deleted").checked ? "?deleted=1" : ""}`);
    if (!ok) return;
    allUsers = data.users;
    renderUsers();
  }

  async function loadAudit() {
    const { ok, data } = await api("GET", "/api/admin/audit?limit=200");
    if (!ok) return;
    $("audit").replaceChildren(...(data.actions.length ? data.actions.map((a) => h("tr", {},
      h("td", { class: "small text-nowrap", text: fmt.date(a.created_at) }),
      h("td", { class: "small", text: a.admin_email || "" }),
      h("td", { class: "small fw-semibold", text: ACTIONS[a.action] || a.action }),
      h("td", { class: "small" }, a.target_email && a.target_type === "user"
        ? [h("a", { href: `/admin/users/${a.target_id}`, text: a.target_email }), " · "] : null, a.detail)))
      : [empty(4, "No admin actions yet.")]));
  }

  $("block-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("block-error").replaceChildren();
    const { ok, data } = await api("POST", "/api/admin/blocklist", { domain: $("domain").value, reason: $("reason").value,
      disable_existing: $("disable-existing").checked });
    if (!ok) { $("block-error").append(h("div", { class: "lp-issue error", text: data.message })); return; }
    LP.toast(`${data.domain} blocked. ${data.links_disabled} existing links disabled.`);
    e.target.reset();
    $("disable-existing").checked = true;
    refresh();
  });
  $("q").addEventListener("input", LP.debounce(loadAll, 300));
  $("user-q").addEventListener("input", renderUsers);
  $("show-deleted").addEventListener("change", loadUsers);
  document.querySelector('[data-bs-target="#t-audit"]').addEventListener("shown.bs.tab", loadAudit);

  function refresh() { loadReview(); loadAll(); loadBlocklist(); if (allUsers.length) loadUsers(); }
  refresh();
  loadUsers();
})();
