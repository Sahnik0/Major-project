import csv
from pathlib import Path
from typing import Any

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


def read_raw_dataframe(file_path: Path) -> pl.DataFrame:
    """Read a CSV or XLSX file into a Polars DataFrame with encoding and delimiter sniffing."""
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        encoding = sniff_encoding(file_path)
        delimiter = sniff_delimiter(file_path, encoding)
        try:
            return pl.read_csv(
                file_path,
                separator=delimiter,
                encoding=encoding,
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
                ignore_errors=True,
                infer_schema_length=1000,
                null_values=["NA", "N/A", "null", "NULL", "", "None", "nan", "NaN"],
            )
    elif suffix in [".xlsx", ".xls"]:
        return pl.read_excel(file_path)
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
) -> tuple[list[str], list[dict[str, Any]], int]:
    """Inspect raw file columns, sample rows, and total row count."""
    df = read_raw_dataframe(file_path)
    columns = df.columns
    row_count = len(df)
    # Get up to 5 sample rows as dicts
    sample_df = df.head(5)
    sample_rows = sample_df.to_dicts()
    return columns, sample_rows, row_count
