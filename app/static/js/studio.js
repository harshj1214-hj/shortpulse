/* QR & sharing studio: live preview, colour presets with a contrast check,
   module/eye styles, logo upload, caption, downloads and share targets. */
(() => {
  const { h, api } = LP;
  const $ = (id) => document.getElementById(id);
  const PRESETS = [["#16243A", "#FFFFFF", "Ink"], ["#000000", "#FFFFFF", "Classic"], ["#0A6FB8", "#FFFFFF", "Signal blue"],
                   ["#1F5E4B", "#F1FBF7", "Forest"], ["#7A1F3D", "#FFF5F8", "Berry"], ["#3B2A86", "#F5F3FF", "Violet"],
                   ["#7A4300", "#FFF8EC", "Amber"], ["#16243A", "#FFF4DB", "Poster cream"]];
  let opts = {}, defaults = {}, hasLogo = false, minContrast = 4, valid = true;

  const lum = (hex) => {
    const ch = (v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
  };
  const contrast = (a, b) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };
  const isHex = (v) => /^#[0-9a-fA-F]{6}$/.test(v);

  function presets() {
    const box = $("presets");
    box.replaceChildren(...PRESETS.map(([fg, bg, name]) => h("button", {
      type: "button", class: "lp-swatch", title: name, "aria-label": name, "aria-pressed": "false",
      style: `background: linear-gradient(135deg, ${fg} 50%, ${bg} 50%)`,
      onclick: () => { opts.fg = fg; opts.bg = bg; sync(); refresh(); } })));
  }

  function sync() {
    for (const k of ["fg", "bg"]) { $(k).value = opts[k]; $(`${k}-hex`).value = opts[k]; }
    document.querySelectorAll('input[name="style"]').forEach((r) => { r.checked = r.value === opts.style; });
    document.querySelectorAll('input[name="eyes"]').forEach((r) => { r.checked = r.value === opts.eyes; });
    $("ec").value = hasLogo ? "H" : opts.ec;
    $("ec").disabled = hasLogo;
    $("margin").value = opts.margin;
    $("logo_scale").value = opts.logo_scale;
    $("logo_scale").disabled = !hasLogo;
    $("caption").value = opts.caption;
    $("logo-remove").hidden = !hasLogo;
    [...$("presets").children].forEach((b, i) =>
      b.setAttribute("aria-pressed", String(PRESETS[i][0] === opts.fg && PRESETS[i][1] === opts.bg)));
  }

  function query(extra = {}) {
    return new URLSearchParams({ fg: opts.fg, bg: opts.bg, style: opts.style, eyes: opts.eyes, ec: opts.ec,
      margin: opts.margin, caption: opts.caption, logo_scale: opts.logo_scale, ...extra }).toString();
  }

  function checkContrast() {
    const ratio = contrast(opts.fg, opts.bg), darkOnLight = lum(opts.fg) < lum(opts.bg);
    const ok = darkOnLight && ratio >= minContrast;
    $("contrast-text").textContent = `${ratio.toFixed(1)}:1 ${ok ? "· scans well" : "· too low"}`;
    const bar = $("contrast-bar");
    bar.style.width = `${Math.min(100, (ratio / 12) * 100)}%`;
    bar.style.backgroundColor = ok ? "var(--lp-mint)" : "var(--lp-alert)";
    const box = $("design-issues");
    box.replaceChildren();
    if (!darkOnLight) LP.issues(box, [{ message: "Make the code darker than the background. Many phone cameras can't read light-on-dark codes." }], "error");
    else if (ratio < minContrast) LP.issues(box, [{ message: `These colours are too similar to scan reliably. Aim for at least ${minContrast}:1.` }], "error");
    return ok;
  }

  const refresh = LP.debounce(() => {
    valid = checkContrast();
    for (const id of ["save", "dl-png", "dl-svg", "copy-img"]) $(id).classList.toggle("disabled", !valid);
    if (!valid) return;
    const stage = $("stage");
    stage.classList.add("is-loading");
    const img = new Image();
    img.onload = () => { $("preview").src = img.src; stage.classList.remove("is-loading"); };
    img.onerror = () => { stage.classList.remove("is-loading"); LP.toast("Couldn't draw the preview. Check the server log.", "danger"); };
    img.src = `/api/links/${STUDIO.id}/qr.png?${query({ size: 760 })}&v=${Date.now()}`;
    $("dl-png").href = `/api/links/${STUDIO.id}/qr.png?${query({ size: 2048, download: 1 })}`;
    $("dl-svg").href = `/api/links/${STUDIO.id}/qr.svg?${query({ download: 1 })}`;
  }, 140);

  // ---- inputs
  for (const k of ["fg", "bg"]) {
    $(k).addEventListener("input", (e) => { opts[k] = e.target.value.toUpperCase(); $(`${k}-hex`).value = opts[k]; sync(); refresh(); });
    $(`${k}-hex`).addEventListener("input", (e) => {
      let v = e.target.value.trim(); if (v && v[0] !== "#") v = "#" + v;
      if (isHex(v)) { opts[k] = v.toUpperCase(); $(k).value = opts[k]; sync(); refresh(); }
    });
  }
  document.querySelectorAll('input[name="style"], input[name="eyes"]').forEach((r) =>
    r.addEventListener("change", (e) => { opts[e.target.name] = e.target.value; refresh(); }));
  $("ec").addEventListener("change", (e) => { opts.ec = e.target.value; refresh(); });
  $("margin").addEventListener("input", (e) => { opts.margin = Number(e.target.value); refresh(); });
  $("logo_scale").addEventListener("input", (e) => { opts.logo_scale = Number(e.target.value); refresh(); });
  $("caption").addEventListener("input", (e) => { opts.caption = e.target.value; refresh(); });

  $("logo").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const { ok, data } = await api("POST", `/api/links/${STUDIO.id}/qr-logo`, fd);
    e.target.value = "";
    if (!ok) { LP.toast(data.message || "Couldn't use that image.", "danger"); return; }
    hasLogo = true; sync(); refresh(); LP.toast("Logo added.");
  });
  $("logo-remove").addEventListener("click", async () => {
    if ((await api("DELETE", `/api/links/${STUDIO.id}/qr-logo`)).ok) { hasLogo = false; sync(); refresh(); }
  });

  $("save").addEventListener("click", async () => {
    if (!valid) return;
    const { ok, data } = await api("PUT", `/api/links/${STUDIO.id}/qr-design`, opts);
    if (ok) { LP.toast("Design saved. Every QR download for this link now uses it."); LP.track("qr_design_saved", { style: opts.style, logo: hasLogo }); }
    else LP.issues($("design-issues"), (data.errors || []).map((m) => ({ message: m })), "error");
  });
  $("reset").addEventListener("click", () => { opts = { ...defaults }; sync(); refresh(); });

  $("copy-img").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    try {
      // A promise-valued ClipboardItem keeps Safari's user-gesture requirement satisfied.
      const blob = fetch(`/api/links/${STUDIO.id}/qr.png?${query({ size: 1024 })}`).then((r) => r.blob());
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      btn.classList.add("is-copied"); btn.textContent = "Copied ✓";
      setTimeout(() => { btn.classList.remove("is-copied"); btn.textContent = "Copy image"; }, 1600);
    } catch {
      LP.toast("This browser can't copy images. Use Download instead.", "warning");
    }
  });

  // ---- share targets
  const enc = encodeURIComponent, url = STUDIO.url, title = STUDIO.title;
  const targets = [
    ["WhatsApp", `https://wa.me/?text=${enc(`${title} ${url}`)}`],
    ["Telegram", `https://t.me/share/url?url=${enc(url)}&text=${enc(title)}`],
    ["X", `https://twitter.com/intent/tweet?url=${enc(url)}&text=${enc(title)}`],
    ["LinkedIn", `https://www.linkedin.com/sharing/share-offsite/?url=${enc(url)}`],
    ["Email", `mailto:?subject=${enc(title)}&body=${enc(url)}`],
  ];
  const share = $("share");
  if (navigator.share) {
    share.append(h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
      try {
        const blob = await (await fetch(`/api/links/${STUDIO.id}/qr.png?${query({ size: 1024 })}`)).blob();
        const file = new File([blob], "qr-code.png", { type: "image/png" });
        const payload = navigator.canShare?.({ files: [file] }) ? { files: [file], title, text: url } : { title, url };
        await navigator.share(payload);
      } catch (err) { if (err?.name !== "AbortError") LP.toast("Sharing was cancelled or isn't available.", "warning"); }
    } }, "Share…"));
  }
  for (const [name, href] of targets) {
    share.append(h("a", { class: "btn btn-outline-primary", href, target: "_blank", rel: "noopener noreferrer",
                          onclick: () => LP.track("share", { method: name.toLowerCase() }) }, name));
  }
  $("dl-png").addEventListener("click", () => LP.track("qr_download", { format: "png" }));
  $("dl-svg").addEventListener("click", () => LP.track("qr_download", { format: "svg" }));

  const stage = $("stage");
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    stage.addEventListener("pointermove", (e) => {
      const r = stage.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      $("preview").style.transform = `perspective(900px) rotateY(${x * 10}deg) rotateX(${-y * 10}deg)`;
    });
    stage.addEventListener("pointerleave", () => { $("preview").style.transform = ""; });
  }

  (async () => {
    const { ok, data } = await api("GET", `/api/links/${STUDIO.id}/qr-design`);
    if (!ok) return;
    opts = { ...data.options }; defaults = { ...data.defaults }; hasLogo = data.has_logo; minContrast = data.min_contrast;
    presets(); sync(); refresh();
  })();
})();
