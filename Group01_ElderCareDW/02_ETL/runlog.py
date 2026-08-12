"""ETL run log.

Every rule that changes data, every period with a missing file, and every column
an era does not have has to be recorded here. The assignment asks for automated
data quality checks, which means there must be evidence of what was checked —
not a silent fix followed by the claim that the data is clean.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

import config


@dataclass
class RunLog:
    """Collects the records of a single run."""

    run_id: str = field(
        default_factory=lambda: dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    rows: list[dict] = field(default_factory=list)

    def add(
        self,
        step: str,
        rule: str,
        detail: str,
        snapshot_date: str = "",
        target: str = "",
        rows_affected: int = 0,
    ) -> None:
        """Record one event.

        step    pipeline stage: extract / clean / transform / integrate / load
        rule    rule code such as Q1 or Q5, or a check name such as pk_unique
        detail  human-readable description — readable without opening the code
        """
        self.rows.append(
            {
                "run_id": self.run_id,
                "logged_at": dt.datetime.now().isoformat(timespec="seconds"),
                "snapshot_date": snapshot_date,
                "step": step,
                "rule": rule,
                "target": target,
                "rows_affected": int(rows_affected),
                "detail": detail,
            }
        )
        prefix = f"[{snapshot_date}] " if snapshot_date else ""
        count = f" ({rows_affected:,} rows)" if rows_affected else ""
        print(f"    - {prefix}{rule}: {detail}{count}")

    def to_frame(self) -> pd.DataFrame:
        cols = [
            "run_id",
            "logged_at",
            "snapshot_date",
            "step",
            "rule",
            "target",
            "rows_affected",
            "detail",
        ]
        if not self.rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(self.rows)[cols]

    def save_csv(self):
        """Write a CSV copy so the log can be read without opening the database."""
        config.ensure_dirs()
        path = config.LOG_DIR / f"etl_run_{self.run_id}.csv"
        self.to_frame().to_csv(path, index=False, encoding="utf-8-sig")
        return path
