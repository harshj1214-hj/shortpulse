/* Smart paste: pull the link out of whatever was copied, offer to strip
   tracking parameters, and shorten it from anywhere with Ctrl+V or drag-and-drop.
   Browsers don't allow reading the clipboard silently, so this listens for the
   user's own paste (or drop) instead. The pure functions are unit-tested in Node. */
const LPPaste = (() => {
  const TRACKING = [/^utm_/i, /^fbclid$/i, /^gclid$/i, /^dclid$/i, /^gbraid$/i, /^wbraid$/i, /^msclkid$/i,
                    /^mc_(cid|eid)$/i, /^igsh(id)?$/i, /^_hs(enc|mi)$/i, /^yclid$/i, /^ref_src$/i];
  const HOST_SPECIFIC = { "youtube.com": ["si", "feature"], "youtu.be": ["si", "feature"], "open.spotify.com": ["si"],
                          "instagram.com": ["igsh", "igshid"], "x.com": ["s", "t"], "twitter.com": ["s", "t"] };

  function extractUrl(text) {
    if (!text) return null;
    text = String(text).trim();
    const m = text.match(/https?:\/\/[^\s<>"'`]+/i);
    let url = m ? m[0] : (/^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)+(\/\S*)?$/i.test(text) ? "https://" + text : null);
    if (!url) return null;
    url = url.replace(/[.,;:!?'"]+$/, "");
    while (/[)\]]$/.test(url)) { // "(see https://example.com/a)" -> drop the unbalanced ")"
      const open = (url.match(/[([]/g) || []).length, close = (url.match(/[)\]]/g) || []).length;
      if (close > open) url = url.slice(0, -1); else break;
    }
    try { new URL(url); } catch { return null; }
    return url;
  }

  function cleanUrl(url) {
    let u;
    try { u = new URL(url); } catch { return { url, removed: [] }; }
    const host = u.hostname.replace(/^(www|m)\./, "");
    const extra = HOST_SPECIFIC[host] || [];
    const removed = [];
    for (const key of [...u.searchParams.keys()]) {
      if (TRACKING.some((r) => r.test(key)) || extra.includes(key)) {
        if (!removed.includes(key)) removed.push(key);
        u.searchParams.delete(key);
      }
    }
    return { url: removed.length ? u.toString() : url, removed };
  }

  if (typeof document === "undefined" || typeof LP === "undefined") return { extractUrl, cleanUrl };

  // ------------------------------------------------------------ quick panel
  const { h, api } = LP;
  let panel, state = { force: false, cleaned: null };
  const $ = (id) => document.getElementById(id);

  function build() {
    panel = h("div", { class: "lp-quick", role: "dialog", "aria-labelledby": "lpq-title", "aria-modal": "false" },
      h("div", { class: "d-flex justify-content-between align-items-center mb-2" },
        h("h2", { class: "h6 mb-0", id: "lpq-title", text: "Shorten this link" }),
        h("button", { class: "btn-close", type: "button", "aria-label": "Close", onclick: close })),
      h("div", { id: "lpq-form" },
        h("input", { class: "form-control", id: "lpq-url", "aria-label": "Destination URL", autocomplete: "off" }),
        h("div", { id: "lpq-clean", class: "mt-2" }),
        h("div", { class: "input-group input-group-sm mt-2" },
          h("span", { class: "input-group-text", text: `${location.host}/` }),
          h("input", { class: "form-control", id: "lpq-alias", placeholder: "alias (optional)", maxlength: 32, "aria-label": "Custom alias" })),
        h("div", { id: "lpq-issues", class: "mt-2", "aria-live": "polite" }),
        h("div", { class: "d-flex gap-2 mt-2" },
          h("button", { class: "btn btn-primary btn-sm", id: "lpq-go", type: "button", onclick: () => shorten() }, "Shorten and copy"),
          h("a", { class: "btn btn-outline-primary btn-sm", id: "lpq-more", href: "/links/new" }, "More options"))),
      h("div", { id: "lpq-done", hidden: true },
        h("p", { class: "lp-short mb-2", id: "lpq-short" }),
        h("div", { class: "d-flex flex-wrap gap-2" },
          h("button", { class: "btn btn-primary btn-sm", id: "lpq-copy", "data-copy": "" }, "Copy"),
          h("a", { class: "btn btn-outline-primary btn-sm", id: "lpq-studio" }, "QR and sharing"),
          h("a", { class: "btn btn-outline-primary btn-sm", id: "lpq-view" }, "Analytics"))));
    document.body.append(panel, h("div", { class: "lp-drop", text: "Drop the link to shorten it" }));
    panel.addEventListener("keydown", (e) => {
      if (e.key === "Escape") close();
      if (e.key === "Enter" && e.target.tagName === "INPUT") shorten();
    });
    $("lpq-url").addEventListener("input", () => { state.force = false; renderClean(); updateMore(); });
    $("lpq-alias").addEventListener("input", () => { state.force = false; });
  }

  function updateMore() { $("lpq-more").href = "/links/new?url=" + encodeURIComponent($("lpq-url").value.trim()); }

  function renderClean() {
    const box = $("lpq-clean");
    box.replaceChildren();
    const c = cleanUrl($("lpq-url").value.trim());
    if (!c.removed.length) return;
    box.append(h("button", { class: "lp-chip-btn", type: "button", onclick: () => {
      $("lpq-url").value = c.url; box.replaceChildren(); updateMore(); } }, `Remove tracking (${c.removed.join(", ")})`));
  }

  function open(url) {
    if (!panel) build();
    $("lpq-form").hidden = false;
    $("lpq-done").hidden = true;
    $("lpq-issues").replaceChildren();
    $("lpq-url").value = url;
    $("lpq-alias").value = "";
    $("lpq-go").textContent = "Shorten and copy";
    state.force = false;
    renderClean();
    updateMore();
    panel.classList.add("open");
    setTimeout(() => $("lpq-go").focus(), 50);
  }

  function close() { panel?.classList.remove("open"); }

  async function shorten() {
    const destination = $("lpq-url").value.trim(), alias = $("lpq-alias").value.trim();
    const issuesBox = $("lpq-issues");
    issuesBox.replaceChildren();
    const v = await api("POST", "/api/links/validate", { destination, alias });
    if (!v.ok) return;
    if (!v.data.ok) { LP.issues(issuesBox, v.data.errors, "error"); return; }
    if (v.data.risk?.level === "high") {
      LP.issues(issuesBox, v.data.warnings, "warning");
      issuesBox.append(h("div", { class: "small", text: "Open it in the full wizard to review and confirm." }));
      $("lpq-more").classList.replace("btn-outline-primary", "btn-primary");
      return;
    }
    const warnings = v.data.warnings.filter((w) => w.code !== "url_scheme_added");
    if (warnings.length && !state.force) {
      LP.issues(issuesBox, warnings, "warning");
      state.force = true;
      $("lpq-go").textContent = "Shorten anyway";
      return;
    }
    $("lpq-go").disabled = true;
    const r = await api("POST", "/api/links", { destination, alias: alias || null });
    $("lpq-go").disabled = false;
    if (!r.ok) { LP.issues(issuesBox, r.data.errors || [], "error"); return; }
    const link = r.data.link;
    $("lpq-form").hidden = true;
    $("lpq-done").hidden = false;
    $("lpq-short").textContent = link.short_url;
    $("lpq-copy").dataset.copy = link.short_url;
    $("lpq-studio").href = `/links/${link.id}/studio`;
    $("lpq-view").href = `/links/${link.id}`;
    panel.classList.remove("lp-success"); void panel.offsetWidth; panel.classList.add("lp-success");
    LP.copy(link.short_url, $("lpq-copy"));
    LP.track("link_created", { method: "quick_paste" });
    document.dispatchEvent(new CustomEvent("lp:link-created", { detail: link }));
  }

  // ---- Ctrl+V anywhere outside a text field
  document.addEventListener("paste", (e) => {
    if (LP.typing(e.target) || LP.typing(document.activeElement)) return;
    const url = extractUrl(e.clipboardData?.getData("text"));
    if (url) { e.preventDefault(); open(url); }
  });
  // ---- drag a link onto the page
  let dragDepth = 0;
  document.addEventListener("dragenter", (e) => {
    if ([...(e.dataTransfer?.types || [])].some((t) => t === "text/uri-list" || t === "text/plain")) {
      dragDepth++; if (!panel) build(); document.body.classList.add("lp-dragging");
    }
  });
  document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("lp-dragging"); } });
  document.addEventListener("dragover", (e) => { if (document.body.classList.contains("lp-dragging")) e.preventDefault(); });
  document.addEventListener("drop", (e) => {
    if (!document.body.classList.contains("lp-dragging")) return;
    e.preventDefault(); dragDepth = 0; document.body.classList.remove("lp-dragging");
    const url = extractUrl(e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain"));
    if (url) open(url); else LP.toast("That doesn't contain a link.", "warning");
  });

  return { extractUrl, cleanUrl, open };
})();
if (typeof module !== "undefined") module.exports = LPPaste;
