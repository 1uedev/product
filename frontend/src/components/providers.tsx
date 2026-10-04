"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";

interface Toast { id: number; text: string; kind: "info" | "ok" | "error" }
const ToastContext = createContext<(text: string, kind?: Toast["kind"]) => void>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000, refetchOnWindowFocus: false,
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  }));
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 9000 : 4500);
  }, []);
  return (
    <QueryClientProvider client={client}>
      <ToastContext.Provider value={push}>
        {children}
        <div className="toasts" role="status" aria-live="polite">
          {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
        </div>
      </ToastContext.Provider>
    </QueryClientProvider>
  );
}
