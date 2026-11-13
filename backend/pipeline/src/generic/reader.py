"""Turn an uploaded .csv or .xlsx into one canonical file: UTF-8, comma-separated, header c0..cN.

The original column names are kept only as display labels (returned in `columns`). Spark, SQL and
file paths only ever see c0..cN, so a hostile header cannot inject anything.
"""
import csv
import io
import zipfile
from pathlib import Path

MAX_COLUMNS = 100
MAX_ROWS = 200_000
MAX_XLSX_UNPACKED = 60 * 1024 * 1024  # zip-bomb guard: total uncompressed size of the workbook
MAX_SHEETS = 20
DELIMITERS = ",;\t|"


class UnreadableFile(ValueError):
    """The file cannot be read as a table; the message is shown to the user."""


def _clean_header(raw):
    """Display names: trimmed, blanks become 'column N', duplicates get a suffix."""
    out, seen = [], {}
    for i, name in enumerate(raw):
        name = (str(name) if name is not None else "").strip() or f"column {i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        out.append(name[:120])
    return out


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(round(v, 10)).rstrip("0").rstrip(".") if v != int(v) else str(int(v))
    return str(v).strip()


def _csv_rows(path):
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")  # Excel's default "CSV" save on Windows
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=DELIMITERS)
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    return list(csv.reader(io.StringIO(text), delimiter=delimiter)), {"delimiter": delimiter}


def _xlsx_rows(path):
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_XLSX_UNPACKED:
            raise UnreadableFile("The Excel file unpacks to more than 60 MB; save the data sheet as CSV instead.")
    import openpyxl  # needs defusedxml installed (requirements.txt) to reject malicious XML

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if len(wb.sheetnames) > MAX_SHEETS:
            raise UnreadableFile(f"The workbook has more than {MAX_SHEETS} sheets.")
        best, best_name = [], None
        for ws in wb.worksheets:  # the data sheet is the one with the most rows (skips "Data Dict")
            rows = []
            for r in ws.iter_rows(values_only=True):
                if len(rows) > MAX_ROWS + 1:
                    break
                if any(v is not None and str(v).strip() != "" for v in r):
                    rows.append([_cell(v) for v in r])
            if len(rows) > len(best):
                best, best_name = rows, ws.title
        return best, {"sheet": best_name, "sheets": list(wb.sheetnames)}
    finally:
        wb.close()


def read_table(path):
    """-> (header, rows, info). Raises UnreadableFile with a user-facing reason."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        try:
            rows, info = _xlsx_rows(path)
        except (zipfile.BadZipFile, KeyError, OSError) as e:
            raise UnreadableFile("The file is not a readable .xlsx workbook.") from e
    else:
        rows, info = _csv_rows(path)
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise UnreadableFile("The file is empty.")
    header = _clean_header(rows[0])
    width = len(header)
    if width < 2:
        raise UnreadableFile("The file has only one column; it needs one row per customer with several columns.")
    if width > MAX_COLUMNS:
        raise UnreadableFile(f"The file has {width} columns; the limit is {MAX_COLUMNS}.")
    data = rows[1:]
    if len(data) > MAX_ROWS:
        raise UnreadableFile(f"The file has more than {MAX_ROWS:,} rows.")
    # Pad short rows, cut overlong ones (a stray trailing comma).
    data = [(r + [""] * width)[:width] for r in data]
    return header, data, {**info, "rows": len(data), "columns": width}


def write_canonical(header, rows, out_csv):
    """Write c0..cN plus a 1-based `row` column (the ID when the file has none).
    Returns the display map [{"key": "c0", "name": "CustomerId"}, ...] (without `row`)."""
    keys = [f"c{i}" for i in range(len(header))]
    out = Path(out_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([*keys, "row"])
        w.writerows([*r, i] for i, r in enumerate(rows, start=1))
    return [{"key": k, "name": n} for k, n in zip(keys, header)]
