/* Two-step link wizard.
   Step 1 validates as you type (server-side, debounced) and pulls the link out
   of pasted text. Step 2 sets rules and shows every warning before publishing.
   The server re-checks everything on publish. */
(() => {
  const { h, api } = LP;
  const $ = (id) => document.getElementById(id);
  const base = `${location.origin}/`;
  $("base").textContent = base;
  let last = null;

  async function loadCampaigns() {
    const { ok, data } = await api("GET", "/api/campaigns", undefined, { quiet: true });
    if (ok) for (const c of data.campaigns) $("campaign").append(h("option", { value: c.id, text: c.name }));
  }

  function render(container, result) {
    container.replaceChildren();
    LP.issues(container, result.errors, "error");
    LP.issues(container, result.warnings, "warning");
    if (!result.errors.length && result.destination) {
      const code = result.alias ? `Alias /${result.alias} is available.` : "A random code will be generated.";
      const app = result.app ? ` Phones will open it in the ${result.app.label} app.` : "";
      container.append(h("div", { class: "lp-issue ok", text: result.warnings.length
        ? `${code}${app} You can continue; you'll confirm the warnings in the next step.` : `${code}${app} Ready to continue.` }));
    }
  }

  function renderClean() {
    const box = $("clean-hint");
    box.replaceChildren();
    const c = LPPaste.cleanUrl($("destination").value.trim());
    if (c.removed.length) {
      box.append(h("button", { class: "lp-chip-btn", type: "button", onclick: () => {
        $("destination").value = c.url; box.replaceChildren(); validate(); } }, `Remove tracking parameters (${c.removed.join(", ")})`));
    }
  }

  const validate = LP.debounce(async () => {
    const destination = $("destination").value.trim(), alias = $("alias").value.trim();
    renderClean();
    if (!destination) { $("issues1").replaceChildren(); $("next").disabled = true; return; }
    const { ok, data } = await api("POST", "/api/links/validate", { destination, alias });
    if (!ok) return;
    if (destination !== $("destination").value.trim() || alias !== $("alias").value.trim()) return; // stale answer
    last = data;
    render($("issues1"), data);
    $("next").disabled = !data.ok;
  }, 300);

  $("destination").addEventListener("input", () => { $("next").disabled = true; validate(); });
  $("alias").addEventListener("input", () => { $("next").disabled = true; validate(); });
  $("destination").addEventListener("paste", (e) => {
    const text = e.clipboardData?.getData("text") || "";
    const url = LPPaste.extractUrl(text);
    if (url && url !== text.trim()) { e.preventDefault(); $("destination").value = url; $("next").disabled = true; validate(); }
  });
  $("paste").addEventListener("click", async () => {
    try {
      const url = LPPaste.extractUrl(await navigator.clipboard.readText());
      if (!url) { LP.toast("There's no link on your clipboard.", "warning"); return; }
      $("destination").value = url; $("next").disabled = true; validate();
    } catch {
      $("destination").focus();
      LP.toast("Your browser didn't allow reading the clipboard. Press Ctrl+V in the box instead.", "warning");
    }
  });

  function show(step) {
    $("step1").hidden = step !== 1;
    $("step2").hidden = step !== 2;
    const panel = $(step === 1 ? "step1" : "step2");
    panel.classList.remove("lp-step-enter"); void panel.offsetWidth; panel.classList.add("lp-step-enter");
    $("s1").className = step === 1 ? "current" : "done";
    $("s2").className = step === 2 ? "current" : "";
    $("s1").toggleAttribute("aria-current", step === 1);
    $("s2").toggleAttribute("aria-current", step === 2);
  }

  $("next").addEventListener("click", () => {
    if (!last?.ok) return;
    $("preview").textContent = base + (last.alias || "•••••••");
    $("dest-review").textContent = last.destination;
    $("issues2").replaceChildren();
    LP.issues($("issues2"), last.warnings, "warning");
    $("ack-wrap").hidden = last.risk?.level !== "high";
    $("ack").checked = false;
    $("app-wrap").hidden = !last.app;
    if (last.app) $("app-label").textContent = `Open in the ${last.app.label} app on phones`;
    show(2);
    $("title").focus();
  });
  $("back").addEventListener("click", () => show(1));

  $("publish").addEventListener("click", async () => {
    const payload = {
      destination: $("destination").value.trim(), alias: $("alias").value.trim() || null,
      title: $("title").value.trim(), campaign_id: $("campaign").value || null, tags: $("tags").value,
      expires_at: LP.epochFromInput($("expires").value), max_clicks: $("max_clicks").value || null,
      password: $("password").value || null, acknowledge_risk: $("ack").checked,
      open_in_app: $("open_in_app").checked, track_engagement: $("track_engagement").checked,
      landing: $("landing_enabled").checked
        ? { enabled: true, heading: $("landing_heading").value, message: $("landing_message").value } : null,
    };
    $("publish").disabled = true;
    const { ok, status, data } = await api("POST", "/api/links", payload);
    $("publish").disabled = false;
    if (status === 422) {
      const backToStep1 = data.errors.some((e) => (e.field === "destination" && e.code !== "risk_unacknowledged") || e.field === "alias");
      if (backToStep1) { last = { ...last, ...data, ok: false }; render($("issues1"), { ...data, destination: null }); show(1); return; }
      $("issues2").replaceChildren();
      LP.issues($("issues2"), data.errors, "error");
      LP.issues($("issues2"), data.warnings, "warning");
      if (data.risk?.level === "high") $("ack-wrap").hidden = false;
      return;
    }
    if (!ok) return;
    const link = data.link;
    $("step2").hidden = true;
    $("s2").className = "done";
    const done = $("done");
    done.hidden = false;
    done.classList.add("lp-step-enter", "lp-success");
    $("short").textContent = link.short_url;
    $("copy").dataset.copy = link.short_url;
    $("qr").onerror = () => { $("qr").hidden = true; };
    $("qr").src = `/api/links/${link.id}/qr.png?size=360`;
    $("studio").href = `/links/${link.id}/studio`;
    $("details").href = `/links/${link.id}`;
    LP.copy(link.short_url, $("copy")); // instant copy
    LP.track("link_created", { method: "wizard", landing_page: Boolean(payload.landing),
                               click_limit: Boolean(payload.max_clicks), password: Boolean(payload.password) });
  });

  $("landing_enabled").addEventListener("change", (e) => {
    $("landing-fields").classList.toggle("open", e.target.checked);
    if (e.target.checked) setTimeout(() => $("landing_heading").focus(), 200);
  });

  const pre = new URLSearchParams(location.search).get("url");
  if (pre) { $("destination").value = pre; validate(); }
  loadCampaigns();
})();
