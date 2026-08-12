"""Reading one period from disk and cleaning it — shared by both drivers.

Kept separate from the drivers so that the dimension run and the fact run see
exactly the same cleaned input. If this lived in one driver and were copied into
the other, the two would drift apart and the facts would stop matching the
dimensions they point at.
"""

from __future__ import annotations

import pandas as pd

import clean
import columns
import config
import fetch_snapshots as fetch
from dimensions import SnapshotFrame
from runlog import RunLog


def read_snapshot(snapshot_date: str, log: RunLog) -> SnapshotFrame | None:
    """Read and clean one period. Returns None if the period is unusable.

    Periods with missing files are normal (2026-08-06 holds only 4 files and no
    Penalties). The rule is: **a missing file skips only that part, not the
    whole period** — except ProviderInfo, without which nothing can be built.
    """
    pi_path = fetch.find_file(snapshot_date, "provider_info")
    if pi_path is None:
        log.add(
            step="extract",
            rule="missing_file",
            snapshot_date=snapshot_date,
            target="provider_info",
            detail="no ProviderInfo file — skipping the whole period",
        )
        return None

    raw = pd.read_csv(pi_path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
    df, era = columns.canonicalize(raw, "provider_info", snapshot_date, log)

    # Rule Q1 has to run before anything else, or every later join is unreliable
    df["ccn"] = clean.normalize_ccn(df["ccn"])
    df["zip_code"] = clean.normalize_zip(df["zip_code"])
    clean.check_ccn_quality(df, snapshot_date, log)

    processing = clean.parse_date(df["processing_date"], "processing_date", snapshot_date, log)
    stamp = processing.dropna().iloc[0] if processing.notna().any() else pd.Timestamp(snapshot_date)
    if processing.notna().any() and processing.nunique() > 1:
        log.add(
            step="clean",
            rule="Q6",
            snapshot_date=snapshot_date,
            target="processing_date",
            rows_affected=int(processing.nunique()),
            detail=f"one period holds several Processing Dates, using the first: {stamp.date()}",
        )
    if str(stamp.date()) != snapshot_date:
        log.add(
            step="extract",
            rule="date_axis",
            snapshot_date=snapshot_date,
            target="processing_date",
            detail=(
                f"archive date ({snapshot_date}) differs from Processing Date "
                f"({stamp.date()}) — Processing Date is the time axis everywhere"
            ),
        )

    clean.report_nulls(
        df,
        ["overall_rating", "special_focus_status", "chain_name", "latitude",
         "total_nursing_turnover_pct"],
        snapshot_date,
        log,
    )

    penalties = None
    pen_path = fetch.find_file(snapshot_date, "penalties")
    if pen_path is None:
        log.add(
            step="extract",
            rule="missing_file",
            snapshot_date=snapshot_date,
            target="penalties",
            detail="no Penalties file in this period — skipping only the penalty part",
        )
    else:
        raw_pen = pd.read_csv(pen_path, dtype=str, encoding=config.CSV_ENCODING, low_memory=False)
        penalties, _ = columns.canonicalize(raw_pen, "penalties", snapshot_date, log)
        penalties["ccn"] = clean.normalize_ccn(penalties["ccn"])

    return SnapshotFrame(
        snapshot_date=snapshot_date,
        processing_date=stamp,
        era=era,
        provider_info=df,
        penalties=penalties,
    )


def drop_republished_periods(
    frames: list[SnapshotFrame], log: RunLog
) -> list[SnapshotFrame]:
    """Keep one frame per Processing Date — the one published last.

    CMS sometimes republishes a period under a new archive date: 2026-07-29 and
    2026-08-06 both carry Processing Date 2026-07-01. The declared grain is one
    row per *publication period*, so a republication is not a new row.

    This runs here, before either driver sees the frames, rather than inside one
    of them. While it lived in the fact builder alone, Fact_Facility_Monthly
    collapsed the pair correctly but Dim_Facility still walked both — and because
    both carry the same Processing Date, SCD2 closed a version at the very
    instant it opened it, leaving a row with effective_date == expiry_date that
    no point in time can ever select. `_validate_scd2` did not report it either:
    its overlap test asks for effective < previous expiry, and equality slips
    through.

    The later file wins because it carries the corrections.
    """
    by_stamp: dict[pd.Timestamp, SnapshotFrame] = {}
    dropped = []
    for frame in sorted(frames, key=lambda f: f.snapshot_date):
        existing = by_stamp.get(frame.processing_date)
        if existing is not None:
            dropped.append((existing.snapshot_date, frame.snapshot_date, frame.processing_date))
        by_stamp[frame.processing_date] = frame

    for old, new, stamp in dropped:
        log.add(
            step="clean",
            rule="Q2",
            snapshot_date=new,
            target="all tables",
            detail=(
                f"period {old} and {new} share Processing Date {stamp.date()}; "
                f"keeping {new} (the later publication carries the corrections)"
            ),
        )
    return sorted(by_stamp.values(), key=lambda f: f.processing_date)


def read_all(dates: list[str] | None, log: RunLog) -> list[SnapshotFrame]:
    """Read every extracted period (oldest first — SCD2 and dedup rely on it)."""
    available = fetch.local_snapshot_dates()
    wanted = [d for d in available if not dates or d in set(dates)]
    if not wanted:
        return []

    print(f"Using {len(wanted)} periods: {wanted}\n")
    frames = []
    for snapshot_date in wanted:
        print(f"Reading period {snapshot_date}")
        frame = read_snapshot(snapshot_date, log)
        if frame is not None:
            frames.append(frame)
    return drop_republished_periods(frames, log)
