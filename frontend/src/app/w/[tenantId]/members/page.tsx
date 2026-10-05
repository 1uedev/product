"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type S } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { t } from "@/lib/i18n";
import { Async, Badge, ErrorNotice, Field, Modal } from "@/components/ui";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

type Member = S["MemberOut"];
const ROLES = ["owner", "admin", "editor", "viewer"] as const;

export default function MembersPage() {
  const { path, can, ws } = useWorkspace();
  const qc = useQueryClient();
  const toast = useToast();
  const members = useQuery({ queryKey: ["members"], queryFn: () => api<Member[]>("GET", path("/members")) });
  const invites = useQuery({ queryKey: ["invitations"], enabled: can("admin"), queryFn: () => api<{ id: string; email: string; role: string; state: string; expires_at: string }[]>("GET", path("/invitations")) });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ email: "", role: "viewer" });
  const [created, setCreated] = useState<S["InvitationCreated"] | null>(null);
  const update = useMutation({
    mutationFn: (v: { id: string; body: Record<string, unknown> }) => api<Member[]>("PATCH", path(`/members/${v.id}`), { body: v.body }),
    onSuccess: (d) => { qc.setQueryData(["members"], d); toast("Mitglied aktualisiert.", "ok"); }, onError: (e) => toast(e instanceof Error ? e.message : "Fehler", "error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<Member[]>("DELETE", path(`/members/${id}`)),
    onSuccess: (d) => { qc.setQueryData(["members"], d); toast("Mitglied entfernt.", "ok"); }, onError: (e) => toast(e instanceof Error ? e.message : "Fehler", "error"),
  });
  const invite = useMutation({
    mutationFn: () => api<S["InvitationCreated"]>("POST", path("/invitations"), { body: f }),
    onSuccess: (d) => { setCreated(d); void qc.invalidateQueries({ queryKey: ["invitations"] }); },
  });
  const revoke = useMutation({ mutationFn: (id: string) => api("DELETE", path(`/invitations/${id}`)), onSuccess: () => void qc.invalidateQueries({ queryKey: ["invitations"] }) });
  const link = created ? `${window.location.origin}${created.invite_path}` : "";
  return (
    <>
      <div className="page-head"><div><h1>Mitglieder</h1><p className="muted">Rollen stammen aus den Mitgliedschaften dieses Workspaces. Der letzte Owner kann nicht entfernt werden.</p></div>
        {can("admin") ? <div className="actions"><button className="btn primary" onClick={() => { setCreated(null); setOpen(true); }} data-testid="invite-open">Mitglied einladen</button></div> : null}</div>
      <Async q={members}>
        {(d) => (
          <div className="card"><div className="table-wrap"><table data-testid="member-table"><thead><tr><th>Name</th>{can("admin") ? <th>E-Mail</th> : null}<th>Rolle</th><th>Status</th>{can("admin") ? <th>Letzte Anmeldung</th> : null}{can("admin") ? <th /> : null}</tr></thead><tbody>
            {d.map((m) => (
              <tr key={m.user_id}><td>{m.display_name}</td>{can("admin") ? <td>{m.email}</td> : null}
                <td>{can("admin") ? <select aria-label={`Rolle von ${m.display_name}`} value={m.role} style={{ width: "auto" }} disabled={m.role === "owner" && !can("owner")} onChange={(e) => update.mutate({ id: m.user_id, body: { role: e.target.value } })}>{ROLES.filter((r) => r !== "owner" || can("owner") || m.role === "owner").map((r) => <option key={r} value={r}>{t.roles[r]}</option>)}</select> : <Badge>{t.roles[m.role]}</Badge>}</td>
                <td><Badge kind={m.status === "active" ? "ok" : "warn"}>{m.status === "active" ? "aktiv" : "deaktiviert"}</Badge></td>
                {can("admin") ? <td>{fmtDate(m.last_login_at, ws.timezone, true)}</td> : null}
                {can("admin") ? <td><button className="btn small" onClick={() => update.mutate({ id: m.user_id, body: { status: m.status === "active" ? "disabled" : "active" } })}>{m.status === "active" ? "Deaktivieren" : "Aktivieren"}</button> <button className="btn small danger" onClick={() => { if (window.confirm(`${m.display_name} aus dem Workspace entfernen?`)) remove.mutate(m.user_id); }}>Entfernen</button></td> : null}</tr>))}
          </tbody></table></div></div>
        )}
      </Async>
      {can("admin") ? (
        <section className="card" style={{ marginTop: "1rem" }} aria-labelledby="inv"><h2 id="inv">Einladungen</h2>
          <Async q={invites} isEmpty={(d) => d.length === 0} empty="Keine Einladungen.">{(d) => <div className="table-wrap"><table><thead><tr><th>E-Mail</th><th>Rolle</th><th>Status</th><th>Gültig bis</th><th /></tr></thead><tbody>{d.map((i) => <tr key={i.id}><td>{i.email}</td><td>{t.roles[i.role]}</td><td>{{ open: "offen", accepted: "angenommen", expired: "abgelaufen", revoked: "zurückgezogen" }[i.state] ?? i.state}</td><td>{fmtDate(i.expires_at, ws.timezone, true)}</td><td>{i.state === "open" ? <button className="btn small" onClick={() => revoke.mutate(i.id)}>Zurückziehen</button> : null}</td></tr>)}</tbody></table></div>}</Async>
        </section>
      ) : null}
      <Modal open={open} onClose={() => setOpen(false)} title="Mitglied einladen" footer={!created ? <button className="btn primary" disabled={!f.email || invite.isPending} onClick={() => invite.mutate()} data-testid="invite-create">Einladung erstellen</button> : null}>
        {created ? (
          <div className="notice ok" role="status"><p>Einladung für <strong>{created.email}</strong> ({t.roles[created.role]}). Der Link ist 7 Tage gültig, funktioniert einmal und ist an diese E-Mail-Adresse gebunden. Er wird nur jetzt angezeigt. Es wird keine E-Mail versendet.</p>
            <Field id="inv-link" label="Einladungslink"><input id="inv-link" readOnly value={link} onFocus={(e) => e.currentTarget.select()} data-testid="invite-link" /></Field>
            <button className="btn small" onClick={() => void navigator.clipboard?.writeText(link)}>Link kopieren</button></div>
        ) : (
          <>
            <Field id="i-mail" label="E-Mail-Adresse"><input id="i-mail" type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} data-testid="invite-email" /></Field>
            <Field id="i-role" label="Rolle"><select id="i-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} data-testid="invite-role">{ROLES.filter((r) => r !== "owner" || can("owner")).map((r) => <option key={r} value={r}>{t.roles[r]}</option>)}</select></Field>
            <p className="small muted">Es gibt keine automatische Zuordnung über E-Mail-Domains. Die eingeladene Person meldet sich mit genau dieser Adresse an.</p>
            {invite.isError ? <ErrorNotice error={invite.error} /> : null}
          </>
        )}
      </Modal>
    </>
  );
}
