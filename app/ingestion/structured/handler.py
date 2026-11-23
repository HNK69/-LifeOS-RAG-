"""
handler.py

Generic structured-data inspection layer.

The handler extracts useful schema/metadata without blindly embedding
every row of a potentially large structured dataset.

Supported currently:
- CSV
- JSON

Future handlers can be added for:
- Excel
- databases
- Parquet
- other structured formats
"""

from pathlib import Path
import csv
import json

try:
    from ingestion.guardrails import check_file_size
except ImportError:
    from app.ingestion.guardrails import check_file_size


def _inspect_csv(path):
    """
    Inspect a CSV without loading the entire dataset into memory.

    Time:  O(n), where n is the number of CSV rows.
    Space: O(c), where c is the number of columns.
    """
    check_file_size(path)

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:

        reader = csv.reader(file)

        try:
            columns = next(reader)
        except StopIteration:
            return {
                "format": "csv",
                "columns": [],
                "column_count": 0,
                "row_count": 0,
            }

        row_count = 0

        for _ in reader:
            row_count += 1

    return {
        "format": "csv",
        "columns": columns,
        "column_count": len(columns),
        "row_count": row_count,
    }


def _inspect_json(path):
    """
    Inspect a JSON file and determine its top-level structure.

    JSON must currently be parsed as a complete document because
    standard JSON does not inherently provide row-by-row streaming.

    Time:  O(n), where n is the JSON file size.
    Space: O(n) in the worst case because the JSON structure is parsed.
    """
    check_file_size(path)

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:

        data = json.load(file)

    if isinstance(data, list):

        return {
            "format": "json",
            "structure": "array",
            "item_count": len(data),
        }

    if isinstance(data, dict):

        return {
            "format": "json",
            "structure": "object",
            "keys": list(data.keys()),
            "key_count": len(data),
        }

    return {
        "format": "json",
        "structure": type(data).__name__,
    }


def _inspect_excel(path):
    """
    Inspect an Excel file (.xlsx, .xls) and extract sheet and structural metadata
    without loading all contents into memory at once.
    """
    check_file_size(path)
    import openpyxl

    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:
        return {
            "format": "xlsx",
            "status": "error",
            "error": f"Invalid or corrupt Excel workbook: {exc}",
            "sheets": [],
            "sheet_count": 0,
            "total_rows": 0,
        }

    sheets_info = []
    total_rows = 0

    try:
        for name in wb.sheetnames:
            ws = wb[name]
            row_count = ws.max_row or 0
            col_count = ws.max_column or 0
            total_rows += row_count
            sheets_info.append({
                "name": name,
                "row_count": row_count,
                "column_count": col_count,
            })
    finally:
        wb.close()

    return {
        "format": "xlsx",
        "sheets": sheets_info,
        "sheet_count": len(sheets_info),
        "total_rows": total_rows,
    }


def handle_structured_file(file_path):
    """
    Inspect a structured-data file and return useful metadata.

    This function does NOT embed the dataset.

    The returned metadata can later be stored in the document
    registry or used by a dedicated structured-data retrieval layer.
    """

    path = Path(file_path).resolve()

    if not path.is_file():
        raise FileNotFoundError(
            f"Structured file does not exist: {path}"
        )

    extension = path.suffix.lower()

    result = {
        "file_path": str(path),
        "filename": path.name,
        "extension": extension,
        "size_bytes": path.stat().st_size,
    }

    if extension == ".csv":

        result.update(
            _inspect_csv(path)
        )

    elif extension == ".json":

        result.update(
            _inspect_json(path)
        )

    elif extension in (".xlsx", ".xls"):

        result.update(
            _inspect_excel(path)
        )

    else:

        result["format"] = "unsupported_structured_format"
        result["status"] = "handler_not_implemented"

        return result

    if "status" not in result:
        result["status"] = "inspected"

    return result