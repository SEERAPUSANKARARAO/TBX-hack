"""
Statistical Anomaly Detector — Bank Transaction Outlier Detection
==================================================================
Identifies anomalous transactions using statistical thresholds:
    Threshold = mu_historical + 2.5 * sigma_historical

Computed per counterparty, on debits only (spend direction) — mixing
credits in would distort the baseline, since incoming payments follow
a different distribution than outgoing spend.
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
    counterparty_name: str
    amount: float
    historical_mean: float
    historical_std: float
    threshold: float
    percentage_above_mean: float
    message: str


class AnomalyDetector:
    """
    Evaluates transactions against historical per-counterparty statistical
    baselines in MySQL to flag high-value anomalies.
    """

    def __init__(self, z_threshold: float = 2.5):
        self.z_threshold = z_threshold

    def _load_counterparty_stats(self, engine: QueryEngine) -> dict[str, dict]:
        """Baseline stats (mean, stddev, count) per counterparty, debits only."""
        sql = """
            SELECT
                counterparty_name,
                AVG(transaction_amount) AS mean_amount,
                STDDEV_SAMP(transaction_amount) AS std_amount,
                COUNT(*) AS tx_count,
                MAX(transaction_amount) AS max_amount
            FROM v_transaction_enriched
            WHERE transaction_type = 'debit' AND counterparty_name IS NOT NULL
            GROUP BY counterparty_name
            HAVING COUNT(*) >= 2
        """
        res = engine.execute(sql)
        stats = {}
        if res.success:
            for row in res.rows:
                name = row[0]
                mean_val = float(row[1] or 0.0)
                std_val = float(row[2] or 0.0)
                stats[name.lower()] = {
                    "counterparty_name": name,
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
        Inspect query results for transactions exceeding statistical thresholds.
        """
        if not query_rows or not query_columns:
            return []

        touched = [t.lower() for t in (tables_touched or [])]
        relevant_tables = {"transaction", "v_transaction_enriched", "v_counterparty_spend_summary"}
        if touched and not any(t in touched for t in relevant_tables):
            return []

        cols_lower = [str(c).lower() for c in query_columns]

        amount_idx = next(
            (i for i, c in enumerate(cols_lower)
             if c in ("transaction_amount", "total_spend", "total_amount", "amount", "spend")),
            None,
        )
        counterparty_idx = next((i for i, c in enumerate(cols_lower) if "counterparty" in c), None)
        tx_id_idx = next((i for i, c in enumerate(cols_lower) if "transaction_id" in c), None)

        if amount_idx is None:
            return []

        engine = QueryEngine()
        stats = self._load_counterparty_stats(engine)

        alerts: list[AnomalyAlert] = []

        for row in query_rows:
            raw_amount = row[amount_idx] if isinstance(row, (list, tuple)) else row.get(query_columns[amount_idx])
            try:
                amount = float(raw_amount)
            except (ValueError, TypeError):
                continue

            counterparty = None
            if counterparty_idx is not None:
                counterparty = row[counterparty_idx] if isinstance(row, (list, tuple)) else row.get(query_columns[counterparty_idx])

            tx_id = None
            if tx_id_idx is not None:
                tx_id = str(row[tx_id_idx] if isinstance(row, (list, tuple)) else row.get(query_columns[tx_id_idx]))

            if counterparty and str(counterparty).lower() in stats:
                c_stat = stats[str(counterparty).lower()]
                threshold = c_stat["threshold"]
                mean = c_stat["mean"]
                std = c_stat["std"]

                is_outlier = False
                if std > 0:
                    if amount > threshold:
                        is_outlier = True
                    elif c_stat["count"] <= 5 and amount > mean * 1.5 and amount > 5000:
                        is_outlier = True
                elif amount > mean * 1.8 and amount > 5000:
                    is_outlier = True

                if is_outlier:
                    pct_above = ((amount - mean) / mean) * 100
                    tx_label = f"Transaction #{tx_id}" if tx_id else "Spend entry"
                    msg = (
                        f"⚠️ **Anomaly Alert**: {tx_label} ({amount:,.2f}) to **{c_stat['counterparty_name']}** "
                        f"is {pct_above:,.1f}% higher than their historical average ({mean:,.2f})."
                    )
                    alerts.append(AnomalyAlert(
                        transaction_id=tx_id,
                        counterparty_name=c_stat["counterparty_name"],
                        amount=amount,
                        historical_mean=mean,
                        historical_std=std,
                        threshold=threshold,
                        percentage_above_mean=pct_above,
                        message=msg,
                    ))

        return alerts
