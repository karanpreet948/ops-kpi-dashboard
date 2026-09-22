"""Unit tests for src/metrics.py.

These tests use small, hand-built DataFrames (no dependency on the
generated CSV) so they run fast and deterministically in CI.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.metrics import (
    DataValidationError,
    compute_breakdown_by_region,
    compute_exceptions_by_category,
    compute_summary_kpis,
    compute_time_series,
    filter_data,
    load_data,
)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    data = [
        # case_id, opened_date, closed_date, category, region, status, cycle_time_days, is_exception
        ("OPS-1", "2025-01-01", "2025-01-03", "Billing Inquiry", "Northeast", "Closed", 2.0, False),
        ("OPS-2", "2025-01-02", "2025-01-10", "Billing Inquiry", "Northeast", "Closed", 8.0, True),
        ("OPS-3", "2025-01-05", None, "Account Setup", "West", "Open", None, False),
        ("OPS-4", "2025-02-01", "2025-02-04", "Account Setup", "West", "Closed", 3.0, False),
        ("OPS-5", "2025-02-10", "2025-02-20", "Dispute Resolution", "Midwest", "Closed", 10.0, True),
        ("OPS-6", "2025-02-15", "2025-02-16", "Dispute Resolution", "Midwest", "Closed", 1.0, False),
    ]
    df = pd.DataFrame(
        data,
        columns=[
            "case_id", "opened_date", "closed_date", "category", "region",
            "status", "cycle_time_days", "is_exception",
        ],
    )
    df["opened_date"] = pd.to_datetime(df["opened_date"])
    df["closed_date"] = pd.to_datetime(df["closed_date"])
    return df


class TestLoadData:
    def test_load_valid_csv(self, tmp_path, sample_df):
        csv_path = tmp_path / "data.csv"
        sample_df.to_csv(csv_path, index=False)

        loaded = load_data(str(csv_path))

        assert len(loaded) == len(sample_df)
        assert pd.api.types.is_datetime64_any_dtype(loaded["opened_date"])
        assert loaded["is_exception"].dtype == bool

    def test_missing_required_column_raises(self, tmp_path, sample_df):
        broken = sample_df.drop(columns=["category"])
        csv_path = tmp_path / "broken.csv"
        broken.to_csv(csv_path, index=False)

        with pytest.raises(DataValidationError):
            load_data(str(csv_path))

    def test_drops_rows_with_unparseable_dates(self, tmp_path, sample_df):
        bad = sample_df.copy()
        bad.loc[0, "opened_date"] = "not-a-date"
        csv_path = tmp_path / "bad_dates.csv"
        bad.to_csv(csv_path, index=False)

        loaded = load_data(str(csv_path))

        assert len(loaded) == len(sample_df) - 1


class TestFilterData:
    def test_filter_by_date_range(self, sample_df):
        result = filter_data(sample_df, start_date="2025-02-01", end_date="2025-02-28")
        assert len(result) == 3
        assert result["opened_date"].min() >= pd.Timestamp("2025-02-01")

    def test_filter_by_category(self, sample_df):
        result = filter_data(sample_df, categories=["Account Setup"])
        assert set(result["category"].unique()) == {"Account Setup"}
        assert len(result) == 2

    def test_filter_by_region(self, sample_df):
        result = filter_data(sample_df, regions=["Midwest"])
        assert len(result) == 2
        assert set(result["region"].unique()) == {"Midwest"}

    def test_filter_combined_can_return_empty(self, sample_df):
        result = filter_data(sample_df, categories=["Account Setup"], regions=["Midwest"])
        assert result.empty

    def test_filter_does_not_mutate_input(self, sample_df):
        original_len = len(sample_df)
        _ = filter_data(sample_df, categories=["Account Setup"])
        assert len(sample_df) == original_len

    def test_filter_empty_dataframe_returns_empty(self, sample_df):
        empty = sample_df.iloc[0:0]
        result = filter_data(empty, categories=["Account Setup"])
        assert result.empty


class TestComputeSummaryKpis:
    def test_basic_summary(self, sample_df):
        kpis = compute_summary_kpis(sample_df)
        assert kpis["total_volume"] == 6
        assert kpis["open_case_count"] == 1
        # avg cycle time over the 5 closed cases: (2+8+3+10+1)/5 = 4.8
        assert math.isclose(kpis["avg_cycle_time_days"], 4.8, rel_tol=1e-6)
        # exception rate: 2 exceptions out of 6 = 0.3333
        assert math.isclose(kpis["exception_rate"], 2 / 6, rel_tol=1e-3)

    def test_empty_dataframe_returns_safe_defaults(self, sample_df):
        empty = sample_df.iloc[0:0]
        kpis = compute_summary_kpis(empty)
        assert kpis["total_volume"] == 0
        assert kpis["open_case_count"] == 0
        assert math.isnan(kpis["avg_cycle_time_days"])
        assert math.isnan(kpis["exception_rate"])

    def test_none_input_returns_safe_defaults(self):
        kpis = compute_summary_kpis(None)
        assert kpis["total_volume"] == 0


class TestComputeTimeSeries:
    def test_monthly_aggregation_shape(self, sample_df):
        ts = compute_time_series(sample_df, freq="M")
        assert list(ts.columns) == ["period", "volume", "avg_cycle_time_days", "exception_rate"]
        # Jan and Feb -> 2 periods
        assert len(ts) == 2
        assert ts["volume"].sum() == 6

    def test_empty_input_returns_empty_with_columns(self, sample_df):
        empty = sample_df.iloc[0:0]
        ts = compute_time_series(empty)
        assert ts.empty
        assert list(ts.columns) == ["period", "volume", "avg_cycle_time_days", "exception_rate"]


class TestComputeExceptionsByCategory:
    def test_exception_rates_computed_correctly(self, sample_df):
        result = compute_exceptions_by_category(sample_df)
        dispute_row = result[result["category"] == "Dispute Resolution"].iloc[0]
        assert dispute_row["total_cases"] == 2
        assert dispute_row["exception_count"] == 1
        assert math.isclose(dispute_row["exception_rate"], 0.5)

    def test_sorted_descending_by_exception_rate(self, sample_df):
        result = compute_exceptions_by_category(sample_df)
        rates = result["exception_rate"].tolist()
        assert rates == sorted(rates, reverse=True)

    def test_empty_input(self, sample_df):
        empty = sample_df.iloc[0:0]
        result = compute_exceptions_by_category(empty)
        assert result.empty


class TestComputeBreakdownByRegion:
    def test_region_breakdown_shape_and_values(self, sample_df):
        result = compute_breakdown_by_region(sample_df)
        assert set(result["region"]) == {"Northeast", "West", "Midwest"}
        midwest = result[result["region"] == "Midwest"].iloc[0]
        assert midwest["total_cases"] == 2
        assert math.isclose(midwest["avg_cycle_time_days"], 5.5)

    def test_empty_input(self, sample_df):
        empty = sample_df.iloc[0:0]
        result = compute_breakdown_by_region(empty)
        assert result.empty
