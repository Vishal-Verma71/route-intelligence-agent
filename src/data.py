"""DataCo Smart Supply Chain: load the raw Kaggle file once and keep a slim order-level table.

The raw CSV (96 MB, 180k order lines, latin-1) is not committed. ``prepare_slim`` condenses
it to one row per order with only the columns this project uses and compact dtypes, saved
as ``data/dataco_slim.parquet`` (committed, a few MB), so a fresh clone works without it.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from .config import RAW_CSV, SLIM_DATA

RAW_COLUMNS = {
    "Order Id": "order_id",
    "order date (DateOrders)": "order_date",
    "shipping date (DateOrders)": "shipping_date",
    "Shipping Mode": "mode",
    "Days for shipment (scheduled)": "scheduled_days",
    "Days for shipping (real)": "real_days",
    "Late_delivery_risk": "late",
    "Delivery Status": "delivery_status",
    "Order Status": "order_status",
    "Market": "market",
    "Order Region": "order_region",
    "Order Country": "order_country",
    "Order City": "order_city",
    "Order State": "order_state",
    "Customer Segment": "customer_segment",
    "Customer State": "customer_state",
    "Latitude": "store_lat",
    "Longitude": "store_lon",
    "Department Name": "department",
    "Category Name": "category",
    "Type": "payment_type",
    "Sales": "sales",
    "Order Item Quantity": "quantity",
}

CATEGORICAL = ["mode", "delivery_status", "order_status", "market", "order_region", "order_country",
               "order_city", "order_state", "customer_segment", "customer_state", "department",
               "category", "payment_type"]


def prepare_slim(raw_csv=RAW_CSV, out=SLIM_DATA) -> pd.DataFrame:
    """Condense the raw order-line file to one row per order and save it as parquet."""
    raw = pd.read_csv(raw_csv, encoding="latin-1", usecols=list(RAW_COLUMNS))
    raw = raw.rename(columns=RAW_COLUMNS)
    raw["order_region"] = raw["order_region"].str.strip()
    # The most valuable line in each order represents its department/category.
    raw = raw.sort_values(["order_id", "sales"], ascending=[True, False])
    first = raw.groupby("order_id", sort=False).first()
    totals = raw.groupby("order_id", sort=False).agg(sales=("sales", "sum"), quantity=("quantity", "sum"),
                                                     lines=("sales", "size"))
    df = first.drop(columns=["sales", "quantity"]).join(totals).reset_index()
    df["order_date"] = pd.to_datetime(df["order_date"])
    df["shipping_date"] = pd.to_datetime(df["shipping_date"])
    df["delay_days"] = (df["real_days"] - df["scheduled_days"]).astype("int8")
    for c in ("scheduled_days", "real_days", "late"):
        df[c] = df[c].astype("int8")
    for c in ("store_lat", "store_lon", "sales"):
        df[c] = df[c].astype("float32")
    df["quantity"] = df["quantity"].astype("int16")
    df["lines"] = df["lines"].astype("int8")
    for c in CATEGORICAL:
        df[c] = df[c].astype("category")
    df = df.sort_values("order_date").reset_index(drop=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False, compression="zstd")
    return df


@lru_cache(maxsize=1)
def _load() -> pd.DataFrame:
    if not SLIM_DATA.exists():
        if not RAW_CSV.exists():
            raise FileNotFoundError(
                f"{SLIM_DATA.name} is missing and the raw DataCo CSV was not found at {RAW_CSV}. "
                "Download it from Kaggle (shashwatwork/dataco-smart-supply-chain-for-big-data-analysis).")
        return prepare_slim()
    return pd.read_parquet(SLIM_DATA)


def load_orders() -> pd.DataFrame:
    """Order-level DataCo table. Returns a copy so callers cannot mutate the cache."""
    return _load().copy()


def dataset_summary() -> dict:
    df = _load()
    return {
        "source": "Kaggle: DataCo SMART SUPPLY CHAIN FOR BIG DATA ANALYSIS (shashwatwork)",
        "orders": int(len(df)),
        "date_from": str(df["order_date"].min().date()),
        "date_to": str(df["order_date"].max().date()),
        "late_rate": round(float(df["late"].mean()), 3),
        "late_rate_by_mode": {str(k): round(float(v), 3)
                              for k, v in df.groupby("mode", observed=True)["late"].mean().items()},
        "markets": {str(k): int(v) for k, v in df["market"].value_counts().items()},
    }


# DataCo stores country names in Spanish; these are the English names used for display.
COUNTRY_EN = {
    "Estados Unidos": "United States", "México": "Mexico", "Filipinas": "Philippines", "Singapur": "Singapore",
    "Pakistán": "Pakistan", "Japón": "Japan", "Malasia": "Malaysia", "Tailandia": "Thailand",
    "Corea del Sur": "South Korea", "Nueva Zelanda": "New Zealand", "Bangladés": "Bangladesh", "Taiwán": "Taiwan",
    "Camboya": "Cambodia", "Irak": "Iraq", "Irán": "Iran", "Turquía": "Turkey", "Afganistán": "Afghanistan",
}


def country_en(name: str) -> str:
    return COUNTRY_EN.get(name, name)


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km (vectorised)."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))
