"""Writes the OpenAPI document: ``python -m decision_evidence.tools.export_openapi docs/openapi.json``."""

from __future__ import annotations

import json
import sys

from decision_evidence.main import create_app


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    schema = create_app().openapi()
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(schema, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {target} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
