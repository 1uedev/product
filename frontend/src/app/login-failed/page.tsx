"use client";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { loginUrl } from "@/lib/api";

const REASONS: Record<string, string> = {
  state_invalid: "Die Anmeldeanfrage ist abgelaufen oder wurde bereits verwendet.",
  token_invalid: "Die Anmeldeantwort konnte nicht geprüft werden.",
  idp_error: "Der Anmeldedienst hat die Anmeldung abgelehnt.",
};

function Inner() {
  const reason = useSearchParams().get("reason") ?? "";
  return (
    <main>
      <div className="card login-card" role="alert">
        <h1>Anmeldung fehlgeschlagen</h1>
        <p>{REASONS[reason] ?? "Die Anmeldung war nicht erfolgreich."}</p>
        <a className="btn primary" href={loginUrl("/")}>Erneut anmelden</a>
      </div>
    </main>
  );
}
export default function LoginFailed() { return <Suspense><Inner /></Suspense>; }
