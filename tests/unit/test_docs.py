"""The user manual and its screenshots must stay consistent: no broken image links, no orphaned images."""

from __future__ import annotations

import re
from pathlib import Path

MANUAL = Path(__file__).resolve().parents[2] / "docs" / "benutzerhandbuch"


def test_every_image_of_the_manual_exists_and_is_used() -> None:
    text = (MANUAL / "README.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"\]\(bilder/([^)\s]+)\)", text))
    present = {p.name for p in (MANUAL / "bilder").glob("*.png")}
    assert referenced, "the manual references no screenshots"
    assert not referenced - present, f"missing screenshots: {sorted(referenced - present)}"
    assert not present - referenced, f"screenshots that no chapter uses: {sorted(present - referenced)}"


def test_every_chapter_link_points_to_a_heading() -> None:
    text = (MANUAL / "README.md").read_text(encoding="utf-8")
    anchors = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, flags=re.M):
        slug = re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.U).strip().replace(" ", "-")
        anchors.add(slug)
    links = set(re.findall(r"\]\(#([^)]+)\)", text))
    assert links
    assert not links - anchors, f"links without a heading: {sorted(links - anchors)}"
