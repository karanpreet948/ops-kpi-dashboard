"""
Metrics and aggregation logic for the Operations KPI Dashboard.

This module is intentionally kept free of any Streamlit or UI code so that
every function here can be unit tested in isolation (see tests/test_metrics.py).
All functions take a pandas DataFrame (or filter parameters) and return
plain pandas objects (DataFrames, Series) or Python scalars.

Expected input schema (columns):
    case_id            : str, unique case/transaction identifier
    opened_date        : datetime-like, date the case was opened
    closed_date        : datetime-like or NaT, date the case was closed
    category           : str, business category of the case
    region             : str, region the case belongs to
    status             : str, "Open" or "Closed"
    cycle_time_days    : float or NaN, days from open to close (NaN if still open)
    is_exception       : bool, whether the case was flagged as an exception/error
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "case_id",
    "opened_date",
    "closed_date",
    "category",
    "region",
    "status",
    "cycle_time_days",
    "is_exception",
]


class DataValidationError(ValueError):
    """Raised when the input data does not match the expected schema."""


def load_data(csv_path: str) -> pd.DataFrame:
    """Load and lightly validate the operations dataset from a CSV file.

    Parses date columns, checks required columns are present, and coerces
    types defensively so that malformed rows do not crash downstream
    aggregations.

    Raises
    ------
    DataValidationError if required columns are missing.
    """
    df = pd.read_csv(csv_path)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise DataValidationError(f"Missing required columns: {missing}")

    df["opened_date"] = pd.to_datetime(df["opened_date"], errors="coerce")
    df["closed_date"] = pd.to_datetime(df["closed_date"], errors="coerce")
    df["cycle_time_days"] = pd.to_numeric(df["cycle_time_days"], errors="coerce")

    # is_exception may come in as the string "True"/"False" from CSV.
    if df["is_exception"].dtype != bool:
        df["is_exception"] = df["is_exception"].astype(str).str.strip().str.lower().map(
            {"true": True, "false": False, "1": True, "0": False}
        ).fillna(False)

    n_bad_dates = df["opened_date"].isna().sum()
    if n_bad_dates:
        logger.warning("Dropping %d rows with unparseable opened_date values.", n_bad_dates)
        df = df[df["opened_date"].notna()].copy()

    return df.reset_index(drop=True)


def filter_data(
    df: pd.DataFrame,
    start_date: Optional[pd.Timestamp] = None,
    end_date: Optional[pd.Timestamp] = None,
    categories: Optional[Iterable[str]] = None,
    regions: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Filter the dataset by open-date range, category, and region.

    Any of the filter arguments may be None/empty, in which case that
    dimension is not filtered. Returns a new DataFrame (never mutates input).
    An empty result set is a valid, non-error outcome — callers (e.g. the
    Streamlit UI) are expected to handle a zero-row result gracefully.
    """
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame(columns=REQUIRED_COLUMNS)

    out = df.copy()

    if start_date is not None:
        out = out[out["opened_date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        out = out[out["opened_date"] <= pd.Timestamp(end_date)]
    if categories:
        categories = list(categories)
        if len(categories) > 0:
            out = out[out["category"].isin(categories)]
    if regions:
        regions = list(regions)
        if len(regions) > 0:
            out = out[out["region"].isin(regions)]

    return out.reset_index(drop=True)


def compute_summary_kpis(df: pd.DataFrame) -> dict:
    """Compute the headline KPI summary row.

    Returns a dict with:
        total_volume        : total number of cases in scope
        avg_cycle_time_days : mean cycle time across CLOSED cases (NaN-safe)
        exception_rate      : fraction (0-1) of cases flagged as exceptions
        open_case_count     : number of cases still open

    Gracefully returns zeros/NaN-safe defaults on an empty DataFrame instead
    of raising, since an empty filter result is expected UI behavior.
    """
    if df is None or df.empty:
        return {
            "total_volume": 0,
            "avg_cycle_time_days": float("nan"),
            "exception_rate": float("nan"),
            "open_case_count": 0,
        }

    total_volume = len(df)
    closed = df[df["status"] == "Closed"]
    avg_cycle_time = closed["cycle_time_days"].mean() if not closed.empty else float("nan")
    exception_rate = df["is_exception"].mean()
    open_case_count = int((df["status"] == "Open").sum())

    return {
        "total_volume": total_volume,
        "avg_cycle_time_days": round(avg_cycle_time, 2) if pd.notna(avg_cycle_time) else float("nan"),
        "exception_rate": round(exception_rate, 4) if pd.notna(exception_rate) else float("nan"),
        "open_case_count": open_case_count,
    }


def compute_time_series(df: pd.DataFrame, freq: str = "W") -> pd.DataFrame:
    """Compute volume and average cycle time aggregated over time.

    Parameters
    ----------
    freq: pandas offset alias for the time bucket, e.g. "D", "W", "M".

    Returns a DataFrame indexed by period start with columns:
        volume, avg_cycle_time_days, exception_rate
    Empty input yields an empty DataFrame with the same columns (not an error).
    """
    columns = ["period", "volume", "avg_cycle_time_days", "exception_rate"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)

    grouped = df.set_index("opened_date").sort_index()
    resampled = grouped.resample(freq)

    volume = resampled["case_id"].count()
    avg_cycle_time = resampled["cycle_time_days"].mean()
    exception_rate = resampled["is_exception"].mean()

    out = pd.DataFrame(
        {
            "volume": volume,
            "avg_cycle_time_days": avg_cycle_time,
            "exception_rate": exception_rate,
        }
    ).reset_index().rename(columns={"opened_date": "period"})

    return out


def compute_exceptions_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """Compute exception counts and rates broken down by category.

    Returns a DataFrame with columns: category, total_cases, exception_count,
    exception_rate — sorted descending by exception_rate.
    """
    columns = ["category", "total_cases", "exception_count", "exception_rate"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)

    grouped = df.groupby("category").agg(
        total_cases=("case_id", "count"),
        exception_count=("is_exception", "sum"),
    )
    grouped["exception_rate"] = (grouped["exception_count"] / grouped["total_cases"]).round(4)
    grouped = grouped.reset_index().sort_values("exception_rate", ascending=False)
    return grouped.reset_index(drop=True)


def compute_breakdown_by_region(df: pd.DataFrame) -> pd.DataFrame:
    """Compute volume and average cycle time broken down by region.

    Returns a DataFrame with columns: region, total_cases, avg_cycle_time_days,
    exception_rate.
    """
    columns = ["region", "total_cases", "avg_cycle_time_days", "exception_rate"]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)

    grouped = df.groupby("region").agg(
        total_cases=("case_id", "count"),
        avg_cycle_time_days=("cycle_time_days", "mean"),
        exception_rate=("is_exception", "mean"),
    ).reset_index()
    grouped["avg_cycle_time_days"] = grouped["avg_cycle_time_days"].round(2)
    grouped["exception_rate"] = grouped["exception_rate"].round(4)
    return grouped.sort_values("total_cases", ascending=False).reset_index(drop=True)
