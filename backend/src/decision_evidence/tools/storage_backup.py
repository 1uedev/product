"""Object storage backup / restore / consistency check.

    python -m decision_evidence.tools.storage_backup backup  <dir>   copy all tenant objects + manifest with sha256
    python -m decision_evidence.tools.storage_backup restore <dir>   put objects back (verifies sha256 first, never deletes)
    python -m decision_evidence.tools.storage_backup verify          DB file records vs. stored objects (needs the migrator role)

Nothing here deletes objects or overwrites a different object with the same key.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from decision_evidence.config import get_settings
from decision_evidence.storage.s3 import S3Storage, build_s3_client

PREFIX = "tenant/"


def _storage() -> S3Storage:
    s = get_settings()
    st = S3Storage(build_s3_client(), s.s3_bucket)
    st.ensure_bucket()
    return st


def backup(target: Path) -> int:
    st = _storage()
    objects = target / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    for key in st.list_keys(PREFIX):
        data = st.get(key)
        dest = objects / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest[key] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    (target / "objects-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print(f"backed up {len(manifest)} objects")
    return 0


def restore(source: Path) -> int:
    st = _storage()
    manifest = json.loads((source / "objects-manifest.json").read_text())
    restored = skipped = 0
    for key, meta in sorted(manifest.items()):
        data = (source / "objects" / key).read_bytes()
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            print(f"CHECKSUM MISMATCH in backup for {key}", file=sys.stderr)
            return 3
        if st.exists(key):
            if hashlib.sha256(st.get(key)).hexdigest() != meta["sha256"]:
                print(f"object {key} exists with different content: refusing to overwrite", file=sys.stderr)
                return 4
            skipped += 1
            continue
        st.put(key, data, "application/octet-stream")
        restored += 1
    print(f"restored {restored} objects, {skipped} already present")
    return 0


def verify() -> int:
    from sqlalchemy import select, text

    from decision_evidence.db import models as m
    from decision_evidence.db.engines import get_engine
    from decision_evidence.db.session import tenant_session

    st = _storage()
    db_keys: set[str] = set()
    missing: list[str] = []
    with get_engine("migrator").connect() as c:
        tenants = [r[0] for r in c.execute(text("SELECT id FROM identity.tenants"))]
    for tid in tenants:
        with tenant_session(get_engine("migrator"), tid) as s:
            for f in s.scalars(select(m.FileRecord)):
                db_keys.add(f.object_key)
                if f.status == "ready" and not st.exists(f.object_key):
                    missing.append(f.object_key)
    orphans = [k for k in st.list_keys(PREFIX) if k not in db_keys]
    print(json.dumps({"files_in_db": len(db_keys), "missing_objects": missing, "orphan_objects": len(orphans)}, indent=1))
    return 1 if missing else 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in {"backup", "restore", "verify"}:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "verify":
        return verify()
    if len(sys.argv) < 3:
        print("directory argument required", file=sys.stderr)
        return 2
    return (backup if cmd == "backup" else restore)(Path(sys.argv[2]))


if __name__ == "__main__":
    sys.exit(main())
