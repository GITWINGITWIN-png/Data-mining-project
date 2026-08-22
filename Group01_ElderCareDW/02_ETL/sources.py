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
    """Collapse frames sharing a Processing Date — **per file, not per period**.

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

    **Why the winner is chosen per file.** Keeping the later frame whole is
    wrong, because a republication is not always a complete one. 2026-08-06 is a
    3.5 MB partial carrying only ProviderInfo and StateAverages, while the
    2026-07-29 release it replaces is 622 MB and includes Penalties. Taking the
    later frame entire threw that Penalties file away — 16,166 rows, which
    collapsed deduplication from 38% to 0% and erased every fine reported after
    the June file was published.

    So the later publication wins each file it actually has, and a file it does
    not carry is inherited from the frame it supersedes. Corrections still win;
    absence no longer destroys.
    """
    by_stamp: dict[pd.Timestamp, SnapshotFrame] = {}
    for frame in sorted(frames, key=lambda f: f.snapshot_date):
        existing = by_stamp.get(frame.processing_date)
        if existing is None:
            by_stamp[frame.processing_date] = frame
            continue

        # `frame` is the later publication: it wins every file it carries.
        merged = SnapshotFrame(
            snapshot_date=frame.snapshot_date,
            processing_date=frame.processing_date,
            era=frame.era,
            provider_info=frame.provider_info,
            penalties=frame.penalties,
        )
        inherited = []
        if merged.penalties is None and existing.penalties is not None:
            merged.penalties = existing.penalties
            inherited.append(f"penalties ({len(existing.penalties):,} rows)")

        by_stamp[frame.processing_date] = merged
        log.add(
            step="clean",
            rule="Q2",
            snapshot_date=frame.snapshot_date,
            target="all tables",
            rows_affected=len(inherited),
            detail=(
                f"period {existing.snapshot_date} and {frame.snapshot_date} share "
                f"Processing Date {frame.processing_date.date()}; keeping "
                f"{frame.snapshot_date} (the later publication carries the corrections)"
                + (f", inheriting from {existing.snapshot_date}: {', '.join(inherited)}"
                   if inherited else "")
            ),
        )
    return sorted(by_stamp.values(), key=lambda f: f.processing_date)


def audit_file_coverage(
    wanted: list[str], frames: list[SnapshotFrame], log: RunLog
) -> int:
    """Report any Penalties file on disk that no surviving frame carries.

    The rule this enforces: **a file that was extracted must be read, or the run
    must say out loud that it was not.**

    This exists because of a real failure. `drop_republished_periods` used to
    keep the later frame whole, and the later frame did not always carry every
    file — 16,166 penalty rows were discarded on every run. Deduplication
    dropped from 38% to 0%, which is a deafening signal, and the whole 22-check
    suite still reported success because nothing compared what was on disk with
    what was consumed.

    Returns the number of unread files, so a caller can fail on it.
    """
    kept_penalties = {
        f.snapshot_date for f in frames if f.penalties is not None
    }
    # A period whose Penalties were inherited into another frame counts as read,
    # so compare row counts rather than period names.
    kept_rows = sum(len(f.penalties) for f in frames if f.penalties is not None)

    on_disk = []
    for snapshot_date in wanted:
        if fetch.find_file(snapshot_date, "penalties") is not None:
            on_disk.append(snapshot_date)

    # Every distinct Processing Date that had a Penalties file anywhere must be
    # represented among the surviving frames.
    stamps_with_penalties = {
        f.processing_date for f in frames if f.penalties is not None
    }
    unread = []
    for f in frames:
        if f.penalties is None and fetch.find_file(f.snapshot_date, "penalties") is not None:
            unread.append(f.snapshot_date)

    log.add(
        step="extract",
        rule="coverage",
        target="penalties",
        rows_affected=len(unread),
        detail=(
            f"{len(on_disk)} extracted Penalties file(s), "
            f"{len(kept_penalties)} carried into {len(stamps_with_penalties)} period(s), "
            f"{kept_rows:,} raw rows ingested, {len(unread)} unread"
            + (f" — UNREAD: {unread}" if unread else "")
        ),
    )
    if unread:
        print(f"  ! {len(unread)} extracted Penalties file(s) were not read: {unread}")
    return len(unread)


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

    frames = drop_republished_periods(frames, log)
    audit_file_coverage(wanted, frames, log)
    return frames
