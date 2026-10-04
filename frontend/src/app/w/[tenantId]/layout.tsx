"use client";

import { useParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { WorkspaceProvider } from "@/components/session";
import { Shell } from "@/components/shell";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  const { tenantId } = useParams<{ tenantId: string }>();
  useEffect(() => { window.localStorage.setItem("de.lastWorkspace", tenantId); }, [tenantId]);
  return (
    <WorkspaceProvider tenantId={tenantId}>
      <Shell>{children}</Shell>
    </WorkspaceProvider>
  );
}
