"""
query.py

Generic structured-data query engine.

Currently supports:
- CSV filtering
- counting
- numeric aggregation
- sorting
- bounded row retrieval

Design goals:
- Never load an entire CSV into memory.
- Stream rows where possible.
- Keep memory bounded by max_results.
- Keep this layer independent of natural-language intent parsing.
- Avoid dataset-specific assumptions.

The future router will translate natural-language requests into
these operations.
"""

from pathlib import Path
import csv


DEFAULT_MAX_RESULTS = 100
import heapq


class _ReverseSortKey:
    """
    Reverses comparisons so heapq can act as a max-heap.

    Supports both numeric and string sort values.
    """

    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return self.value > other.value
    


def _open_csv(file_path):
    """
    Open a CSV and return the file handle + DictReader.

    The caller is responsible for closing the file.

    Space: O(c), where c = number of columns.
    """

    path = Path(file_path).resolve()

    if not path.is_file():
        raise FileNotFoundError(
            f"Structured file does not exist: {path}"
        )

    if path.suffix.lower() != ".csv":
        raise ValueError(
            f"CSV operation requires a CSV file: {path}"
        )

    file = path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    )

    reader = csv.DictReader(file)

    if reader.fieldnames is None:
        file.close()
        raise ValueError(
            f"CSV has no header: {path.name}"
        )

    return file, reader


def _validate_column(reader, column):
    """Validate that a requested column exists."""

    if column not in reader.fieldnames:
        raise ValueError(
            f"Column '{column}' not found. "
            f"Available columns: {list(reader.fieldnames)}"
        )


def _matches(row, filters):
    """
    Check whether a row satisfies all filters.

    Filters:
        {
            "column": expected_value
        }

    Matching is case-insensitive string matching.

    Time: O(f), where f = number of filters.
    Space: O(1).
    """

    for column, expected in filters.items():

        actual = str(
            row.get(column, "")
        ).strip().casefold()

        expected = str(
            expected
        ).strip().casefold()

        if actual != expected:
            return False

    return True


def query_csv(
    file_path,
    filters=None,
    max_results=DEFAULT_MAX_RESULTS,
):
    """
    Filter CSV rows using equality conditions.

    Example:

        query_csv(
            path,
            filters={"Churn": "Yes"},
            max_results=10,
        )

    Returns:
        filename
        columns
        match_count
        truncated
        rows

    Time:
        O(n * f)

        n = rows scanned
        f = number of filters

    Space:
        O(r * c)

        r <= max_results
        c = number of columns
    """

    if max_results <= 0:
        raise ValueError(
            "max_results must be greater than zero."
        )

    filters = filters or {}

    file, reader = _open_csv(file_path)

    try:

        for column in filters:
            _validate_column(reader, column)

        rows = []
        match_count = 0

        for row in reader:

            if not _matches(row, filters):
                continue

            match_count += 1

            if len(rows) < max_results:
                rows.append(dict(row))

        return {
            "filename": Path(file_path).name,
            "columns": list(reader.fieldnames),
            "match_count": match_count,
            "truncated": match_count > max_results,
            "rows": rows,
        }

    finally:
        file.close()


def count_csv(
    file_path,
    filters=None,
):
    """
    Count rows matching optional equality filters.

    Returns only the count; matching rows are never stored.

    Time:  O(n * f)
    Space: O(c)
    """

    filters = filters or {}

    file, reader = _open_csv(file_path)

    try:

        for column in filters:
            _validate_column(reader, column)

        count = 0

        for row in reader:

            if _matches(row, filters):
                count += 1

        return count

    finally:
        file.close()


def aggregate_csv(
    file_path,
    column,
    operation,
    filters=None,
):
    """
    Perform a numeric aggregation.

    Supported operations:
        sum
        avg
        min
        max

    Numeric values that cannot be converted to float are ignored.

    Time:  O(n * f)
    Space: O(1) with respect to row count.
    """

    filters = filters or {}

    file, reader = _open_csv(file_path)

    try:

        _validate_column(reader, column)

        for filter_column in filters:
            _validate_column(reader, filter_column)

        operation = operation.lower().strip()

        supported = {
            "sum",
            "avg",
            "min",
            "max",
        }

        if operation not in supported:
            raise ValueError(
                f"Unsupported aggregation '{operation}'. "
                f"Supported: {sorted(supported)}"
            )

        total = 0.0
        count = 0
        minimum = None
        maximum = None

        for row in reader:

            if not _matches(row, filters):
                continue

            raw_value = row.get(column)

            if raw_value is None:
                continue

            try:
                value = float(
                    str(raw_value).strip()
                )
            except (TypeError, ValueError):
                continue

            total += value
            count += 1

            if minimum is None or value < minimum:
                minimum = value

            if maximum is None or value > maximum:
                maximum = value

        if count == 0:
            return None

        if operation == "sum":
            return total

        if operation == "avg":
            return total / count

        if operation == "min":
            return minimum

        return maximum

    finally:
        file.close()


def sort_csv(
    file_path,
    sort_column,
    descending=False,
    filters=None,
    max_results=DEFAULT_MAX_RESULTS,
):
    """
    Return the top/bottom matching rows using bounded memory.

    Time:
        O(n * f + n log k)

    Space:
        O(k * c)

    where:
        n = rows scanned
        f = number of filters
        k = max_results
        c = number of columns
    """

    if max_results <= 0:
        raise ValueError(
            "max_results must be greater than zero."
        )

    filters = filters or {}

    file, reader = _open_csv(file_path)

    try:
        _validate_column(reader, sort_column)

        for filter_column in filters:
            _validate_column(reader, filter_column)

        heap = []
        match_count = 0

        def sort_key(row):
            value = row.get(sort_column, "").strip()

            try:
                return (0, float(value))
            except ValueError:
                return (1, value.casefold())

        for row in reader:

            if not _matches(row, filters):
                continue

            match_count += 1

            row = dict(row)
            key = sort_key(row)

            if descending:
                # Keep the largest k values.
                heap_item = (key, row)

                if len(heap) < max_results:
                    heapq.heappush(heap, heap_item)

                elif key > heap[0][0]:
                    heapq.heapreplace(heap, heap_item)

            else:
                # Keep the smallest k values.
                heap_item = (
                    _ReverseSortKey(key),
                    row,
                )

                if len(heap) < max_results:
                    heapq.heappush(heap, heap_item)

                elif key < heap[0][0].value:
                    heapq.heapreplace(heap, heap_item)

        if descending:

            heap.sort(
                key=lambda item: item[0],
                reverse=True,
            )

            rows = [
                item[1]
                for item in heap
            ]

        else:

            heap.sort(
                key=lambda item: item[0].value
            )

            rows = [
                item[1]
                for item in heap
            ]

        return {
            "filename": Path(file_path).name,
            "columns": list(reader.fieldnames),
            "match_count": match_count,
            "truncated": match_count > max_results,
            "rows": rows,
        }

    finally:
        file.close()

def get_csv_columns(file_path):
    """
    Return CSV column names.

    Only the header is read.

    Time: O(1) relative to row count.
    Space: O(c).
    """

    file, reader = _open_csv(file_path)

    try:
        return list(reader.fieldnames)

    finally:
        file.close()