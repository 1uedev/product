"""Worker handler for import commits. All rows are inserted in ONE transaction together with the job completion."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from decision_evidence.db import models as m
from decision_evidence.jobs.runner import JobContext, JobError, job_handler
from decision_evidence.modules.imports import service
from decision_evidence.modules.imports.domain.parsing import FileRejected
from decision_evidence.storage.port import ObjectNotFound
from decision_evidence.storage.s3 import get_storage
from decision_evidence.tenancy.context import add_audit


@job_handler("import_commit")
def import_commit(ctx: JobContext) -> None:
    batch_id = uuid.UUID(ctx.payload["batch_id"])
    with ctx.session() as s:
        batch = s.get(m.ImportBatch, batch_id)
        if batch is None:
            raise JobError("batch_not_found", "Der Import existiert nicht mehr.")
        if batch.status == "committed":  # redelivered after success: nothing to do, job state is fixed below
            raise JobError("already_committed", "Der Import wurde bereits ausgeführt.")
        file = s.get(m.FileRecord, batch.file_id)
        file_key = file.object_key if file else None
    if file_key is None:
        raise JobError("file_not_found", "Die Datei existiert nicht mehr.")
    try:
        data = get_storage().get(file_key)
    except ObjectNotFound as exc:
        raise JobError("file_missing_in_storage", "Die Datei fehlt im Speicher.") from exc
    ctx.set_progress(5)
    ctx.check_lease()

    with ctx.session() as s:
        batch = s.get(m.ImportBatch, batch_id)
        file = s.get(m.FileRecord, batch.file_id)  # type: ignore[union-attr]
        assert batch is not None and file is not None
        try:
            plan = service.compute_import_plan(s, batch, file, data)
        except FileRejected as exc:
            _fail_batch(ctx, batch_id, exc.code, exc.message)
            raise JobError(exc.code, exc.message) from exc
        if plan["blocking"]:
            summary = service.summarise_plan(plan)
            _fail_batch(ctx, batch_id, "import_invalid", "; ".join(plan["blocking"])[:400], summary)
            raise JobError("import_invalid", "; ".join(plan["blocking"])[:400])
        result = service.apply_plan(s, batch, file, plan, progress=ctx.set_progress)
        batch.status, batch.result = "committed", result
        add_audit(s, ctx.requested_by, "import.committed", "import_batch", batch.id, {"kind": batch.kind, **{k: v for k, v in result.items() if isinstance(v, int)}})
        ctx.complete(s, {"batch_id": str(batch.id), **result})


def _fail_batch(ctx: JobContext, batch_id: uuid.UUID, code: str, message: str, summary: dict | None = None) -> None:
    with ctx.session() as s:
        b = s.scalar(select(m.ImportBatch).where(m.ImportBatch.id == batch_id).with_for_update())
        if b is not None and b.status != "committed":
            b.status = "failed"
            b.result = {"error_code": code, "message": message, **({"preview": summary} if summary else {})}
