import pytest
import polars as pl
from apps.api.app.services.dataset_tools import (
    resolve_column_name,
    execute_rank_top_bottom,
    execute_filter_and_aggregate,
    execute_column_distribution_stats,
    execute_compare_groups,
    execute_inspect_raw_sample,
    TOOL_DEFINITIONS,
)


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

