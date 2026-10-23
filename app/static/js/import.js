(() => {
  const { h, api } = LP;
  const $ = (id) => document.getElementById(id);
  let rows = [];

  function input(row, field, label) {
    return h("input", { class: "form-control form-control-sm", value: row[field] || "", "aria-label": `${label}, row ${row.row}`,
      oninput: (e) => { row[field] = e.target.value; } });
  }

  function render(summary) {
    $("review").hidden = false;
    $("summary").textContent = `${summary.ok + summary.warning} of ${summary.total} rows ready to import`;
    $("commit").disabled = summary.ok + summary.warning === 0;
    $("ack-wrap").hidden = !rows.some((r) => r.risk?.level === "high");
    const body = $("rows");
    body.replaceChildren();
    for (const r of rows) {
      const result = h("td", { style: "min-width:16rem" });
      if (r.status === "ok") result.append(h("div", { class: "lp-issue ok", text: "Ready" }));
      if (r.imported) result.replaceChildren(h("div", { class: "lp-issue ok", text: `Imported as /${r.imported}` }));
      else { LP.issues(result, r.errors, "error"); LP.issues(result, r.warnings, "warning"); }
      body.append(h("tr", {},
        h("td", { class: "text-secondary", text: r.row }),
        h("td", { style: "min-width:16rem" }, input(r, "destination", "Destination")),
        h("td", { style: "min-width:9rem" }, input(r, "alias", "Alias")),
        h("td", { style: "min-width:9rem" }, input(r, "expires_at", "Expiry")),
        h("td", { class: "small", text: r.campaign || "" }),
        result));
    }
  }

  $("upload").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("upload-error").replaceChildren();
    const fd = new FormData();
    fd.append("file", $("file").files[0]);
    fd.append("tz", LP.tz());
    const { ok, data } = await api("POST", "/api/import/preview", fd);
    if (!ok) { $("upload-error").append(h("div", { class: "lp-issue error", text: data.message || "Could not read the file." })); return; }
    rows = data.rows;
    render(data.summary);
    for (const note of data.notes || []) LP.toast(note, "warning");
  });

  $("recheck").addEventListener("click", async () => {
    const pending = rows.filter((r) => !r.imported);
    const { ok, data } = await api("POST", "/api/import/validate", { rows: pending, tz: LP.tz() });
    if (!ok) return;
    rows = rows.filter((r) => r.imported).concat(data.rows).sort((a, b) => a.row - b.row);
    render(data.summary);
  });

  $("commit").addEventListener("click", async () => {
    const pending = rows.filter((r) => !r.imported);
    $("commit").disabled = true;
    const { ok, data } = await api("POST", "/api/import/commit", { rows: pending, tz: LP.tz(), acknowledge_risk: $("ack").checked });
    if (!ok) { $("commit").disabled = false; return; }
    const byRow = Object.fromEntries(data.results.map((r) => [r.row, r]));
    for (const r of rows) {
      const res = byRow[r.row];
      if (!res) continue;
      if (res.ok) r.imported = res.code;
      else { r.errors = res.errors; r.status = "error"; }
    }
    const remaining = rows.filter((r) => !r.imported);
    render({ ok: 0, warning: 0, error: remaining.length, total: rows.length });
    $("summary").textContent = `Imported ${data.imported} links. ${data.failed ? `${data.failed} rows still need fixing.` : ""}`;
    LP.toast(`Imported ${data.imported} links.`);
    LP.track("csv_import", { imported: data.imported, failed: data.failed });
  });
})();
