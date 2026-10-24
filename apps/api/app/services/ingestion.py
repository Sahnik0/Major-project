import csv
from pathlib import Path
from typing import Any, Dict, List, Tuple

import chardet
import polars as pl
from fastapi import HTTPException, UploadFile

from apps.api.app.config import settings


def sniff_encoding(file_path: Path, max_bytes: int = 65536) -> str:
    """Sniff text file encoding using chardet on the first chunk."""
    with open(file_path, "rb") as f:
        raw = f.read(max_bytes)
    if not raw:
        return "utf-8"
    detection = chardet.detect(raw)
    encoding = detection.get("encoding")
    return encoding if encoding else "utf-8"


def sniff_delimiter(file_path: Path, encoding: str, num_lines: int = 10) -> str:
    """Detect CSV delimiter using csv.Sniffer."""
    try:
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            lines = [f.readline() for _ in range(num_lines)]
            sample = "".join(lines)
            if not sample.strip():
                return ","
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(sample, delimiters=",\t;|")
            return dialect.delimiter
    except Exception:
        return ","


async def save_upload_stream(
    upload_file: UploadFile,
    dest_path: Path,
    max_size_mb: int = settings.MAX_UPLOAD_SIZE_MB,
) -> int:
    """Stream an uploaded file to disk while enforcing max file size."""
    max_bytes = max_size_mb * 1024 * 1024
    total_bytes = 0
    chunk_size = 1024 * 1024  # 1MB chunks

    with open(dest_path, "wb") as buffer:
        while True:
            chunk = await upload_file.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_bytes:
                buffer.close()
                if dest_path.exists():
                    dest_path.unlink()
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "FILE_TOO_LARGE",
                        "message": f"File exceeds maximum allowed size of {max_size_mb} MB.",
                        "hint": "Try filtering or compressing the dataset before uploading.",
                    },
                )
            buffer.write(chunk)
    return total_bytes


def sniff_header_row_and_meta(file_path: Path, max_rows: int = 15) -> Tuple[int, Dict[str, Any]]:
    """
    Intelligently identifies the genuine header row by scoring candidate rows,
    skipping company titles, GSTIN numbers, report dates, or empty lines.
    Also extracts document metadata such as report title and period date range.
    """
    import re
    suffix = file_path.suffix.lower()
    meta: Dict[str, Any] = {"pre_header_lines": [], "date_range": None, "report_name": None}
    rows_sample: List[List[Any]] = []

    if suffix in [".xlsx", ".xls"]:
        import openpyxl
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet = wb.active
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i >= max_rows:
                    break
                rows_sample.append(list(row))
            wb.close()
        except Exception:
            pass
    elif suffix == ".csv":
        encoding = sniff_encoding(file_path)
        delimiter = sniff_delimiter(file_path, encoding)
        try:
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                reader = csv.reader(f, delimiter=delimiter)
                for i, row in enumerate(reader):
                    if i >= max_rows:
                        break
                    rows_sample.append(row)
        except Exception:
            pass

    if not rows_sample:
        return 0, meta

    # Score each row for likelihood of being the column header:
    # A genuine header row has:
    # 1. High number of non-empty text strings
    # 2. High number of unique strings
    scores: List[Tuple[int, int, int]] = []
    for idx, r in enumerate(rows_sample):
        valid_strs = [
            str(x).strip() for x in r 
            if x is not None and str(x).strip() != "" and str(x).strip().lower() not in ("none", "nan", "null")
        ]
        unique_strs = len(set(valid_strs))
        scores.append((idx, len(valid_strs), unique_strs))

    best_row_idx = max(scores, key=lambda s: (s[2], s[1]))[0]

    # Don't skip row 0 if row 0 has plenty of headers (e.g. >= 3) and best row isn't significantly better
    if scores[0][2] >= 3 and scores[best_row_idx][2] <= scores[0][2] + 1:
        best_row_idx = 0

    pre_lines = []
    for i in range(best_row_idx):
        line_strs = [str(x).strip() for x in rows_sample[i] if x is not None and str(x).strip() != ""]
        if line_strs:
            pre_lines.append(" ".join(line_strs))
    meta["pre_header_lines"] = pre_lines
    meta_text = " ".join(pre_lines)

    m = re.search(r"(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4})\s*(?:to|-)\s*(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4})", meta_text, re.IGNORECASE)
    if m:
        try:
            import pandas as pd
            s_dt = pd.to_datetime(m.group(1), dayfirst=True).date()
            e_dt = pd.to_datetime(m.group(2), dayfirst=True).date()
            meta["date_range"] = (s_dt.isoformat(), e_dt.isoformat())
        except Exception:
            pass

    return best_row_idx, meta


def read_raw_dataframe(file_path: Path) -> pl.DataFrame:
    """Read a CSV or XLSX file into a Polars DataFrame with auto header sniffing and delimiter detection."""
    suffix = file_path.suffix.lower()
    header_idx, _ = sniff_header_row_and_meta(file_path)

    if suffix == ".csv":
        encoding = sniff_encoding(file_path)
        delimiter = sniff_delimiter(file_path, encoding)
        try:
            return pl.read_csv(
                file_path,
                separator=delimiter,
                encoding=encoding,
                skip_rows=header_idx,
                ignore_errors=True,
                infer_schema_length=1000,
                null_values=["NA", "N/A", "null", "NULL", "", "None", "nan", "NaN"],
            )
        except Exception:
            # Fallback to UTF-8 with replace
            return pl.read_csv(
                file_path,
                separator=delimiter,
                encoding="utf-8",
                skip_rows=header_idx,
                ignore_errors=True,
                infer_schema_length=1000,
                null_values=["NA", "N/A", "null", "NULL", "", "None", "nan", "NaN"],
            )
    elif suffix in [".xlsx", ".xls"]:
        try:
            # Try fast calamine engine with detected header offset
            return pl.read_excel(
                file_path,
                engine="calamine",
                read_options={"header_row": header_idx},
            )
        except Exception:
            import pandas as pd
            df_pd = pd.read_excel(file_path, header=header_idx)
            return pl.from_pandas(df_pd)
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FILE_TYPE",
                "message": f"Unsupported file extension '{suffix}'. Only .csv and .xlsx files are supported.",
                "hint": "Convert your file to CSV or XLSX format.",
            },
        )


def inspect_raw_file(
    file_path: Path,
    include_meta: bool = False,
) -> Any:
    """Inspect raw file columns, sample rows, and total row count (and optional metadata)."""
    header_idx, meta = sniff_header_row_and_meta(file_path)
    df = read_raw_dataframe(file_path)
    columns = df.columns
    row_count = len(df)
    # Get up to 5 sample rows as dicts
    sample_df = df.head(5)
    sample_rows = sample_df.to_dicts()
    if include_meta:
        return columns, sample_rows, row_count, meta
    return columns, sample_rows, row_count
