from pathlib import Path

import polars as pl

from apps.api.app.services.ingestion import (
    inspect_raw_file,
    read_raw_dataframe,
    sniff_delimiter,
    sniff_encoding,
)


def test_sniff_delimiter(tmp_path: Path):
    comma_file = tmp_path / "comma.csv"
    comma_file.write_text("date,product,quantity\n2023-01-01,SKU1,10\n2023-01-02,SKU1,15\n")
    assert sniff_delimiter(comma_file, "utf-8") == ","

    semicolon_file = tmp_path / "semicolon.csv"
    semicolon_file.write_text("date;product;quantity\n2023-01-01;SKU1;10\n2023-01-02;SKU1;15\n")
    assert sniff_delimiter(semicolon_file, "utf-8") == ";"

    tab_file = tmp_path / "tab.csv"
    tab_file.write_text("date\tproduct\tquantity\n2023-01-01\tSKU1\t10\n2023-01-02\tSKU1\t15\n")
    assert sniff_delimiter(tab_file, "utf-8") == "\t"


def test_sniff_encoding(tmp_path: Path):
    utf8_file = tmp_path / "utf8.csv"
    utf8_file.write_text("date,product,quantity\n2023-01-01,Café,10\n", encoding="utf-8")
    assert "utf" in sniff_encoding(utf8_file).lower()

    latin_file = tmp_path / "latin1.csv"
    latin_file.write_bytes("date,product,quantity\n2023-01-01,Café,10\n".encode("iso-8859-1"))
    enc = sniff_encoding(latin_file)
    assert enc is not None


def test_read_raw_dataframe(tmp_path: Path):
    csv_file = tmp_path / "sales.csv"
    csv_file.write_text("date,product,quantity\n2023-01-01,SKU1,10\n2023-01-02,SKU2,20\n")
    df = read_raw_dataframe(csv_file)
    assert isinstance(df, pl.DataFrame)
    assert df.columns == ["date", "product", "quantity"]
    assert len(df) == 2


def test_inspect_raw_file(tmp_path: Path):
    csv_file = tmp_path / "sales.csv"
    csv_file.write_text("date,product,quantity\n2023-01-01,SKU1,10\n2023-01-02,SKU2,20\n")
    cols, sample_rows, count = inspect_raw_file(csv_file)
    assert cols == ["date", "product", "quantity"]
    assert count == 2
    assert len(sample_rows) == 2
    assert sample_rows[0]["product"] == "SKU1"
