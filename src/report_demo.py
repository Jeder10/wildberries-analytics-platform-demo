"""Prepare the interview report using only the synthetic model definitions."""

from datetime import date
import json
from pathlib import Path
import re
from uuid import uuid4

from .app_paths import APP_ROOT, RESOURCE_ROOT
from .report_preview import (
    ANALYSIS, DAILY, DATE_SLICERS, FINANCE, MODEL, REPORT, copy_report, save_json, set_date_slicer,
)


def prepare_demo(*, template: Path = RESOURCE_ROOT / "powerbi", output_root: Path = APP_ROOT / "data") -> Path:
    parameters = Path(MODEL) / "definition" / "expressions.tmdl"
    original = (template / parameters).read_text(encoding="utf-8")
    synthetic, changed = re.subn(r"(?m)^expression DemoMode = (?:false|true)(?=\s|$)", "expression DemoMode = true", original)
    if changed != 1:
        raise ValueError("Ожидался один параметр DemoMode.")
    output = output_root / f"powerbi-demo-{uuid4().hex[:8]}"
    target = copy_report(template, output, "Wildberries Demo.pbip")
    (output / parameters).write_text(synthetic, encoding="utf-8")
    pages = output / REPORT / "definition" / "pages"
    manifest = pages / "pages.json"
    settings = json.loads(manifest.read_text(encoding="utf-8"))
    settings["activePageName"] = DAILY
    save_json(manifest, settings)
    for page in (DAILY, FINANCE, ANALYSIS):
        slicer, entity, field = DATE_SLICERS[page]
        first = date(2025, 1, 15) if page == ANALYSIS else date(2025, 2, 10)
        last = date(2025, 2, 12)
        set_date_slicer(pages / page / "visuals" / slicer / "visual.json", entity, field, first, last)
    return target


if __name__ == "__main__":
    print(prepare_demo())
