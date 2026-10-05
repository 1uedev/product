"use client";

import { useQuery } from "@tanstack/react-query";
import { useSearchParams, useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { api, ApiError, loginUrl, type S } from "@/lib/api";
import { t } from "@/lib/i18n";
import { ErrorNotice, Spinner } from "@/components/ui";
import { useMe } from "@/components/session";

function Inner() {
  const token = useSearchParams().get("token") ?? "";
  const router = useRouter();
  const me = useMe();
  const preview = useQuery({ queryKey: ["invite", token], enabled: token.length >= 16, queryFn: () => api<S["InvitationPreview"]>("GET", "/api/v1/auth/invitations/preview", { query: { token } }), retry: false });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (token.length < 16) return <main><div className="card login-card"><ErrorNotice error={new ApiError(400, { title: "Einladungslink unvollständig" }, "")} /></div></main>;
  const unauth = me.error instanceof ApiError && me.error.status === 401;
  async function accept() {
    setBusy(true);
    try {
      const r = await api<S["AcceptOut"]>("POST", "/api/v1/auth/invitations/accept", { body: { token } });
      window.localStorage.setItem("de.lastWorkspace", r.tenant_id);
      router.replace(`/w/${r.tenant_id}`);
    } catch (e) { setError(e); setBusy(false); }
  }
  return (
    <main>
      <div className="card login-card">
        <h1>Einladung</h1>
        {preview.isPending ? <Spinner /> : preview.isError ? <ErrorNotice error={preview.error} /> : (
          <>
            <p>Sie wurden zum Workspace <strong>{preview.data.tenant_name}</strong> als <strong>{t.roles[preview.data.role]}</strong> eingeladen (für {preview.data.email}).</p>
            {!preview.data.usable ? <div className="notice danger" role="alert">Diese Einladung ist abgelaufen, wurde verwendet oder zurückgezogen.</div> : unauth ? (
              <a className="btn primary" href={loginUrl(`/invite?token=${encodeURIComponent(token)}`)}>Anmelden und Einladung annehmen</a>
            ) : (
              <button className="btn primary" disabled={busy || me.isPending} onClick={() => void accept()}>Einladung annehmen</button>
            )}
          </>
        )}
        {error ? <ErrorNotice error={error} /> : null}
      </div>
    </main>
  );
}
export default function InvitePage() { return <Suspense><Inner /></Suspense>; }
