"""
Operations KPI Dashboard — Streamlit application.

A representative, portfolio-grade dashboard demonstrating the kind of
operations-KPI reporting a Senior Business Analyst would build for
stakeholders: case/transaction volume trends, cycle-time performance,
exception/error rates, and category/region breakdowns.

All data is synthetic (see scripts/generate_synthetic_data.py). This app
makes NO external network calls — it reads a local CSV file only.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from src.metrics import (
    DataValidationError,
    compute_breakdown_by_region,
    compute_exceptions_by_category,
    compute_summary_kpis,
    compute_time_series,
    filter_data,
    load_data,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent / "data" / "synthetic_operations_data.csv"

st.set_page_config(
    page_title="Operations KPI Dashboard",
    page_icon="\U0001F4CA",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def get_data(path: str) -> pd.DataFrame:
    """Load the operations dataset once per session (cached)."""
    return load_data(path)


def main() -> None:
    st.title("Operations KPI Dashboard")
    st.caption(
        "A portfolio demonstration dashboard for operations/case-processing KPI reporting. "
        "All data shown is synthetic."
    )

    # ---- Load data (with graceful error handling) -----------------------
    if not DATA_PATH.exists():
        st.error(
            f"Data file not found at `{DATA_PATH}`. "
            "Run `python scripts/generate_synthetic_data.py` first to generate it."
        )
        st.stop()

    try:
        df = get_data(str(DATA_PATH))
    except DataValidationError as exc:
        logger.exception("Failed to load/validate operations data.")
        st.error(f"The data file is malformed and could not be loaded: {exc}")
        st.stop()
    except Exception:  # noqa: BLE001 - surface unexpected errors safely to the user
        logger.exception("Unexpected error while loading operations data.")
        st.error("An unexpected error occurred while loading the data. Check the application logs.")
        st.stop()

    if df.empty:
        st.warning("The dataset is empty. Nothing to display.")
        st.stop()

    # ---- Sidebar filters --------------------------------------------------
    st.sidebar.header("Filters")

    min_date = df["opened_date"].min().date()
    max_date = df["opened_date"].max().date()

    date_range = st.sidebar.date_input(
        "Opened date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    # date_input can return a single date while the user is still picking
    # the second one — guard against that instead of crashing.
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    else:
        start_date, end_date = min_date, max_date

    all_categories = sorted(df["category"].unique().tolist())
    selected_categories = st.sidebar.multiselect(
        "Category", options=all_categories, default=all_categories
    )

    all_regions = sorted(df["region"].unique().tolist())
    selected_regions = st.sidebar.multiselect(
        "Region", options=all_regions, default=all_regions
    )

    filtered = filter_data(
        df,
        start_date=pd.Timestamp(start_date),
        end_date=pd.Timestamp(end_date),
        categories=selected_categories,
        regions=selected_regions,
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(f"{len(filtered):,} of {len(df):,} records match the current filters.")

    if filtered.empty:
        st.warning(
            "No records match the selected filters. Try widening the date range "
            "or selecting more categories/regions."
        )
        st.stop()

    # ---- KPI summary row ----------------------------------------------
    kpis = compute_summary_kpis(filtered)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Case Volume", f"{kpis['total_volume']:,}")
    avg_cycle = kpis["avg_cycle_time_days"]
    col2.metric("Avg. Cycle Time (days)", f"{avg_cycle:.1f}" if pd.notna(avg_cycle) else "N/A")
    exc_rate = kpis["exception_rate"]
    col3.metric("Exception Rate", f"{exc_rate * 100:.1f}%" if pd.notna(exc_rate) else "N/A")
    col4.metric("Open Cases", f"{kpis['open_case_count']:,}")

    st.markdown("---")

    # ---- Time series: volume and cycle time ----------------------------
    st.subheader("Volume & Cycle Time Over Time")
    freq_label = st.radio("Aggregate by", ["Day", "Week", "Month"], index=1, horizontal=True)
    freq_map = {"Day": "D", "Week": "W", "Month": "ME"}
    ts = compute_time_series(filtered, freq=freq_map[freq_label])

    if ts.empty:
        st.info("Not enough data to build a time series for the current filters.")
    else:
        fig_ts = px.line(
            ts,
            x="period",
            y=["volume", "avg_cycle_time_days"],
            labels={"period": "Period", "value": "Value", "variable": "Metric"},
            title=None,
        )
        fig_ts.update_layout(legend_title_text="Metric", hovermode="x unified")
        st.plotly_chart(fig_ts, use_container_width=True)

    st.markdown("---")

    # ---- Exceptions by category + region breakdown side by side -------
    left, right = st.columns(2)

    with left:
        st.subheader("Exceptions by Category")
        exc_by_cat = compute_exceptions_by_category(filtered)
        if exc_by_cat.empty:
            st.info("No category data available for the current filters.")
        else:
            fig_bar = px.bar(
                exc_by_cat,
                x="category",
                y="exception_rate",
                hover_data=["total_cases", "exception_count"],
                labels={"category": "Category", "exception_rate": "Exception Rate"},
            )
            fig_bar.update_layout(yaxis_tickformat=".0%", xaxis_tickangle=-30)
            st.plotly_chart(fig_bar, use_container_width=True)

    with right:
        st.subheader("Breakdown by Region")
        region_breakdown = compute_breakdown_by_region(filtered)
        if region_breakdown.empty:
            st.info("No region data available for the current filters.")
        else:
            fig_region = px.bar(
                region_breakdown,
                x="region",
                y="total_cases",
                hover_data=["avg_cycle_time_days", "exception_rate"],
                labels={"region": "Region", "total_cases": "Case Volume"},
            )
            st.plotly_chart(fig_region, use_container_width=True)

    st.markdown("---")
    with st.expander("View filtered raw data"):
        st.dataframe(filtered, use_container_width=True)


if __name__ == "__main__":
    main()
