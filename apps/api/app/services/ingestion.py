import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import chardet
import polars as pl
from fastapi import HTTPException, UploadFile

from apps.api.app.config import settings

SUPPORTED_EXTENSIONS = {
    ".csv",
    ".tsv",
    ".txt",
    ".xlsx",
    ".xls",
    ".json",
    ".jsonl",
    ".ndjson",
    ".parquet",
}
NULL_TOKENS = ["NA", "N/A", "null", "NULL", "", "None", "nan", "NaN", "-", "nil", "NIL"]


def sniff_encoding(file_path: Path, max_bytes: int = 65536) -> str:
    """Sniff text file encoding using chardet on the first chunk."""
    with open(file_path, "rb") as f:
        raw = f.read(max_bytes)
    if not raw:
        return "utf-8"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    detection = chardet.detect(raw)
    encoding = detection.get("encoding")
    return encoding if encoding else "utf-8"


def sniff_delimiter(file_path: Path, encoding: str, num_lines: int = 15) -> str:
    """Detect delimited file separator using csv.Sniffer with fallback token counting."""
    if file_path.suffix.lower() == ".tsv":
        return "\t"
    try:
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            lines = [f.readline() for _ in range(num_lines)]
            lines = [ln for ln in lines if ln.strip()]
            sample = "".join(lines)
            if not sample.strip():
                return ","
            sniffer = csv.Sniffer()
            dialect = sniffer.sniff(sample, delimiters=",\t;|")
            return dialect.delimiter
    except Exception:
        try:
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                first_line = f.readline()
            counts = {d: first_line.count(d) for d in [",", "\t", ";", "|"]}
            best = max(counts, key=counts.get)
            return best if counts[best] > 0 else ","
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
    suffix = file_path.suffix.lower()
    meta: Dict[str, Any] = {"pre_header_lines": [], "date_range": None, "report_name": None}
    rows_sample: List[List[Any]] = []

    if suffix in [".xlsx", ".xls"]:
        import openpyxl
        try:
            wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
            sheet = wb.active
            # Pick first non-empty sheet if active sheet has no rows
            if sheet is None or sheet.max_row in (None, 0):
                for name in wb.sheetnames:
                    cand_sheet = wb[name]
                    if cand_sheet.max_row and cand_sheet.max_row > 0:
                        sheet = cand_sheet
                        break
            if sheet is not None:
                for i, row in enumerate(sheet.iter_rows(values_only=True)):
                    if i >= max_rows:
                        break
                    rows_sample.append(list(row))
            wb.close()
        except Exception:
            pass
    elif suffix in [".csv", ".tsv", ".txt"]:
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
    else:
        return 0, meta

    if not rows_sample:
        return 0, meta

    # Score each row for likelihood of being the column header:
    # Genuine headers have many unique non-numeric text labels rather than pure numbers.
    scores: List[Tuple[int, float, int, int]] = []
    for idx, r in enumerate(rows_sample):
        valid_strs = [
            str(x).strip()
            for x in r
            if x is not None and str(x).strip() != "" and str(x).strip().lower() not in ("none", "nan", "null")
        ]
        if not valid_strs:
            scores.append((idx, -1.0, 0, 0))
            continue
        unique_strs = len(set(valid_strs))
        alpha_like = sum(1 for s in valid_strs if re.search(r"[A-Za-z]", s) and not re.match(r"^\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4}$", s))
        score = unique_strs + 0.5 * alpha_like
        scores.append((idx, score, len(valid_strs), unique_strs))

    best_row_idx = max(scores, key=lambda s: (s[1], s[3], s[2]))[0]

    # Don't skip row 0 if row 0 already looks like a clean header row
    if scores[0][3] >= 2 and scores[0][1] >= scores[best_row_idx][1] - 1.5:
        best_row_idx = 0

    pre_lines = []
    for i in range(best_row_idx):
        line_strs = [str(x).strip() for x in rows_sample[i] if x is not None and str(x).strip() != ""]
        if line_strs:
            pre_lines.append(" ".join(line_strs))
    meta["pre_header_lines"] = pre_lines
    if pre_lines:
        meta["report_name"] = pre_lines[0][:120]
    meta_text = " ".join(pre_lines)

    m = re.search(
        r"(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4})\s*(?:to|-)\s*(\d{1,4}[-/\.]\d{1,2}[-/\.]\d{1,4})",
        meta_text,
        re.IGNORECASE,
    )
    if m:
        try:
            import pandas as pd

            s_dt = pd.to_datetime(m.group(1), dayfirst=True).date()
            e_dt = pd.to_datetime(m.group(2), dayfirst=True).date()
            meta["date_range"] = (s_dt.isoformat(), e_dt.isoformat())
        except Exception:
            pass

    return best_row_idx, meta


