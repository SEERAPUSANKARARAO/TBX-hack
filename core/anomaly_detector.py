"""
Statistical Anomaly Detector — Financial Outlier Detection
==========================================================
Identifies anomalous financial transactions using statistical thresholds:
    Threshold = μ_historical + 2.5 * σ_historical

When queries return transactions or payouts, this module evaluates whether
any items significantly exceed historical baselines and generates actionable
audit alerts.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from core.query_engine import QueryEngine

logger = logging.getLogger(__name__)


@dataclass
class AnomalyAlert:
    """Represents a detected financial anomaly."""
    transaction_id: Optional[str]
    vendor_name: str
    amount: float
    historical_mean: float
    historical_std: float
    threshold: float
    percentage_above_mean: float
    message: str


class AnomalyDetector:
    """
    Evaluates transactions and payouts against historical statistical baselines
    in DuckDB to flag high-value anomalies.
    """

    def __init__(self, db_path: str, z_threshold: float = 2.5):
        self.db_path = db_path
        self.z_threshold = z_threshold
        self._stats_cache: dict[str, dict] = {}

    def _load_vendor_stats(self, engine: QueryEngine) -> dict[str, dict]:
        """
        Compute baseline stats (mean, stddev, count) for each vendor across transactions.
        """
        sql = """
            SELECT
                vendor_name,
                AVG(amount) AS mean_amount,
                STDDEV_SAMP(amount) AS std_amount,
                COUNT(*) AS tx_count,
                MAX(amount) AS max_amount
            FROM transactions
            GROUP BY vendor_name
            HAVING COUNT(*) >= 2
        """
        res = engine.execute(sql)
        stats = {}
        if res.success:
            for row in res.rows:
                v_name = row[0]
                mean_val = float(row[1] or 0.0)
                std_val = float(row[2] or 0.0)
                stats[v_name.lower()] = {
                    "vendor_name": v_name,
                    "mean": mean_val,
                    "std": std_val,
                    "count": int(row[3]),
                    "max": float(row[4] or 0.0),
                    "threshold": mean_val + (self.z_threshold * std_val) if std_val > 0 else mean_val * 2.0,
                }
        return stats

    def detect_anomalies(
        self,
        query_columns: list[str],
        query_rows: list,
        tables_touched: list[str] = None,
    ) -> list[AnomalyAlert]:
        """
        Inspect query results to find any transactions that exceed statistical thresholds.

        Args:
            query_columns: Column names of the query result.
            query_rows: List of row data (tuples or lists).
            tables_touched: Tables touched by the SQL query.

        Returns:
            List of AnomalyAlert objects.
        """
        if not query_rows or not query_columns:
            return []

        # Check if query involves transactions or payouts
        touched = [t.lower() for t in (tables_touched or [])]
        if touched and not any(t in touched for t in ["transactions", "vendor_payouts", "v_vendor_spend_summary"]):
            return []

        cols_lower = [str(c).lower() for c in query_columns]

        # Identify column indices
        amount_idx = next((i for i, c in enumerate(cols_lower) if c in ["amount", "total_amount", "spend", "total_spend"]), None)
        vendor_idx = next((i for i, c in enumerate(cols_lower) if "vendor" in c or c == "name"), None)
        tx_id_idx = next((i for i, c in enumerate(cols_lower) if "transaction_id" in c or "id" in c), None)

        if amount_idx is None:
            return []

        engine = QueryEngine(self.db_path)
        stats = self._load_vendor_stats(engine)

        alerts: list[AnomalyAlert] = []

        for row in query_rows:
            raw_amount = row[amount_idx] if isinstance(row, (list, tuple)) else row.get(query_columns[amount_idx])
            try:
                amount = float(raw_amount)
            except (ValueError, TypeError):
                continue

            vendor = None
            if vendor_idx is not None:
                vendor = row[vendor_idx] if isinstance(row, (list, tuple)) else row.get(query_columns[vendor_idx])

            tx_id = None
            if tx_id_idx is not None:
                tx_id = str(row[tx_id_idx] if isinstance(row, (list, tuple)) else row.get(query_columns[tx_id_idx]))

            # If vendor is known, check against vendor stats
            if vendor and str(vendor).lower() in stats:
                v_stat = stats[str(vendor).lower()]
                threshold = v_stat["threshold"]
                mean = v_stat["mean"]
                std = v_stat["std"]

                # For small sample sizes, a large outlier distorts the standard deviation.
                # Threshold uses z_threshold, with adaptive fallback for small samples (N < 6).
                is_outlier = False
                if std > 0:
                    if amount > threshold:
                        is_outlier = True
                    elif v_stat["count"] <= 5 and amount > mean * 1.5 and amount > 5000:
                        is_outlier = True
                elif amount > mean * 1.8 and amount > 5000:
                    is_outlier = True

                if is_outlier:
                    pct_above = ((amount - mean) / mean) * 100
                    tx_label = f"Transaction #{tx_id}" if tx_id else f"Spend entry"
                    msg = (
                        f"⚠️ **Anomaly Alert**: {tx_label} (${amount:,.2f}) to **{v_stat['vendor_name']}** "
                        f"is {pct_above:,.1f}% higher than their historical average (${mean:,.2f})."
                    )
                    alerts.append(AnomalyAlert(
                        transaction_id=tx_id,
                        vendor_name=v_stat["vendor_name"],
                        amount=amount,
                        historical_mean=mean,
                        historical_std=std,
                        threshold=threshold,
                        percentage_above_mean=pct_above,
                        message=msg,
                    ))

        return alerts
