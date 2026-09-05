"""
Query Engine — DuckDB Execution Layer
======================================
Executes validated SQL against DuckDB and returns structured results.
Handles error reporting, empty results, and execution timing.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

# Defense-in-depth: even if the SQL validator's PII guard were ever bypassed,
# a value returned under one of these column names is masked here before it
# reaches the LLM or the API response. The validator is the primary defense;
# this is the belt-and-suspenders layer.
SENSITIVE_COLUMNS = {"account_number", "utr_number"}


@dataclass
class QueryResult:
    """Structured result from a DuckDB query execution."""
    success: bool
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    execution_time_ms: float = 0.0
    sql: str = ""
    error: str | None = None
    tables_touched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to a JSON-serializable dictionary."""
        return {
            "success": self.success,
            "columns": self.columns,
            "rows": [
                {col: _serialize_value(val) for col, val in zip(self.columns, row)}
                for row in self.rows
            ],
            "row_count": self.row_count,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "sql": self.sql,
            "error": self.error,
            "tables_touched": self.tables_touched,
        }

    def summary_text(self) -> str:
        """Generate a human-readable summary of the results."""
        if not self.success:
            return f"Query failed: {self.error}"
        if self.row_count == 0:
            return "Query returned no results."

        # Build a simple text table
        lines = []
        lines.append(f"({self.row_count} row{'s' if self.row_count != 1 else ''}, "
                      f"{self.execution_time_ms:.1f}ms)")
        lines.append("")

        # Column headers
        col_widths = [max(len(str(col)), 8) for col in self.columns]
        for i, col in enumerate(self.columns):
            for row in self.rows[:20]:  # Sample first 20 rows for width calc
                col_widths[i] = max(col_widths[i], len(str(row[i])))
            col_widths[i] = min(col_widths[i], 40)  # Cap width

        header = " | ".join(
            str(col).ljust(col_widths[i]) for i, col in enumerate(self.columns)
        )
        lines.append(header)
        lines.append("-+-".join("-" * w for w in col_widths))

        # Data rows (limit to 25 for readability)
        display_rows = self.rows[:25]
        for row in display_rows:
            line = " | ".join(
                str(val).ljust(col_widths[i])[:col_widths[i]]
                for i, val in enumerate(row)
            )
            lines.append(line)

        if self.row_count > 25:
            lines.append(f"... and {self.row_count - 25} more rows")

        return "\n".join(lines)


def _serialize_value(val):
    """Convert DuckDB values to JSON-serializable types."""
    if val is None:
        return None
    if isinstance(val, (int, float, str, bool)):
        return val
    # Handle Decimal, date, datetime, etc.
    return str(val)


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names referenced in SQL (best-effort)."""
    import re
    # Match FROM/JOIN followed by table names
    pattern = r'(?:FROM|JOIN)\s+(\w+)'
    matches = re.findall(pattern, sql, re.IGNORECASE)
    return list(set(matches))


class QueryEngine:
    """
    Executes SQL queries against a DuckDB database.

    Usage:
        engine = QueryEngine("path/to/financial.duckdb")
        result = engine.execute("SELECT * FROM transactions LIMIT 5")
        print(result.summary_text())
    """

    def __init__(self, db_path: str):
        """
        Initialize the query engine with a DuckDB database path.

        Args:
            db_path: Path to the DuckDB database file.
        """
        self.db_path = db_path
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"Database not found: {db_path}. "
                f"Run 'python db/init_db.py' first."
            )

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Create a read-only connection to the database."""
        return duckdb.connect(self.db_path, read_only=True)

    def execute(self, sql: str) -> QueryResult:
        """
        Execute a SQL query and return structured results.

        Args:
            sql: The SQL query to execute.

        Returns:
            QueryResult with columns, rows, timing, and metadata.
        """
        start_time = time.perf_counter()

        try:
            con = self._get_connection()
            try:
                result = con.execute(sql)
                columns = [desc[0] for desc in result.description]
                rows = [list(row) for row in result.fetchall()]
                elapsed_ms = (time.perf_counter() - start_time) * 1000

                sensitive_idx = [i for i, c in enumerate(columns) if c.lower() in SENSITIVE_COLUMNS]
                if sensitive_idx:
                    for row in rows:
                        for i in sensitive_idx:
                            row[i] = "***MASKED***"

                return QueryResult(
                    success=True,
                    columns=columns,
                    rows=rows,
                    row_count=len(rows),
                    execution_time_ms=elapsed_ms,
                    sql=sql,
                    tables_touched=_extract_tables_from_sql(sql),
                )
            finally:
                con.close()

        except duckdb.Error as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return QueryResult(
                success=False,
                sql=sql,
                execution_time_ms=elapsed_ms,
                error=f"DuckDB error: {str(e)}",
                tables_touched=_extract_tables_from_sql(sql),
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            return QueryResult(
                success=False,
                sql=sql,
                execution_time_ms=elapsed_ms,
                error=f"Unexpected error: {str(e)}",
            )

    def get_table_info(self) -> dict:
        """
        Get metadata about all tables in the database.
        Useful for debugging and prompt building.

        Returns:
            Dict mapping table names to their column info.
        """
        con = self._get_connection()
        try:
            tables = con.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """).fetchall()

            info = {}
            for (table_name,) in tables:
                columns = con.execute("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = ?
                    ORDER BY ordinal_position
                """, [table_name]).fetchall()
                info[table_name] = [
                    {"name": col, "type": dtype, "nullable": nullable}
                    for col, dtype, nullable in columns
                ]
            return info
        finally:
            con.close()
