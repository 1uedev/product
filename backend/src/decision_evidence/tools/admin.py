"""Operator CLI (runs with the migrator role inside the backend image):

    python -m decision_evidence.tools.admin list-tenants
    python -m decision_evidence.tools.admin create-tenant <slug> "<name>" <owner-email>   prints a one-time owner invitation link
    python -m decision_evidence.tools.admin set-external-org <slug> <central-org-id>      explicit mapping to a future central organisation id
    python -m decision_evidence.tools.admin clear-external-org <slug>

The mapping to a central organisation is never inferred from names or e-mail domains.
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session
from decision_evidence.tools.bootstrap import create_tenant


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "list-tenants":
        with plain_session(get_engine("migrator")) as s:
            for t in s.scalars(select(m.Tenant).order_by(m.Tenant.slug)):
                print(f"{t.id}  {t.slug:24} {t.status:10} external_org_id={t.external_org_id or '-'}  {t.name}")
        return 0
    if cmd == "create-tenant" and len(args) == 3:
        path = create_tenant(args[0], args[1], args[2])
        print(f"one-time owner invitation (7 days, bound to {args[2]}): {get_settings().public_origin}{path}")
        return 0
    if cmd in {"set-external-org", "clear-external-org"}:
        with plain_session(get_engine("migrator")) as s:
            target = s.scalar(select(m.Tenant).where(m.Tenant.slug == args[0]))
            if target is None:
                print("unknown tenant", file=sys.stderr)
                return 1
            target.external_org_id = args[1] if cmd == "set-external-org" else None
        print("ok")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
