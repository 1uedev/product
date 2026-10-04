"""Imports all job handler modules so that they register themselves with the runner."""

from decision_evidence.modules.analysis import jobs as _analysis_jobs  # noqa: F401
from decision_evidence.modules.decisions import jobs as _decision_jobs  # noqa: F401
from decision_evidence.modules.imports import jobs as _import_jobs  # noqa: F401
