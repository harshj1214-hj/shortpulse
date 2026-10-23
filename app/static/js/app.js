/* Shared helpers. All user-controlled text (titles, URLs, aliases) is inserted
   with textContent via LP.h(), never innerHTML, so a malicious title can't run script. */
const LP = (() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || "";
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  function toast(message, kind = "success") {
    const box = h("div", { class: `toast align-items-center text-bg-${kind} border-0`, role: "status" },
      h("div", { class: "d-flex" },
        h("div", { class: "toast-body", text: message }),
        h("button", { type: "button", class: "btn-close btn-close-white me-2 m-auto", "data-bs-dismiss": "toast", "aria-label": "Close" })));
    document.getElementById("toasts").append(box);
    const t = new bootstrap.Toast(box, { delay: kind === "danger" ? 6000 : 3000 });
    box.addEventListener("hidden.bs.toast", () => box.remove());
    t.show();
  }

  async function api(method, url, body, { quiet = false } = {}) {
    const init = { method, headers: { "X-CSRF-Token": csrf, Accept: "application/json" } };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, init); }
    catch (e) { toast("Can't reach the server. Check your connection and try again.", "danger"); throw e; }
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (res.status === 401) window.location = "/login?next=" + encodeURIComponent(location.pathname);
    if (!res.ok && !quiet && res.status !== 422) toast(data?.message || `Request failed (${res.status}).`, "danger");
    return { ok: res.ok, status: res.status, data: data || {} };
  }

  const nf = new Intl.NumberFormat();
  const fmt = {
    num: (n) => nf.format(n || 0),
    date: (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "",
    day: (ts) => new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" }),
    hour: (ts) => new Date(ts * 1000).toLocaleTimeString(undefined, { hour: "numeric" }),
    secs: (s) => s === null || s === undefined ? "" : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`,
    rel(ts) {
      if (!ts) return "";
      const d = ts - Date.now() / 1000, a = Math.abs(d);
      const [v, u] = a < 3600 ? [a / 60, "minute"] : a < 86400 ? [a / 3600, "hour"] : [a / 86400, "day"];
      return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(Math.round(d < 0 ? -v : v), u);
    },
  };

  const STATUS_LABELS = { active: "Active", paused: "Paused", disabled: "Disabled", expired: "Expired", limit_reached: "Limit reached" };
  const status = (s) => h("span", { class: `lp-status ${s}`, text: STATUS_LABELS[s] || s });

  /* Google Analytics event, if GA is enabled on this page. Never pass URLs or personal data. */
  function track(name, params = {}) {
    try { if (typeof window.gtag === "function") window.gtag("event", name, params); } catch { /* analytics must never break the app */ }
  }

  function legacyCopy(text) {
    const ta = h("textarea", { style: "position:fixed;opacity:0", readonly: true });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }

  /* Copy text; if a button is given it briefly turns into a "Copied" confirmation. */
  async function copy(text, button) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch { ok = legacyCopy(text); }
    if (ok) track("copy_link");
    if (button) {
      const label = button.dataset.label || button.textContent;
      button.dataset.label = label;
      button.classList.remove("is-copied", "is-copy-failed");
      void button.offsetWidth; // restart the animation
      button.classList.add(ok ? "is-copied" : "is-copy-failed");
      button.textContent = ok ? "Copied ✓" : "Press Ctrl+C";
      clearTimeout(button._lpTimer);
      button._lpTimer = setTimeout(() => { button.classList.remove("is-copied", "is-copy-failed"); button.textContent = label; }, 1600);
    } else {
      toast(ok ? "Copied to clipboard." : "Couldn't copy automatically. Select the link and press Ctrl+C.", ok ? "success" : "warning");
    }
    return ok;
  }

  function issues(container, list, kind) {
    for (const i of list || []) container.append(h("div", { class: `lp-issue ${kind}`, text: i.message }));
  }

  const tz = () => -new Date().getTimezoneOffset();
  const epochFromInput = (v) => (v ? Math.floor(new Date(v).getTime() / 1000) : null);
  function inputFromEpoch(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function bars(container, rows, emptyText = "No data yet.") {
    container.replaceChildren();
    if (!rows || !rows.length) { container.append(h("p", { class: "text-secondary small mb-0", text: emptyText })); return; }
    const max = Math.max(...rows.map((r) => r.n), 1);
    const ul = h("ul", { class: "lp-bars" });
    rows.forEach((r, i) => ul.append(h("li", {}, h("span", { text: r.label }), h("span", { class: "n", text: fmt.num(r.n) }),
      h("span", { class: "track" }, h("span", { class: "fill", style: `width:${(r.n / max) * 100}%;animation-delay:${i * 40}ms` })))));
    container.append(ul);
  }

  /* Animate a number from its current value to `to`. */
  function countUp(el, to, format = fmt.num) {
    const from = Number(el.dataset.value || 0);
    el.dataset.value = to;
    if (reduceMotion || from === to) { el.textContent = format(to); return; }
    const start = performance.now(), dur = 700;
    const step = (now) => {
      const t = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - t, 3);
      el.textContent = format(Math.round(from + (to - from) * e));
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function debounce(fn, ms) { let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); }; }
  const isDark = () => document.documentElement.getAttribute("data-bs-theme") === "dark";
  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));

  // ---- theme toggle
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-bs-theme", next);
    try { localStorage.setItem("lp-theme", next); } catch { /* private mode */ }
    document.dispatchEvent(new CustomEvent("lp:theme", { detail: next }));
  });

  // ---- any element with data-copy copies its value
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy]");
    if (!b || !b.dataset.copy) return;
    e.preventDefault();
    copy(b.dataset.copy, b);
  });

  // ---- keyboard shortcuts: N = new link, / = search
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey || typing(document.activeElement)) return;
    if (e.key === "n" && document.querySelector(".lp-nav .navbar-nav")) { location.href = "/links/new"; }
    else if (e.key === "/") { const s = document.querySelector("[data-search]"); if (s) { e.preventDefault(); s.focus(); } }
  });

  // ---- button ripple
  document.addEventListener("pointerdown", (e) => {
    const b = e.target.closest(".btn");
    if (!b || reduceMotion) return;
    const r = b.getBoundingClientRect(), size = Math.max(r.width, r.height) * 2;
    const dot = h("span", { class: "lp-ripple", style: `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px` });
    b.append(dot);
    setTimeout(() => dot.remove(), 650);
  });

  // ---- sections slide in as they scroll into view
  if ("IntersectionObserver" in window && !reduceMotion) {
    document.documentElement.classList.add("lp-js");
    const main = document.querySelector("main");
    const io = new IntersectionObserver((entries) => {
      for (const en of entries) if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
    }, { rootMargin: "0px 0px -40px 0px" });
    const watch = () => document.querySelectorAll(".lp-panel, .lp-stats, .lp-facts > div").forEach((el) => {
      if (el.parentElement !== main && !el.classList.contains("lp-reveal")) { el.classList.add("lp-reveal"); io.observe(el); }
    });
    watch();
    document.addEventListener("DOMContentLoaded", watch);
  }

  if (window.Chart) {
    Chart.defaults.font.family = '"Public Sans", system-ui, sans-serif';
    Chart.defaults.color = "#5B6B7F";
  }

  return { h, api, toast, fmt, status, copy, issues, tz, epochFromInput, inputFromEpoch, bars, countUp, debounce, isDark, typing, csrf, track };
})();
