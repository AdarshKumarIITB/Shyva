from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable


ROOT = Path("/mnt/desktop/Shyva")
CATALOG_INDEX = Path("/home/ubuntu/author_shyva_test_matrix.csv")
OUTPUT_JSON = ROOT / "scripts" / "exhaustive_test_matrix.json"
OUTPUT_MD = ROOT / "scripts" / "exhaustive_test_matrix_summary.md"

EXPECTED_COLUMNS = [
    "Case ID",
    "Title",
    "Product Family",
    "Lane",
    "Description",
    "Origin",
    "Destination",
    "Effective Date",
    "Expected Behavior",
    "Expected Min Transitions",
    "Expected Required Questions",
    "Expected Terminal State",
    "Notes",
]

EXPECTED_COLUMNS_ALT = [
    "Case ID",
    "Title",
    "Product Family",
    "Lane",
    "Description",
    "Origin",
    "Destination",
    "Effective Date",
    "Expected Behavior",
    "Expected Minimum Transitions",
    "Expected Required Questions",
    "Expected Terminal State",
    "Notes",
]

EXPECTED_COLUMNS_ALT_NO_NOTES = [
    "Case ID",
    "Title",
    "Product Family",
    "Lane",
    "Description",
    "Origin",
    "Destination",
    "Effective Date",
    "Expected Behavior",
    "Expected Minimum Transitions",
    "Expected Required Questions",
    "Expected Terminal State",
]

EXPECTED_COLUMNS_NO_NOTES = [
    "Case ID",
    "Title",
    "Product Family",
    "Lane",
    "Description",
    "Origin",
    "Destination",
    "Effective Date",
    "Expected Behavior",
    "Expected Min Transitions",
    "Expected Required Questions",
    "Expected Terminal State",
]

EXPECTED_COLUMNS_LOWER = [
    "case_id",
    "title",
    "product_family",
    "lane",
    "description",
    "origin",
    "destination",
    "effective_date",
    "expected_behavior",
    "expected_minimum_transitions",
    "expected_required_questions",
    "expected_terminal_state",
    "notes",
]

EXPECTED_COLUMNS_LOWER_NO_NOTES = [
    "case_id",
    "title",
    "product_family",
    "lane",
    "description",
    "origin",
    "destination",
    "effective_date",
    "expected_behavior",
    "expected_minimum_transitions",
    "expected_required_questions",
    "expected_terminal_state",
]

EXPECTED_COLUMNS_REQ_ABBR = [
    "Case ID",
    "Title",
    "Product Family",
    "Lane",
    "Description",
    "Origin",
    "Destination",
    "Effective Date",
    "Expected Behavior",
    "Expected Min Transitions",
    "Expected Req Questions",
    "Expected Terminal State",
    "Notes",
]


DETAIL_FIELD_MAP = {
    "case_id": "case_id",
    "title": "title",
    "product_family": "product_family",
    "lane": "lane",
    "description": "description",
    "origin": "origin",
    "destination": "destination",
    "effective_date": "effective_date",
    "expected_behavior": "expected_behavior",
    "expected_minimum_transitions": "expected_min_transitions",
    "expected_required_questions": "expected_required_questions",
    "expected_terminal_state": "expected_terminal_state",
    "notes": "notes",
}


def _parse_int(value: str) -> int:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0


def _normalize_row(row: dict[str, str]) -> dict:
    if "Case ID" not in row and "case_id" in row:
        row = {
            "Case ID": row.get("case_id", ""),
            "Title": row.get("title", ""),
            "Product Family": row.get("product_family", ""),
            "Lane": row.get("lane", ""),
            "Description": row.get("description", ""),
            "Origin": row.get("origin", ""),
            "Destination": row.get("destination", ""),
            "Effective Date": row.get("effective_date", ""),
            "Expected Behavior": row.get("expected_behavior", ""),
            "Expected Minimum Transitions": row.get("expected_minimum_transitions", row.get("expected_min_transitions", "0")),
            "Expected Required Questions": row.get("expected_required_questions", row.get("expected_req_questions", "0")),
            "Expected Terminal State": row.get("expected_terminal_state", ""),
            "Notes": row.get("notes", ""),
        }
    min_transition_key = "Expected Min Transitions"
    if min_transition_key not in row:
        min_transition_key = "Expected Minimum Transitions"
    req_question_key = "Expected Required Questions"
    if req_question_key not in row:
        req_question_key = "Expected Req Questions"
    normalized = {
        "case_id": row["Case ID"],
        "title": row["Title"],
        "product_family": row["Product Family"],
        "lane": row["Lane"],
        "description": row["Description"],
        "origin": row["Origin"],
        "destination": row["Destination"],
        "effective_date": row["Effective Date"],
        "expected_behavior": row["Expected Behavior"],
        "expected_min_transitions": _parse_int(row[min_transition_key]),
        "expected_required_questions": _parse_int(row[req_question_key]),
        "expected_terminal_state": row["Expected Terminal State"],
        "notes": row.get("Notes", ""),
    }
    return normalized


