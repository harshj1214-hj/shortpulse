/* ShortPulse engagement beacon.
   1) On ShortPulse landing pages it is included automatically (data-cid attribute).
   2) On YOUR OWN website, add:
        <script src="https://YOUR-SHORTPULSE-HOST/static/lp-beacon.js" defer></script>
      It does nothing unless the page was opened through a ShortPulse link with
      "measure time on page and scroll depth" on (the URL then has ?lp_cid=...).
   It reports only: that id, visible time, deepest scroll position, which tracked
   button was pressed, and a heartbeat every 10 s for "reading now". No cookies. */
(function () {
  var script = document.currentScript;
  if (!script || !window.URLSearchParams) return;
  var params = new URLSearchParams(location.search);
  var cid = script.getAttribute("data-cid") || params.get("lp_cid");
  if (!cid || !/^[A-Za-z0-9_-]{16}$/.test(cid)) return;
  var endpoint = new URL("/api/engage", script.src).href;

  if (params.has("lp_cid") && history.replaceState) { // tidy the address bar
    params.delete("lp_cid");
    var q = params.toString();
    history.replaceState(history.state, "", location.pathname + (q ? "?" + q : "") + location.hash);
  }

  var visibleMs = 0, since = document.visibilityState === "visible" ? Date.now() : null, maxScroll = 0, out = null;
  function depth() {
    var d = document.documentElement, body = document.body;
    var scrollable = Math.max(d.scrollHeight, body ? body.scrollHeight : 0) - window.innerHeight;
    if (scrollable <= 0) return 100;
    return Math.max(0, Math.min(100, Math.round((window.scrollY / scrollable) * 100)));
  }
  function onScroll() { var p = depth(); if (p > maxScroll) maxScroll = p; }
  function send() {
    var ms = visibleMs + (since ? Date.now() - since : 0);
    var body = JSON.stringify({ cid: cid, ms: Math.min(ms, 14400000), scroll: maxScroll, out: out });
    if (navigator.sendBeacon) navigator.sendBeacon(endpoint, body);
    else fetch(endpoint, { method: "POST", body: body, keepalive: true, mode: "no-cors" });
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      if (since) { visibleMs += Date.now() - since; since = null; }
      send();
    } else { since = Date.now(); send(); }
  });
  window.addEventListener("pagehide", send);
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("load", onScroll);
  document.addEventListener("click", function (e) { // tracked buttons on landing pages
    var a = e.target && e.target.closest ? e.target.closest("[data-lp-out]") : null;
    if (a) { out = a.getAttribute("data-lp-out"); onScroll(); send(); }
  }, true);
  setTimeout(function () { onScroll(); send(); }, 1500);                       // shows up as "reading now" quickly
  setInterval(function () { if (document.visibilityState === "visible") send(); }, 10000); // heartbeat
})();
