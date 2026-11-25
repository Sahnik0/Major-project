from datetime import date
from pathlib import Path

import polars as pl

from apps.api.app.schemas.mapping import MappingConfirmationRequest
from apps.api.app.services.cleaning import (
    clean_and_regularize_dataset,
    detect_date_format_ambiguity,
)


def test_detect_date_format_ambiguity():
    # Day > 12 in first position -> dayfirst
    df_dayfirst = pl.DataFrame({"dt": ["25-01-2023", "14-02-2023", "05-03-2023"]})
    assert detect_date_format_ambiguity(df_dayfirst, "dt") == "dayfirst"

    # Day > 12 in second position -> monthfirst
    df_monthfirst = pl.DataFrame({"dt": ["01-25-2023", "02-14-2023", "03-05-2023"]})
    assert detect_date_format_ambiguity(df_monthfirst, "dt") == "monthfirst"

    # ISO format YYYY-MM-DD
    df_iso = pl.DataFrame({"dt": ["2023-01-25", "2023-02-14", "2023-03-05"]})
    assert detect_date_format_ambiguity(df_iso, "dt") == "iso"


def test_active_period_zero_filling(tmp_path: Path):
    """
    CRITICAL RULE: Fill missing periods with 0 only when the product was active
    (between its first and last sale); leading gaps before first sale are NOT zeros.
    """
    # Two products:
    # ProdA sold on 2023-01-03 and 2023-01-05
    # ProdB sold on 2023-01-01 and 2023-01-05
    # For ProdA, 2023-01-01 and 2023-01-02 must NOT exist in the regularized series!
    # For ProdA, 2023-01-04 MUST exist with quantity 0.
    raw_df = pl.DataFrame(
        {
            "order_date": ["2023-01-03", "2023-01-05", "2023-01-01", "2023-01-05"],
            "sku": ["ProdA", "ProdA", "ProdB", "ProdB"],
            "qty": [10, 20, 5, 15],
        }
    )

    req = MappingConfirmationRequest(
        mapping={"date": "order_date", "product": "sku", "quantity": "qty"},
        drop_duplicates=False,
        returns_handling="net",
        aggregation_granularity="daily",
    )

    cleaned_df, meta = clean_and_regularize_dataset(raw_df, req, dataset_id="test_active_fill")

    # ProdA checks
    proda_df = cleaned_df.filter(pl.col("product") == "ProdA").sort("date")
    proda_dates = [d.isoformat() for d in proda_df["date"].to_list()]
    assert "2023-01-01" not in proda_dates
    assert "2023-01-02" not in proda_dates
    assert proda_dates == ["2023-01-03", "2023-01-04", "2023-01-05"]

    proda_quantities = proda_df["quantity"].to_list()
    assert proda_quantities == [10.0, 0.0, 20.0]


def test_returns_handling(tmp_path: Path):
    # Test negative quantities handled as returns
    raw_df = pl.DataFrame(
        {
            "date": ["2023-01-01", "2023-01-01"],
            "product": ["SKU1", "SKU1"],
            "quantity": [15, -5],
        }
    )

    req_net = MappingConfirmationRequest(
        mapping={"date": "date", "product": "product", "quantity": "quantity"},
        returns_handling="net",
    )
    cleaned_net, _ = clean_and_regularize_dataset(raw_df, req_net, dataset_id="test_returns_net")
    assert cleaned_net["quantity"].to_list() == [10.0]  # 15 - 5
    assert cleaned_net["gross_quantity"].to_list() == [15.0]
    assert cleaned_net["returns_quantity"].to_list() == [5.0]


def test_duplicates_handling():
    raw_df = pl.DataFrame(
        {
            "date": ["2023-01-01", "2023-01-01", "2023-01-02"],
            "product": ["SKU1", "SKU1", "SKU1"],
            "quantity": [10, 10, 20],
        }
    )

    # When drop_duplicates is False: quantities sum up on aggregation
    req_no_drop = MappingConfirmationRequest(
        mapping={"date": "date", "product": "product", "quantity": "quantity"},
        drop_duplicates=False,
    )
    cleaned, meta = clean_and_regularize_dataset(raw_df, req_no_drop, dataset_id="test_dup_keep")
    assert meta["duplicate_count"] == 1
    # Aggregated on same date -> 10 + 10 = 20
    assert cleaned.filter(pl.col("date") == date(2023, 1, 1))["quantity"][0] == 20.0

    # When drop_duplicates is True: duplicate row is dropped before aggregation
    req_drop = MappingConfirmationRequest(
        mapping={"date": "date", "product": "product", "quantity": "quantity"},
        drop_duplicates=True,
    )
    cleaned_drop, _ = clean_and_regularize_dataset(raw_df, req_drop, dataset_id="test_dup_drop")
    assert cleaned_drop.filter(pl.col("date") == date(2023, 1, 1))["quantity"][0] == 10.0
