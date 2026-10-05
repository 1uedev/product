"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, type S } from "@/lib/api";
import { t } from "@/lib/i18n";
import { StatusBadge } from "@/components/ui";
import { useWorkspace } from "@/components/session";
import { useToast } from "@/components/providers";

export type Job = S["JobOut"];

/** Polls a job until it reaches a final state and reports completion once. */
export function useJob(jobId: string | null | undefined, onDone?: (job: Job) => void) {
  const { path } = useWorkspace();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>("GET", path(`/jobs/${jobId}`)),
    enabled: !!jobId,
    refetchInterval: (query) => { const s = query.state.data?.status; return s === "succeeded" || s === "failed" ? false : 1500; },
    staleTime: 0,
  });
  const status = q.data?.status;
  useEffect(() => {
    if (q.data && (status === "succeeded" || status === "failed")) {
      void qc.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "job" && query.queryKey[0] !== "me" });
      onDone?.(q.data);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);
  return q;
}

export function JobProgress({ jobId, onDone, label }: { jobId: string; onDone?: (job: Job) => void; label?: string }) {
  const { path, can } = useWorkspace();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useJob(jobId, onDone);
  const job = q.data;
  if (!job) return <div className="notice" role="status">Aufgabe wird geladen …</div>;
  const failed = job.status === "failed";
  const retryable = failed && can("editor");
  return (
    <div className={`notice ${failed ? "danger" : job.status === "succeeded" ? "ok" : ""}`} role={failed ? "alert" : "status"} aria-live="polite" data-testid="job-progress" data-status={job.status}>
      <div className="row">
        <strong>{label ?? t.jobKind[job.kind] ?? job.kind}</strong>
        <StatusBadge value={job.status} map={t.status.job} />
        {job.attempts > 1 ? <span className="small muted">Versuch {job.attempts} von {job.max_attempts}</span> : null}
      </div>
      {job.status === "queued" || job.status === "running" ? (
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress} aria-label="Fortschritt"><div style={{ width: `${job.status === "queued" ? 3 : Math.max(job.progress, 5)}%` }} /></div>
      ) : null}
      {failed ? (
        <div>
          <div>{job.error_message ?? "Die Aufgabe ist fehlgeschlagen."} <span className="small muted">({job.error_code})</span></div>
          {retryable ? (
            <button className="btn small" style={{ marginTop: ".4rem" }} onClick={async () => {
              try { await api("POST", path(`/jobs/${job.id}/retry`)); void qc.invalidateQueries({ queryKey: ["job", job.id] }); toast("Aufgabe wird wiederholt.", "ok"); }
              catch { toast("Wiederholung nicht möglich.", "error"); }
            }}>{t.common.retry}</button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
