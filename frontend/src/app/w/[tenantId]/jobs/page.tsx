"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Page } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Field, Pager, StatusBadge } from "@/components/ui";
import { JobProgress, type Job } from "@/components/jobs";
import { useWorkspace } from "@/components/session";

export default function JobsPage() {
  const { path, ws, tenantId } = useWorkspace();
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["jobs", tenantId, status, offset], queryFn: () => api<Page<Job>>("GET", path("/jobs"), { query: { status, limit: 20, offset } }),
    refetchInterval: (query) => (query.state.data?.items.some((j) => j.status === "queued" || j.status === "running") ? 2000 : false),
  });
  return (
    <>
      <div className="page-head"><div><h1>Aufgaben</h1><p className="muted">Importe, Analysen und KI-Entwürfe laufen im Hintergrund. Fehlgeschlagene Aufgaben können Sie wiederholen. Ergebnisse werden nie doppelt gespeichert.</p></div></div>
      <div style={{ maxWidth: 260 }}><Field id="js" label="Status"><select id="js" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="">alle</option>{Object.entries(t.status.job).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field></div>
      <Async q={q} isEmpty={(d) => d.items.length === 0} empty="Keine Aufgaben.">
        {(d) => (
          <div className="card"><div className="table-wrap"><table data-testid="job-table"><thead><tr><th>Art</th><th>Status</th><th className="num">Fortschritt</th><th className="num">Versuche</th><th>Erstellt</th><th>Fehler</th><th /></tr></thead><tbody>
            {d.items.map((j) => (
              <tr key={j.id}><td>{t.jobKind[j.kind] ?? j.kind}</td><td><StatusBadge value={j.status} map={t.status.job} /></td><td className="num">{j.progress} %</td><td className="num">{j.attempts}/{j.max_attempts}</td><td>{fmtDate(j.created_at, ws.timezone, true)}</td><td className="small">{j.error_message ?? ""}{j.error_code ? ` (${j.error_code})` : ""}</td>
                <td><button className="btn small" onClick={() => setOpen(open === j.id ? null : j.id)}>Details</button></td></tr>))}
          </tbody></table></div>
            {open ? <div style={{ marginTop: ".75rem" }}><JobProgress jobId={open} onDone={() => void qc.invalidateQueries({ queryKey: ["jobs"] })} />{(() => { const j = d.items.find((x) => x.id === open); return j?.result ? <pre className="small" style={{ overflow: "auto" }}>{JSON.stringify(j.result, null, 2)}</pre> : null; })()}</div> : null}
            <Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>
        )}
      </Async>
    </>
  );
}