_CURRENCY_NUM_RE = re.compile(
    r"^\s*\(?[-+]?\s*(?:[$€£₹¥]|USD|INR|EUR|GBP|Rs\.?)?\s*\d{1,3}(?:,\d{2,3})*(?:\.\d+)?\s*%?\)?\s*$"
    r"|^\s*\(?[-+]?\s*(?:[$€£₹¥]|USD|INR|EUR|GBP|Rs\.?)?\s*\d+(?:\.\d+)?\s*%?\)?\s*$",
    re.IGNORECASE,
)


def _sanitize_raw_dataframe(df: pl.DataFrame) -> pl.DataFrame:
    """
    Cleans raw dataframe headers, removes 100%-empty unnamed columns and all-null rows,
    and coerces formatted numeric strings (currency, commas, percentages) to Float64
    while preserving genuine string identifiers (leading zeros, codes, dates).
    """
    if df.width == 0:
        return df

    # 1. Clean & deduplicate column names
    cleaned_names: List[str] = []
    seen: Dict[str, int] = {}
    drop_unnamed_empty: List[str] = []

    for idx, col in enumerate(df.columns):
        raw_name = str(col).strip() if col is not None else ""
        is_unnamed = (
            not raw_name
            or raw_name.lower().startswith("unnamed:")
            or raw_name.lower().startswith("__unnamed")
        )
        if is_unnamed and df[col].null_count() == len(df):
            drop_unnamed_empty.append(col)
            continue
        base_name = raw_name if raw_name and not is_unnamed else f"Column_{idx + 1}"
        if base_name in seen:
            seen[base_name] += 1
            final_name = f"{base_name}_{seen[base_name]}"
        else:
            seen[base_name] = 1
            final_name = base_name
        cleaned_names.append((col, final_name))

    if drop_unnamed_empty:
        df = df.drop(drop_unnamed_empty)

    rename_map = {old: new for old, new in cleaned_names if old != new and old in df.columns}
    if rename_map:
        df = df.rename(rename_map)

    if len(df) == 0 or df.width == 0:
        return df

    # 2. Drop completely empty rows (where every column is null)
    df = df.filter(~pl.all_horizontal(pl.all().is_null()))
    if len(df) == 0:
        return df

    # 3. Coerce formatted numeric strings (e.g. "$1,250.50", "₹45,000", "(120.5)", "18.5%")
    cast_exprs = []
    id_like_kw = ("id", "code", "sku", "barcode", "zip", "postal", "phone", "mobile", "gst", "hsn", "account")
    for col in df.columns:
        if df[col].dtype not in (pl.Utf8, pl.String):
            continue
        col_lower = col.lower()
        if any(k in col_lower for k in id_like_kw):
            continue
        non_null = df[col].drop_nulls()
        if len(non_null) == 0:
            continue
        sample = non_null.head(200).to_list()
        # Skip if values have leading zeros like "00123" (likely identifier/code)
        if any(isinstance(v, str) and len(v.strip()) > 1 and v.strip().startswith("0") and v.strip()[1:2].isdigit() for v in sample):
            continue
        # Only coerce when there is actually formatting ($/₹/,/%/parentheses) or plain numbers stored as strings
        matched = sum(1 for v in sample if isinstance(v, str) and bool(_CURRENCY_NUM_RE.match(v)))
        if matched / len(sample) >= 0.90:
            # Verify it's not a date column like "20230101" only if needed; plain numbers or currency convert cleanly
            cleaned_expr = (
                pl.col(col)
                .str.strip_chars()
                .str.replace_all(r"^\((.*)\)$", "-$1")
                .str.replace_all(r"[$€£₹¥,%]", "")
                .str.replace_all(r"(?i)\b(?:USD|INR|EUR|GBP|Rs\.?)\b", "")
                .str.strip_chars()
                .cast(pl.Float64, strict=False)
                .alias(col)
            )
            cast_exprs.append(cleaned_expr)

    if cast_exprs:
        df = df.with_columns(cast_exprs)

    return df


