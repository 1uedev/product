"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, loginUrl } from "@/lib/api";
import { t } from "@/lib/i18n";
import { Spinner } from "@/components/ui";
import { useMe } from "@/components/session";
import Link from "next/link";

interface PublicConfig { demo_mode: boolean; ai: { provider: string; demo: boolean }; demo_accounts: { email: string; name: string; role: string }[]; demo_password: string | null }

export default function Home() {
  const me = useMe();
  const router = useRouter();
  const cfg = useQuery({ queryKey: ["public-config"], queryFn: () => api<PublicConfig>("GET", "/api/v1/public/config") });
  const signedIn = !!me.data;
  useEffect(() => {
    if (!me.data) return;
    const stored = typeof window !== "undefined" ? window.localStorage.getItem("de.lastWorkspace") : null;
    const target = me.data.workspaces.find((w) => w.tenant_id === stored) ?? (me.data.workspaces.length === 1 ? me.data.workspaces[0] : undefined);
    if (target) router.replace(`/w/${target.tenant_id}`);
  }, [me.data, router]);

  if (me.isPending) return <main><div className="empty"><Spinner /></div></main>;
  if (signedIn) {
    const workspaces = me.data.workspaces;
    return (
      <main>
        <div className="card login-card">
          <h1>Workspace wählen</h1>
          {workspaces.length === 0 ? (
            <div className="notice warn" role="status">Ihr Konto gehört noch zu keinem Workspace. Bitten Sie einen Admin um eine Einladung und öffnen Sie den Einladungslink.</div>
          ) : (
            <ul>{workspaces.map((w) => <li key={w.tenant_id}><Link href={`/w/${w.tenant_id}`} onClick={() => window.localStorage.setItem("de.lastWorkspace", w.tenant_id)}>{w.name}</Link> ({t.roles[w.role]})</li>)}</ul>
          )}
          <button className="btn" onClick={async () => { const o = await api<{ logout_url: string | null }>("POST", "/api/v1/auth/logout"); window.location.assign(o.logout_url ?? "/"); }}>{t.common.signOut}</button>
        </div>
      </main>
    );
  }
  const unauth = me.error instanceof ApiError && me.error.status === 401;
  return (
    <main>
      <div className="card login-card">
        <h1>decision-evidence</h1>
        <p>Kundenfeedback und wirtschaftlicher Kontext werden zu überprüfbaren Entscheidungsvorlagen. Die KI schlägt vor, das Team entscheidet.</p>
        {!unauth ? <div className="notice danger" role="alert">Der Anmeldestatus konnte nicht geprüft werden.</div> : null}
        <a className="btn primary" href={loginUrl("/")} data-testid="login-button">{t.common.signIn}</a>
        {cfg.data?.demo_mode ? (
          <div className="notice warn" style={{ marginTop: "1rem" }}>
            <strong>Demo-Modus</strong>
            <p className="small">Synthetische Daten, bekannte Zugänge. Passwort für alle: <code>{cfg.data.demo_password}</code>{cfg.data.ai.demo ? " · KI: deterministischer Demo-Adapter" : ""}</p>
            <ul className="small">{cfg.data.demo_accounts.map((a) => <li key={a.email}><code>{a.email}</code> ({a.role})</li>)}</ul>
          </div>
        ) : null}
      </div>
    </main>
  );
}
