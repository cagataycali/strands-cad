"""Meta layer — BOM parsing + design journal."""
from __future__ import annotations
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from strands import tool
from strands_cad._common import ok, err


def _parse_price(raw: str) -> float | None:
    """Extract a numeric price from '$12.99' / '€3.43' / '12.99 USD' etc."""
    if not raw:
        return None
    m = re.search(r"[-+]?\d*\.?\d+", str(raw).replace(",", "."))
    return float(m.group(0)) if m else None


@tool
def bom_parse(csv_file: str) -> dict:
    """Parse a BOM (bill of materials) CSV.

    Expects columns like: part, quantity, price, link (any subset OK — headers are used).

    Args:
        csv_file: Path to BOM CSV.

    Returns:
        {status, content, parts:[{...}], count}
    """
    src = Path(csv_file).resolve()
    if not src.exists():
        return err(f"BOM not found: {src}")
    parts = []
    with open(src, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_clean = {k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                        for k, v in row.items() if k}
            row_clean["_price"] = _parse_price(row_clean.get("price", ""))
            try:
                row_clean["_quantity"] = int(re.search(r"\d+", str(row_clean.get("quantity", "1"))).group())
            except Exception:
                row_clean["_quantity"] = 1
            parts.append(row_clean)
    return ok(f"parsed {len(parts)} parts", parts=parts, count=len(parts))


@tool
def bom_total(csv_file: str, only_missing: bool = False) -> dict:
    """Compute total cost of a BOM.

    Args:
        csv_file: Path to BOM CSV.
        only_missing: If True, sum only parts NOT marked as owned (looks for '✓' or 'yes' in any column).

    Returns:
        {status, content, total, parts_counted, parts_skipped}
    """
    parsed = bom_parse(csv_file)
    if parsed["status"] != "success":
        return parsed
    parts = parsed["parts"]
    total = 0.0
    counted = 0
    skipped = 0
    for p in parts:
        price = p.get("_price")
        qty = p.get("_quantity", 1)
        if price is None:
            skipped += 1
            continue
        if only_missing:
            owned = any((str(v).strip().lower() in ("✓", "yes", "true", "1", "[✓]"))
                        for v in p.values())
            if owned:
                skipped += 1
                continue
        total += price * qty
        counted += 1
    return ok(f"total = {total:.2f} across {counted} part(s), {skipped} skipped",
              total=round(total, 2), parts_counted=counted, parts_skipped=skipped)


@tool
def journal_append(
    journal_file: str,
    iteration: str,
    summary: str,
    details: dict | None = None,
) -> dict:
    """Append a dated entry to a Markdown design journal (creates file if missing).

    Args:
        journal_file: Path to journal .md file (created if missing).
        iteration: Version / iteration label (e.g. "v6", "iter-12").
        summary: Short one-line summary.
        details: Optional dict of extra fields to render as a bullet list.
    """
    p = Path(journal_file).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"\n## {iteration} — {ts}", f"", f"**{summary}**", ""]
    if details:
        for k, v in details.items():
            lines.append(f"- **{k}**: {v}")
    entry = "\n".join(lines) + "\n"
    if not p.exists():
        p.write_text(f"# Design Journal\n{entry}")
    else:
        with open(p, "a", encoding="utf-8") as f:
            f.write(entry)
    return ok(f"appended {iteration} to {p}", path=str(p), iteration=iteration)