def _read_json_dataframe(file_path: Path) -> pl.DataFrame:
    """Reads JSON array, nested wrapper object ({data/records/items/rows: [...]}), or columnar dict into Polars."""
    with open(file_path, "r", encoding=sniff_encoding(file_path), errors="replace") as f:
        payload = json.load(f)

    if isinstance(payload, list):
        return pl.from_dicts(payload, infer_schema_length=1000) if payload else pl.DataFrame()
    if isinstance(payload, dict):
        for key in ("data", "records", "rows", "items", "results"):
            if key in payload and isinstance(payload[key], list):
                return pl.from_dicts(payload[key], infer_schema_length=1000) if payload[key] else pl.DataFrame()
        # Check if dict of equal-length lists (column-oriented JSON)
        if payload and all(isinstance(v, list) for v in payload.values()):
            return pl.DataFrame(payload)
        # Single flat record
        return pl.from_dicts([payload])
    raise ValueError("JSON root must be an array of records or an object containing records.")


def read_raw_dataframe(file_path: Path) -> pl.DataFrame:
    """Read CSV, TSV, TXT, XLSX, XLS, JSON, JSONL, or Parquet into a sanitized Polars DataFrame."""
    suffix = file_path.suffix.lower()
    header_idx, _ = sniff_header_row_and_meta(file_path)

    if suffix in [".csv", ".tsv", ".txt"]:
        encoding = sniff_encoding(file_path)
        delimiter = sniff_delimiter(file_path, encoding)
        try:
            df = pl.read_csv(
                file_path,
                separator=delimiter,
                encoding=encoding,
                skip_rows=header_idx,
                ignore_errors=True,
                infer_schema_length=2000,
                try_parse_dates=False,
                null_values=NULL_TOKENS,
            )
        except Exception:
            df = pl.read_csv(
                file_path,
                separator=delimiter,
                encoding="utf-8",
                skip_rows=header_idx,
                ignore_errors=True,
                infer_schema_length=2000,
                try_parse_dates=False,
                null_values=NULL_TOKENS,
            )
        return _sanitize_raw_dataframe(df)

    elif suffix in [".xlsx", ".xls"]:
        try:
            df = pl.read_excel(
                file_path,
                engine="calamine",
                read_options={"header_row": header_idx},
            )
        except Exception:
            import pandas as pd

            df_pd = pd.read_excel(file_path, header=header_idx)
            df = pl.from_pandas(df_pd)
        return _sanitize_raw_dataframe(df)

    elif suffix == ".json":
        try:
            df = _read_json_dataframe(file_path)
            return _sanitize_raw_dataframe(df)
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_JSON_DATA",
                    "message": f"Failed to parse JSON dataset: {e}",
                    "hint": "Provide a JSON array of objects or a columnar JSON object.",
                },
            )

    elif suffix in [".jsonl", ".ndjson"]:
        df = pl.read_ndjson(file_path, infer_schema_length=2000)
        return _sanitize_raw_dataframe(df)

    elif suffix == ".parquet":
        df = pl.read_parquet(file_path)
        return _sanitize_raw_dataframe(df)

    else:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "UNSUPPORTED_FILE_TYPE",
                "message": f"Unsupported file extension '{suffix}'. Supported: CSV, TSV, TXT, XLSX, XLS, JSON, JSONL, Parquet.",
                "hint": "Upload a tabular file in CSV, TSV, Excel, JSON, or Parquet format.",
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
