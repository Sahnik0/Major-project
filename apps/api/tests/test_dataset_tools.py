import polars as pl
import pytest

from apps.api.app.services.dataset_tools import (
    TOOL_DEFINITIONS,
    discover_query_context,
    execute_column_distribution_stats,
    execute_compare_groups,
    execute_filter_and_aggregate,
    execute_inspect_raw_sample,
    execute_rank_top_bottom,
    resolve_column_name,
)
from apps.api.app.services.narrator import validate_narrative_traceability


@pytest.fixture
def sample_df():
    return pl.DataFrame({
        "BRAND": ["SXI", "SXI", "ARYN", "ARYN", "SELF"],
        "DEPARTMENT": ["JEANS", "SHIRTS", "JEANS", "TSHIRTS", "SUITS"],
        "Sale Qty": [100.0, 50.0, 80.0, 40.0, 20.0],
        "Closing Qty": [300.0, 150.0, 200.0, 100.0, 50.0],
        "Profit Amt(In-rate)": [5000.0, 2500.0, 4000.0, 1800.0, 10000.0],
        "Gross Item Sale": [15000.0, 7500.0, 12000.0, 6000.0, 25000.0],
    })


def test_tool_definitions_present():
    assert len(TOOL_DEFINITIONS) >= 6
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "rank_top_bottom" in names
    assert "filter_and_aggregate" in names
    assert "column_distribution_stats" in names
    assert "compare_groups" in names
    assert "inspect_raw_sample" in names
    assert "inventory_status_lookup" in names


def test_resolve_column_name(sample_df):
    cols = sample_df.columns
    # Exact match
    assert resolve_column_name(cols, "BRAND") == "BRAND"
    # Case-insensitive
    assert resolve_column_name(cols, "brand") == "BRAND"
    # Synonyms
    assert resolve_column_name(cols, "sales") == "Sale Qty"
    assert resolve_column_name(cols, "profit") == "Profit Amt(In-rate)"
    assert resolve_column_name(cols, "revenue") == "Gross Item Sale"
    assert resolve_column_name(cols, "stock") == "Closing Qty"


def test_execute_rank_top_bottom(sample_df):
    res = execute_rank_top_bottom(sample_df, dimension="BRAND", metric="profit", n=2, ascending=False)
    assert res["tool_name"] == "rank_top_bottom"
    assert len(res["table_rows"]) == 2
    # SELF has highest profit (10,000)
    assert res["table_rows"][0][0] == "SELF"
    assert "10,000.00" in res["table_rows"][0][1]


def test_execute_filter_and_aggregate(sample_df):
    # Sum of profit for SXI
    res = execute_filter_and_aggregate(
        sample_df, target_metric="profit", agg_func="sum", filter_column="BRAND", filter_value="SXI"
    )
    assert res["tool_name"] == "filter_and_aggregate"
    # SXI profit = 5000 + 2500 = 7500
    assert res["facts"]["result_value"] == 7500.0


def test_execute_column_distribution_stats(sample_df):
    res = execute_column_distribution_stats(sample_df, column="Sale Qty")
    assert res["tool_name"] == "column_distribution_stats"
    # Total sum = 100 + 50 + 80 + 40 + 20 = 290
    assert res["facts"]["sum"] == 290.0
    assert res["facts"]["mean"] == 58.0


def test_execute_compare_groups(sample_df):
    res = execute_compare_groups(sample_df, dimension="BRAND", entities=["SXI", "ARYN"])
    assert res["tool_name"] == "compare_groups"
    assert len(res["table_rows"]) == 2
    entities = [r[0] for r in res["table_rows"]]
    assert "SXI" in entities
    assert "ARYN" in entities


def test_execute_inspect_raw_sample(sample_df):
    res = execute_inspect_raw_sample(sample_df, search_column="DEPARTMENT", keyword="JEANS", limit=5)
    assert res["tool_name"] == "inspect_raw_sample"
    assert res["facts"]["records_matched"] == 2


def test_discover_query_context_high_cardinality_and_full_totals():
    # Create dataset with >1000 unique BRAND values to ensure no 400-cardinality cutoff drops brands
    brands = [f"BRAND_{i:04d}" for i in range(1200)] + ["PEPE", "PEPE-KIDS", "MUFTI", "JOCKEY", "BEE-VEE"]
    df_large = pl.DataFrame({
        "BRAND": brands,
        "Sale Qty": [10.0] * len(brands),
        "Profit Amt(In-rate)": [100.0] * len(brands),
    })
    ctx = discover_query_context("Total sales and profit of brand MUFTI JOCKEY and BEE-VEE", df_large)
    brand_filters = [f for f in ctx["matched_value_filters"] if f["column"] == "BRAND"]
    assert brand_filters, "Expected BRAND values to be matched even with >1200 unique values"
    matched_vals = set(brand_filters[0]["values"])
    assert {"MUFTI", "JOCKEY", "BEE-VEE"}.issubset(matched_vals)

    ctx_pepe = discover_query_context("Total sales of pepe", df_large)
    pepe_vals = set(ctx_pepe["matched_value_filters"][0]["values"])
    assert "PEPE" in pepe_vals
    assert "PEPE-KIDS" in pepe_vals

    # Verify rank_top_bottom computes grand total across ALL 1,205 entities, not just top N
    rank_res = execute_rank_top_bottom(df_large, dimension="BRAND", metric="Sale Qty", n=5, ascending=False)
    assert rank_res["facts"]["total_entities"] == len(brands)
    assert rank_res["facts"]["grand_total_across_all_entities"] == float(len(brands) * 10.0)


def test_strict_narrative_traceability_rejects_hallucinations():
    facts = {"total_sales": 615.0, "records": 1297}
    assert validate_narrative_traceability("Total sales are 615 across 1,297 records.", facts) is True
    assert validate_narrative_traceability("1. Total sales are 615.", facts) is True
    # Must reject hallucinated numbers even in small ranges (e.g. 5 or 618)
    assert validate_narrative_traceability("Total sales are 618 across 5 brands.", facts) is False
    assert validate_narrative_traceability("Total sales are 5 units.", facts) is False

