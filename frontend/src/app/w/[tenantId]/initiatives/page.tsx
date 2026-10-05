"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, type Page, type S } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Field, Pager, StatusBadge } from "@/components/ui";
import { useWorkspace } from "@/components/session";

export default function InitiativesPage() {
  const { path, ws, tenantId } = useWorkspace();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ["initiatives", status, offset], queryFn: () => api<Page<S["InitiativeOut"]>>("GET", path("/initiatives"), { query: { status, limit: 25, offset } }) });
  return (
    <>
      <div className="page-head"><div><h1>Initiativen</h1><p className="muted">Eine Initiative beantwortet ein bestätigtes Problem. Initiativen legen Sie auf der Seite des jeweiligen Problems an.</p></div></div>
      <div style={{ maxWidth: 260 }}><Field id="is" label="Status"><select id="is" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="">alle</option>{Object.entries(t.status.initiative).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field></div>
      <Async q={q} isEmpty={(d) => d.items.length === 0} empty={<p>Noch keine Initiativen. Öffnen Sie ein Problem und wählen Sie „Initiative anlegen“.</p>}>
        {(d) => (
          <div className="card"><div className="table-wrap"><table data-testid="initiative-table"><thead><tr><th>Initiative</th><th>Problem</th><th>Status</th><th className="num">Annahmen (offen)</th><th>Entscheidung</th><th>Geändert</th></tr></thead><tbody>
            {d.items.map((i) => (
              <tr key={i.id}><td><Link href={`/w/${tenantId}/initiatives/${i.id}`}>{i.title}</Link></td><td><Link href={`/w/${tenantId}/problems/${i.problem_id}`}>{i.problem_title}</Link></td><td><StatusBadge value={i.status} map={t.status.initiative} /></td>
                <td className="num">{i.assumption_count} ({i.open_assumptions})</td><td>{i.latest_decision ? <><StatusBadge value={String(i.latest_decision.state)} map={t.status.decision} /> Revision {String(i.latest_decision.revision)}</> : "–"}</td><td>{fmtDate(i.updated_at, ws.timezone)}</td></tr>))}
          </tbody></table></div><Pager total={d.total} limit={d.limit} offset={d.offset} onChange={setOffset} /></div>
        )}
      </Async>
    </>
  );
}