def _finalize_detail_row(current: dict[str, str], case_id: str, title: str) -> dict[str, str]:
    row = dict(current)
    if case_id and "case_id" not in row:
        row["case_id"] = case_id
    if title and "title" not in row:
        row["title"] = title
    return row


def _parse_detail_sections(lines: list[str], path: Path) -> list[dict]:
    rows: list[dict] = []
    current: dict[str, str] = {}
    in_details = False
    current_case_id = ""
    current_title = ""
    pending_attribute_table = False
    for raw_line in lines:
        line = raw_line.rstrip()
        if line.startswith("## Detailed Test Cases"):
            in_details = True
            current = {}
            continue
        if not in_details:
            continue
        if line.startswith("### "):
            if current:
                rows.append(_finalize_detail_row(current, current_case_id, current_title))
            current = {}
            pending_attribute_table = False
            heading = line[4:].strip()
            if ":" in heading:
                current_case_id, current_title = [part.strip() for part in heading.split(":", 1)]
            else:
                current_case_id, current_title = heading, heading
            continue
        if line.startswith("| Attribute | Value |"):
            pending_attribute_table = True
            continue
        if pending_attribute_table and line.startswith("|-----------|"):
            continue
        if pending_attribute_table and line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 2:
                key = cells[0].strip().lower().replace(" ", "_")
                value = cells[1].strip()
                mapped = DETAIL_FIELD_MAP.get(key)
                if mapped:
                    current[mapped] = value
            continue
        if (line.startswith("- **") or line.startswith("*   **") or line.startswith("* **")) and "**:" in line:
            marker_trimmed = line.lstrip("-* ")
            left, right = marker_trimmed.split(":", 1)
            raw_key = left.strip().strip("*").strip().lower()
            key = raw_key.replace(" ", "_")
            value = right.strip()
            mapped = DETAIL_FIELD_MAP.get(key) or DETAIL_FIELD_MAP.get(raw_key)
            if mapped:
                current[mapped] = value
    if current:
        rows.append(_finalize_detail_row(current, current_case_id, current_title))

    normalized_rows: list[dict] = []
    required = {value for value in DETAIL_FIELD_MAP.values() if value not in {"case_id", "title", "notes"}}
    for row in rows:
        missing = required - set(row.keys())
        if missing:
            raise ValueError(f"Missing detail fields {sorted(missing)} in {path}")
        row.setdefault("notes", "")
        row.setdefault("case_id", row.get("case_id", ""))
        row.setdefault("title", row.get("title", row.get("case_id", "")))
        row["expected_min_transitions"] = _parse_int(row["expected_min_transitions"])
        row["expected_required_questions"] = _parse_int(row["expected_required_questions"])
        normalized_rows.append(row)
    return normalized_rows


def _parse_markdown_table(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    table_lines = [line for line in lines if line.startswith("|")]
    if len(table_lines) < 2:
        raise ValueError(f"No Markdown table found in {path}")

    header = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    tabular_variants = [EXPECTED_COLUMNS, EXPECTED_COLUMNS_ALT, EXPECTED_COLUMNS_ALT_NO_NOTES, EXPECTED_COLUMNS_NO_NOTES, EXPECTED_COLUMNS_LOWER, EXPECTED_COLUMNS_LOWER_NO_NOTES, EXPECTED_COLUMNS_REQ_ABBR]
    if header in tabular_variants:
        active_columns = header
        rows: list[dict] = []
        for line in table_lines[2:]:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != len(active_columns):
                continue
            rows.append(_normalize_row(dict(zip(active_columns, cells))))
        return rows

    if header[:3] == ["Case ID", "Title", "Path Type"]:
        return _parse_detail_sections(lines, path)

    raise ValueError(f"Unexpected columns in {path}: {header}")


def _iter_catalog_paths() -> Iterable[tuple[str, Path]]:
    with CATALOG_INDEX.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            subject = row["Subject"].strip()
            catalog_file = Path(row["Catalog File"].strip())
            if not catalog_file.exists():
                raise FileNotFoundError(catalog_file)
            yield subject, catalog_file


def build() -> dict:
    matrix: list[dict] = []
    family_lane_counts: dict[str, int] = {}
    for subject, catalog_path in _iter_catalog_paths():
        cases = _parse_markdown_table(catalog_path)
        family_lane_counts[subject] = len(cases)
        for case in cases:
            case["subject"] = subject
            matrix.append(case)

    payload = {
        "total_cases": len(matrix),
        "family_lane_counts": family_lane_counts,
        "cases": matrix,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Exhaustive Test Matrix Summary",
        "",
        f"Total cases: **{len(matrix)}**",
        "",
        "| Family Lane | Case Count |",
        "|---|---:|",
    ]
    for subject, count in family_lane_counts.items():
        lines.append(f"| {subject} | {count} |")
    lines.extend([
        "",
        f"Structured JSON output: `{OUTPUT_JSON}`",
    ])
    OUTPUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    payload = build()
    print(json.dumps({"total_cases": payload["total_cases"]}, indent=2))
