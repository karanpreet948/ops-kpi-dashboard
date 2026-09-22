"""
Synthetic operations data generator.

Generates a realistic, fully synthetic CSV of operational case/transaction
records for use in the Operations KPI Dashboard portfolio project.

No real data, no real PII, no real company logic of any kind is used here.
Every value is produced by a seeded random-number generator so the output
is reproducible.

Usage:
    python scripts/generate_synthetic_data.py
    python scripts/generate_synthetic_data.py --rows 2000 --seed 42 --out data/synthetic_operations_data.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CATEGORIES = [
    "Account Setup",
    "Billing Inquiry",
    "Documentation Review",
    "Eligibility Check",
    "Service Request",
    "Dispute Resolution",
    "Data Correction",
    "Renewal Processing",
]

REGIONS = ["Northeast", "Southeast", "Midwest", "Southwest", "West"]

STATUSES = ["Closed", "Closed", "Closed", "Closed", "Open"]  # mostly closed, some still open

# Relative volume weighting per category (some categories are simply more common).
CATEGORY_WEIGHTS = np.array([0.16, 0.18, 0.10, 0.12, 0.16, 0.08, 0.10, 0.10])

# Baseline mean cycle time (in days) per category — some categories are
# inherently slower to resolve than others.
CATEGORY_BASE_CYCLE_DAYS = {
    "Account Setup": 2.5,
    "Billing Inquiry": 3.0,
    "Documentation Review": 6.0,
    "Eligibility Check": 4.5,
    "Service Request": 3.5,
    "Dispute Resolution": 9.0,
    "Data Correction": 2.0,
    "Renewal Processing": 5.0,
}

# Baseline exception rate per category (probability a record is flagged
# as an exception / processing error).
CATEGORY_BASE_EXCEPTION_RATE = {
    "Account Setup": 0.04,
    "Billing Inquiry": 0.07,
    "Documentation Review": 0.09,
    "Eligibility Check": 0.06,
    "Service Request": 0.05,
    "Dispute Resolution": 0.14,
    "Data Correction": 0.10,
    "Renewal Processing": 0.05,
}


def _seasonal_volume_multiplier(month: int) -> float:
    """Return a multiplier that creates a seasonal volume pattern.

    Simulates a business that gets busier toward year-end (Q4 renewal /
    open-enrollment style peak) and has a summer lull.
    """
    seasonal = {
        1: 1.05, 2: 0.95, 3: 1.00, 4: 0.95, 5: 0.90, 6: 0.85,
        7: 0.80, 8: 0.85, 9: 0.95, 10: 1.15, 11: 1.35, 12: 1.25,
    }
    return seasonal.get(month, 1.0)


def generate_dataset(n_rows: int, seed: int, start_date: dt.date, days_span: int) -> pd.DataFrame:
    """Generate a synthetic operations dataset.

    Parameters
    ----------
    n_rows: target number of records to generate (approximate; seasonal
        weighting means the realized count of "opened" records is drawn
        row-by-row so the exact count is deterministic given the seed).
    seed: RNG seed for full reproducibility.
    start_date: first possible "opened_date" in the dataset.
    days_span: number of days the dataset should span (~365 for 12 months).

    Returns
    -------
    A pandas DataFrame with columns:
        case_id, opened_date, closed_date, category, region, status,
        cycle_time_days, is_exception
    """
    rng = np.random.default_rng(seed)

    # Build a day-by-day sampling weight so that record "opened_date" values
    # cluster according to the seasonal pattern instead of being uniform.
    all_days = [start_date + dt.timedelta(days=i) for i in range(days_span)]
    day_weights = np.array([_seasonal_volume_multiplier(d.month) for d in all_days], dtype=float)
    # Add mild day-of-week effect: fewer cases open on weekends.
    dow_multiplier = np.array([0.4 if d.weekday() >= 5 else 1.0 for d in all_days])
    day_weights = day_weights * dow_multiplier
    day_weights = day_weights / day_weights.sum()

    opened_day_idx = rng.choice(len(all_days), size=n_rows, p=day_weights)
    opened_dates = [all_days[i] for i in opened_day_idx]

    categories = rng.choice(CATEGORIES, size=n_rows, p=CATEGORY_WEIGHTS)
    regions = rng.choice(REGIONS, size=n_rows)

    records = []
    for i in range(n_rows):
        category = categories[i]
        region = regions[i]
        opened_date = opened_dates[i]

        base_cycle = CATEGORY_BASE_CYCLE_DAYS[category]
        # Lognormal-ish noise so cycle time is right-skewed (a realistic shape
        # for "time to resolve" data) with an occasional long-tail outlier.
        cycle_time = rng.gamma(shape=2.2, scale=base_cycle / 2.2)

        # Inject a small number of extreme outliers (e.g. escalations stuck
        # in a queue) to make the charts more realistic.
        if rng.random() < 0.015:
            cycle_time += rng.uniform(20, 45)

        is_exception = rng.random() < CATEGORY_BASE_EXCEPTION_RATE[category]

        # Most records are closed; a fraction remain open (no closed_date yet),
        # weighted toward records opened more recently.
        days_since_open = (all_days[-1] - opened_date).days
        prob_still_open = max(0.02, 0.5 - 0.03 * days_since_open) if days_since_open < 15 else 0.02
        still_open = rng.random() < prob_still_open

        if still_open:
            closed_date = pd.NaT
            status = "Open"
            cycle_time_days = np.nan
        else:
            closed_date = opened_date + dt.timedelta(days=round(cycle_time, 1))
            status = "Closed"
            cycle_time_days = round(cycle_time, 2)

        records.append(
            {
                "case_id": f"OPS-{100000 + i}",
                "opened_date": opened_date,
                "closed_date": closed_date,
                "category": category,
                "region": region,
                "status": status,
                "cycle_time_days": cycle_time_days,
                "is_exception": bool(is_exception),
            }
        )

    df = pd.DataFrame.from_records(records)
    df = df.sort_values("opened_date").reset_index(drop=True)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic operations KPI data.")
    parser.add_argument("--rows", type=int, default=2200, help="Number of synthetic records to generate.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    parser.add_argument(
        "--out",
        type=str,
        default=str(Path(__file__).resolve().parent.parent / "data" / "synthetic_operations_data.csv"),
        help="Output CSV path.",
    )
    parser.add_argument("--days-span", type=int, default=365, help="Number of days the dataset should span.")
    args = parser.parse_args()

    end_date = dt.date(2025, 9, 21)
    start_date = end_date - dt.timedelta(days=args.days_span)

    logger.info("Generating %d synthetic records (seed=%d) spanning %d days...", args.rows, args.seed, args.days_span)
    df = generate_dataset(n_rows=args.rows, seed=args.seed, start_date=start_date, days_span=args.days_span)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    logger.info("Wrote %d rows to %s", len(df), out_path)
    logger.info("Exception rate: %.2f%%", 100 * df["is_exception"].mean())
    logger.info("Closed records: %d / %d", (df["status"] == "Closed").sum(), len(df))


if __name__ == "__main__":
    main()
