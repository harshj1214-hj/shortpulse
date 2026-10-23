/* Admin: one user's account. Every change here is recorded in the activity log. */
(() => {
  const { h, api, fmt } = LP;
  const $ = (id) => document.getElementById(id);
  const ACTIONS = { user_updated: "Changed account", password_set: "Set new password", links_disabled: "Disabled links",
                    user_deleted: "Deleted account", campaign_updated: "Renamed campaign", campaign_deleted: "Deleted campaign" };
  let d = null;

  const empty = (cols, text) => h("tr", {}, h("td", { colspan: cols, class: "text-secondary", text }));

  function render() {
    const u = d.user, deleted = u.status === "deleted";
    document.title = `${u.email} · ShortPulse`;
    $("u-email").textContent = u.email;
    const appTitle = document.getElementById("appbar-title");
    if (appTitle) appTitle.textContent = u.email;
    $("u-meta").replaceChildren(
      h("span", { class: `lp-status ${u.status === "active" ? "active" : u.status === "suspended" ? "expired" : "disabled"}`,
                  text: u.status[0].toUpperCase() + u.status.slice(1) }), " · ",
      u.role === "admin" ? "Admin" : "User", " · ",
      `joined ${fmt.date(u.created_at)} · `,
      u.last_login_at ? `last signed in ${fmt.rel(u.last_login_at)}` : "never signed in");
    $("u-links-page").href = `/links?owner=${u.id}`;
    $("u-banner").replaceChildren(deleted ? h("div", { class: "alert alert-danger", text: "This account was deleted. Its links are kept, disabled, for traceability." }) : "");

    $("f-email").value = u.email;
    $("f-role").value = u.role;
    $("self-note").hidden = !d.is_self;
    for (const id of ["f-role", "save-role", "activate", "suspend", "delete-user"]) $(id).disabled = d.is_self || deleted;
    for (const id of ["f-email", "save-email", "f-password", "save-password", "disable-links"]) $(id).disabled = deleted;
    $("activate").classList.toggle("active", u.status === "active");
    $("suspend").classList.toggle("active", u.status === "suspended");

    const s = d.links_summary, c = d.clicks;
    const stats = [[s.total, "links"], [s.active, "active"], [s.disabled, "disabled"], [c.human, "clicks by people"],
                   [c.human_30d, "clicks, last 30 days"], [c.unique_visitors, "unique visitors"], [c.bots, "bot visits"]];
    $("u-stats").replaceChildren(...stats.map(([v, l]) => h("div", { class: "lp-stat" }, h("div", { class: "v", text: fmt.num(v) }), h("div", { class: "l", text: l }))));

    $("u-links").replaceChildren(...(d.links.length ? d.links.map((l) => h("tr", {},
      h("td", {}, h("a", { href: `/links/${l.id}`, class: "text-decoration-none" }, h("span", { class: "code", text: `/${l.code}` })),
        l.title ? h("div", { class: "small text-secondary", text: l.title }) : null),
      h("td", {}, h("div", { class: "lp-dest", style: "max-width:22ch", title: l.destination, text: l.destination })),
      h("td", {}, LP.status(l.effective_status)),
      h("td", { class: "lp-num", text: fmt.num(l.human_clicks) }),
      h("td", { class: "text-nowrap" },
        h("a", { class: "btn btn-sm btn-outline-primary me-1", href: `/links/${l.id}` }, "Open"),
        l.status === "disabled"
          ? h("button", { class: "btn btn-sm btn-outline-primary", onclick: () => linkAction(l, "enable") }, "Restore")
          : h("button", { class: "btn btn-sm btn-outline-danger", onclick: () => linkAction(l, "disable") }, "Disable"))))
      : [empty(5, "No links.")]));

    $("u-campaigns").replaceChildren(...(d.campaigns.length ? d.campaigns.map((c) => h("tr", {},
      h("td", {}, h("div", { class: "fw-semibold", text: c.name }), c.description ? h("div", { class: "small text-secondary", text: c.description }) : null),
      h("td", { class: "lp-num", text: fmt.num(c.links) }),
      h("td", { class: "small", text: fmt.day(c.created_at) }),
      h("td", { class: "text-nowrap" },
        h("button", { class: "btn btn-sm btn-outline-primary me-1", onclick: () => renameCampaign(c) }, "Rename"),
        h("button", { class: "btn btn-sm btn-outline-danger", onclick: () => deleteCampaign(c) }, "Delete"))))
      : [empty(4, "No campaigns.")]));

    $("u-visits").replaceChildren(...(d.visits.length ? d.visits.map((v) => h("tr", {},
      h("td", { class: "small text-nowrap", text: fmt.date(v.ts) }),
      h("td", {}, h("a", { href: `/links/${v.link_id}`, class: "code small text-decoration-none", text: `/${v.code}` })),
      h("td", { class: "small font-monospace", text: v.ip || "" }),
      h("td", { class: "small" }, v.is_bot ? h("span", { class: "text-danger", text: `Bot: ${v.bot_reason}` }) : `${v.device}, ${v.browser}`),
      h("td", { class: "small", text: [v.city, v.country].filter(Boolean).join(", ") }),
      h("td", { class: "small", text: v.source === "qr" ? "QR code" : (v.referrer_domain || "Direct") })))
      : [empty(6, "No visits yet.")]));

    $("u-history").replaceChildren(...(d.history.length ? d.history.map((a) => h("li", { class: "low" },
      h("span", { class: "bar" }),
      h("div", {}, h("span", { class: "fw-semibold", text: ACTIONS[a.action] || a.action }), ` by ${a.admin_email || "an admin"}`,
        h("div", { class: "small text-secondary", text: `${fmt.date(a.created_at)}${a.detail ? " · " + a.detail : ""}` }))))
      : [h("li", {}, h("span"), h("span", { class: "text-secondary", text: "No admin changes yet." }))]));
  }

  async function load() {
    const { ok, data } = await api("GET", `/api/admin/users/${USER_ID}`);
    if (ok) { d = data; render(); }
  }

  async function update(body, success) {
    const { ok, data } = await api("PATCH", `/api/admin/users/${USER_ID}`, body);
    if (!ok) { LP.toast(data.message || "Couldn't save.", "danger"); return; }
    if (data.changed !== false) LP.toast(success);
    load();
  }

  async function linkAction(l, action) {
    let body;
    if (action === "disable") {
      const reason = window.prompt(`Disable /${l.code}? Reason shown to the owner:`, "Violates platform policy");
      if (reason === null) return;
      body = { reason };
    }
    if ((await api("POST", `/api/admin/links/${l.id}/${action}`, body)).ok) { LP.toast(action === "disable" ? "Link disabled." : "Link restored."); load(); }
  }

  async function renameCampaign(c) {
    const name = window.prompt("New campaign name:", c.name);
    if (name === null || !name.trim()) return;
    const { ok, data } = await api("PATCH", `/api/admin/campaigns/${c.id}`, { name: name.trim() });
    if (ok) { LP.toast("Campaign renamed."); load(); } else LP.toast(data.message || "Couldn't rename.", "danger");
  }

  async function deleteCampaign(c) {
    if (!confirm(`Delete the campaign "${c.name}"? Its ${c.links} links stay, without a campaign.`)) return;
    if ((await api("DELETE", `/api/admin/campaigns/${c.id}`)).ok) { LP.toast("Campaign deleted."); load(); }
  }

  $("save-email").addEventListener("click", () => update({ email: $("f-email").value }, "Email updated."));
  $("save-role").addEventListener("click", () => update({ role: $("f-role").value }, "Role updated."));
  $("activate").addEventListener("click", () => update({ status: "active" }, "Account reactivated."));
  $("suspend").addEventListener("click", () => {
    if (!confirm(`Suspend ${d.user.email}? They'll be signed out and can't sign in.`)) return;
    update({ status: "suspended", disable_links: $("suspend-links").checked }, "Account suspended.");
  });
  $("save-password").addEventListener("click", async () => {
    const password = $("f-password").value;
    const { ok, data } = await api("POST", `/api/admin/users/${USER_ID}/password`, { password });
    if (!ok) { LP.toast(data.message || "Couldn't set the password.", "danger"); return; }
    $("f-password").value = "";
    LP.toast("New password set. Tell the user privately.");
    load();
  });
  $("disable-links").addEventListener("click", async () => {
    const reason = window.prompt("Disable ALL links of this user? Reason shown to the owner:", "Violates platform policy");
    if (reason === null) return;
    const { ok, data } = await api("POST", `/api/admin/users/${USER_ID}/disable-links`, { reason });
    if (ok) { LP.toast(`${data.links_disabled} links disabled.`); load(); }
  });
  $("delete-user").addEventListener("click", async () => {
    const typed = window.prompt(`This deletes ${d.user.email}. Type the email to confirm:`);
    if (typed === null) return;
    if (typed.trim().toLowerCase() !== d.user.email.toLowerCase()) { LP.toast("The email didn't match. Nothing was deleted.", "warning"); return; }
    const { ok, data } = await api("DELETE", `/api/admin/users/${USER_ID}`);
    if (ok) { LP.toast(`Account deleted. ${data.links_disabled} links disabled.`); load(); }
    else LP.toast(data.message || "Couldn't delete.", "danger");
  });

  load();
})();
