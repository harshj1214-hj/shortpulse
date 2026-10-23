(() => {
  const { h, api, fmt } = LP;
  const $ = (id) => document.getElementById(id);
  let link = null, period = "7d", lastReport = null;

  function renderLink() {
    document.title = `${link.title || "/" + link.code} · ShortPulse`;
    $("title").textContent = link.title || `/${link.code}`;
    const appTitle = document.getElementById("appbar-title");
    if (appTitle) appTitle.textContent = link.title || `/${link.code}`;
    $("short").textContent = link.short_url;
    $("copy").dataset.copy = link.short_url;
    $("status").replaceChildren(LP.status(link.effective_status));
    $("dest").replaceChildren("Goes to ", h("a", { href: link.destination, rel: "noopener noreferrer nofollow", target: "_blank", text: link.destination }));
    $("qr").onerror = () => { $("qr").replaceWith(h("p", { class: "small text-secondary", text: "QR images aren't available on this server (install qrcode and Pillow)." })); };
    $("qr").src = `/api/links/${link.id}/qr.png?size=360&v=${link.updated_at}`;
    $("qr-download").href = `/api/links/${link.id}/qr.png?size=2048&download=1`;

    const banner = $("banner");
    banner.replaceChildren();
    if (link.status === "disabled") banner.append(h("div", { class: "alert alert-danger", text:
      `An admin disabled this link: ${link.disabled_reason || "no reason given"}. Visitors see a "disabled" page. Its analytics are kept.` }));
    else if (link.flagged) banner.append(h("div", { class: "alert alert-warning", text:
      `This link is waiting for admin review: ${link.risk_reasons || "the URL checker rated it high risk"}.` }));

    $("toggle").hidden = link.status === "disabled";
    $("toggle").textContent = link.status === "paused" ? "Resume link" : "Pause link";
    const adminBtn = $("admin-toggle");
    if (adminBtn) { adminBtn.hidden = false; adminBtn.textContent = link.status === "disabled" ? "Restore link" : "Disable link"; }

    const rules = [
      ["Expiry", link.expires_at ? `${fmt.date(link.expires_at)} (${fmt.rel(link.expires_at)})` : "Never"],
      ["Click limit", link.max_clicks ? `${fmt.num(link.click_count)} of ${fmt.num(link.max_clicks)} used` : "No limit"],
      ["Password", link.has_password ? "Required" : "None"],
      ["On phones", link.app ? (link.open_in_app ? `Opens the ${link.app.label} app` : "Opens in the browser") : "Opens in the browser"],
      ["Landing page", link.landing_enabled ? "Shown first" : "Off"],
      ["Engagement", link.landing_enabled ? "Measured on the landing page" : link.track_engagement ? "Measured (needs the script)" : "Off"],
      ["Campaign", link.campaign_name || "None"],
      ["Tags", link.tags.join(", ") || "None"],
      ["Created", fmt.date(link.created_at)],
    ];
    if (link.owner_email) rules.push(["Owner", link.owner_email]);
    $("rules").replaceChildren(...rules.flatMap(([k, v]) =>
      [h("dt", { class: "col-5 fw-normal text-secondary", text: k }), h("dd", { class: "col-7", text: v })]));

    $("snippet-box").hidden = !link.track_engagement || link.landing_enabled;
    $("lp-preview").href = link.short_url;
    const tag = `<script src="${location.origin}/static/lp-beacon.js" defer></script>`;
    $("snippet").textContent = tag;
    $("snippet-copy").dataset.copy = tag;
  }

  async function loadLink() {
    const { ok, data } = await api("GET", `/api/links/${LINK_ID}`);
    if (ok) { link = data.link; renderLink(); if (lastReport) renderEngagement(lastReport.engagement); }
  }

  function renderEngagement(e) {
    const measuring = link && (link.landing_enabled || link.track_engagement);
    $("eng-empty").hidden = !link || measuring || e.visits > 0;
    $("eng-data").hidden = !measuring && e.visits === 0;
    const reading = $("reading");
    reading.classList.toggle("on", e.reading_now > 0);
    $("reading-text").textContent = `${e.reading_now} reading now`;
    const items = e.visits
      ? [[fmt.secs(e.median_seconds), "median time on page"], [fmt.secs(e.avg_seconds), "average time"],
         [`${e.avg_scroll}%`, "average scroll depth"], [fmt.num(e.visits), "measured visits"]]
      : [["—", "waiting for the first visit"]];
    if (e.continued_pct !== null && e.continued_pct !== undefined) items.push([`${e.continued_pct}%`, "pressed a button"]);
    const box = $("eng-stats");
    const sig = items.map((i) => i.join("=")).join("|");
    if (box.dataset.sig !== sig) {
      box.dataset.sig = sig;
      box.replaceChildren(...items.map(([v, l]) => h("div", { class: "lp-stat" }, h("div", { class: "v", text: v }), h("div", { class: "l", text: l }))));
    }
    const funnel = $("funnel");
    funnel.replaceChildren(...(e.funnel.length ? e.funnel : [25, 50, 75, 100].map((d) => ({ depth: d, pct: 0 }))).map((f, i) =>
      h("div", { class: "lp-funnel-row" },
        h("span", { class: "small", text: f.depth === 100 ? "Reached the end" : `Scrolled ${f.depth}%` }),
        h("span", { class: "track" }, h("span", { class: "fill", style: `width:${f.pct}%;animation-delay:${i * 90}ms` })),
        h("span", { class: "small lp-num", text: `${f.pct}%` }))));
  }

  // ---- landing page editor
  const THEMES = { ink: "#0A6FB8", sunrise: "#D9480F", mint: "#0B7A5C", violet: "#5F3DC4" };
  let page = null;
  function linkRow(item = { label: "", url: "" }) {
    const row = h("div", { class: "d-flex gap-2 mb-2 lp-link-row" },
      h("input", { class: "form-control form-control-sm", placeholder: "Button text", maxlength: 40, "aria-label": "Button text", value: item.label }),
      h("input", { class: "form-control form-control-sm", placeholder: "https://…", "aria-label": "Button link", value: item.url }),
      h("button", { class: "btn btn-sm btn-outline-danger", type: "button", "aria-label": "Remove button", onclick: () => row.remove() }, "×"));
    return row;
  }
  function renderLanding() {
    $("lp-enabled").checked = page.enabled;
    $("lp-heading").value = page.heading;
    $("lp-message").value = page.message;
    $("lp-button").value = page.button_label === "Continue" ? "" : page.button_label;
    $("lp-themes").replaceChildren(...Object.entries(THEMES).map(([name, color]) => h("button", {
      type: "button", class: "lp-swatch", role: "radio", "aria-label": name, title: name, "aria-pressed": String(page.theme === name),
      "aria-checked": String(page.theme === name), style: `background:${color}`,
      onclick: () => { page.theme = name; renderLanding(); } })));
    $("lp-links").replaceChildren(...page.links.map((l) => linkRow(l)));
  }
  async function loadLanding() {
    const { ok, data } = await api("GET", `/api/links/${LINK_ID}/landing`);
    if (ok) { page = data.page; renderLanding(); }
  }
  async function saveLanding() {
    const body = {
      enabled: $("lp-enabled").checked, heading: $("lp-heading").value, message: $("lp-message").value,
      button_label: $("lp-button").value || "Continue", theme: page.theme,
      links: [...document.querySelectorAll(".lp-link-row")].map((r) => ({ label: r.children[0].value, url: r.children[1].value })),
    };
    const { ok, data } = await api("PUT", `/api/links/${LINK_ID}/landing`, body);
    $("lp-issues").replaceChildren();
    if (!ok) { LP.issues($("lp-issues"), data.errors || [], "error"); return; }
    page = data.page;
    renderLanding();
    LP.toast(page.enabled ? "Landing page is live. Open the short link to see it." : "Landing page turned off.");
    loadLink();
  }
  $("lp-add").addEventListener("click", () => {
    if (document.querySelectorAll(".lp-link-row").length >= 5) { LP.toast("Up to 5 extra buttons.", "warning"); return; }
    $("lp-links").append(linkRow());
  });
  $("lp-save").addEventListener("click", saveLanding);
  $("eng-landing").addEventListener("click", () => {
    $("lp-enabled").checked = true;
    $("landing-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(() => $("lp-heading").focus(), 500);
  });
  $("eng-script").addEventListener("click", async () => {
    const res = await patch({ track_engagement: true });
    if (res.ok) { LP.toast("Done. Copy the script below onto your website."); $("snippet-box").scrollIntoView({ behavior: "smooth" }); }
  });

  async function loadReport() {
    const { ok, data: r } = await api("GET", `/api/analytics/link/${LINK_ID}?period=${period}&tz=${LP.tz()}`);
    if (!ok) return;
    lastReport = r;
    $("headline").textContent = `${fmt.num(r.clicks.human)} ${r.clicks.human === 1 ? "visit" : "visits"} by people in the last ${Report.PERIOD_WORDS[r.period]}`;
    Report.pulse($("chart"), r, { dark: false });
    Report.stats($("stats"), r, { showLinks: false });
    Report.breakdowns(r);
    renderEngagement(r.engagement);
  }

  let seen = new Set();
  async function loadClicks() {
    const { ok, data } = await api("GET", `/api/links/${LINK_ID}/clicks?limit=50`);
    if (!ok) return;
    $("ip-head").textContent = data.full_ip ? "IP address" : "IP (shortened)";
    $("ip-note").textContent = data.full_ip
      ? "Full IP addresses are shown because you're an admin. They're erased after the retention period; the shortened form stays."
      : "IP addresses are shortened to protect visitors' privacy. Admins can see full addresses for abuse investigation.";
    const body = $("clicks");
    const first = seen.size === 0;
    body.replaceChildren();
    if (!data.clicks.length) body.append(h("tr", {}, h("td", { colspan: 7, class: "text-secondary", text: "No visits yet. Open the short link to see your first one here." })));
    for (const c of data.clicks) {
      const key = `${c.ts}|${c.ip}|${c.browser}|${c.source}`;
      const fresh = !first && !seen.has(key);
      seen.add(key);
      const eng = [c.engaged_ms !== null && c.engaged_ms !== undefined ? `${fmt.secs(c.engaged_ms / 1000)}, ${c.scroll_pct ?? 0}% scrolled` : "",
                   c.outbound ? `pressed “${c.outbound}”` : ""].filter(Boolean).join(" · ");
      body.append(h("tr", { class: fresh ? "lp-new" : "lp-row-in", style: `animation-delay:${Math.min(body.children.length, 12) * 25}ms` },
        h("td", { class: "small", text: fmt.date(c.ts) }),
        h("td", { class: "small" }, c.is_bot ? h("span", { class: "text-danger", text: `Bot: ${c.bot_reason}` }) : "Person",
          c.app_switch ? h("div", { class: "lp-tag mt-1", text: "sent to app" }) : null),
        h("td", { class: "small font-monospace", text: c.ip || "" }),
        h("td", { class: "small", text: `${c.device}, ${c.browser} on ${c.os}` }),
        h("td", { class: "small", text: [c.city, c.country].filter(Boolean).join(", ") }),
        h("td", { class: "small", text: c.source === "qr" ? "QR code" : (c.referrer_domain || "Direct") }),
        h("td", { class: "small", text: eng })));
    }
  }

  async function patch(body) {
    const res = await api("PATCH", `/api/links/${LINK_ID}`, body);
    if (res.ok) { link = res.data.link; renderLink(); }
    return res;
  }

  $("toggle").addEventListener("click", async () => {
    const res = await patch({ status: link.status === "paused" ? "active" : "paused" });
    if (res.ok) LP.toast(link.status === "paused" ? "Link paused. Visitors see a paused page." : "Link resumed.");
    else LP.issues($("banner"), res.data.errors, "error");
  });
  $("admin-toggle")?.addEventListener("click", async () => {
    if (link.status === "disabled") {
      if ((await api("POST", `/api/admin/links/${LINK_ID}/enable`)).ok) LP.toast("Link restored.");
    } else {
      const reason = window.prompt("Reason shown to the owner:", "Phishing or malware");
      if (reason === null) return;
      if ((await api("POST", `/api/admin/links/${LINK_ID}/disable`, { reason })).ok) LP.toast("Link disabled. Redirects stopped.");
    }
    loadLink();
  });

  // ---- edit modal
  $("edit").addEventListener("show.bs.modal", async () => {
    $("e-destination").value = link.destination;
    $("e-title").value = link.title;
    $("e-tags").value = link.tags.join(", ");
    $("e-expires").value = LP.inputFromEpoch(link.expires_at);
    $("e-max").value = link.max_clicks || "";
    $("e-password").value = "";
    $("e-remove-pw").checked = false;
    $("e-remove-pw").disabled = !link.has_password;
    $("e-app").checked = link.open_in_app;
    $("e-app-wrap").hidden = !link.app;
    if (link.app) $("e-app-label").textContent = `Open in the ${link.app.label} app on phones`;
    $("e-engage").checked = link.track_engagement;
    $("e-issues").replaceChildren();
    $("e-ack-wrap").hidden = true;
    const sel = $("e-campaign");
    if (sel.options.length === 1) {
      const { data } = await api("GET", "/api/campaigns");
      for (const c of data.campaigns || []) sel.append(h("option", { value: c.id, text: c.name }));
    }
    sel.value = link.campaign_id || "";
  });
  $("save").addEventListener("click", async () => {
    const body = {
      title: $("e-title").value, campaign_id: $("e-campaign").value || null, tags: $("e-tags").value,
      max_clicks: $("e-max").value || null, acknowledge_risk: $("e-ack").checked,
      open_in_app: $("e-app").checked, track_engagement: $("e-engage").checked,
    };
    const exp = LP.epochFromInput($("e-expires").value);
    if (exp !== link.expires_at) body.expires_at = exp;
    if ($("e-destination").value.trim() !== link.destination) body.destination = $("e-destination").value.trim();
    if ($("e-password").value) body.password = $("e-password").value;
    else if ($("e-remove-pw").checked) body.remove_password = true;
    const res = await patch(body);
    $("e-issues").replaceChildren();
    if (!res.ok) {
      LP.issues($("e-issues"), res.data.errors, "error");
      LP.issues($("e-issues"), res.data.warnings, "warning");
      if (res.data.risk?.level === "high") $("e-ack-wrap").hidden = false;
      return;
    }
    bootstrap.Modal.getInstance($("edit")).hide();
    LP.toast("Changes saved.");
  });

  $("period").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-period]");
    if (!b) return;
    document.querySelectorAll("#period button").forEach((x) => x.classList.toggle("active", x === b));
    period = b.dataset.period;
    loadReport();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "c" && !e.ctrlKey && !e.metaKey && !LP.typing(document.activeElement) && link) LP.copy(link.short_url, $("copy"));
  });
  document.addEventListener("lp:theme", () => { if (lastReport) Report.pulse($("chart"), lastReport, { dark: false }); });

  loadLink().then(() => { loadReport(); loadLanding(); });
  loadClicks();
  setInterval(() => { if (!document.hidden) loadReport(); }, 5000);   // live "reading now"
  setInterval(() => { if (!document.hidden) loadClicks(); }, 15000);
})();
