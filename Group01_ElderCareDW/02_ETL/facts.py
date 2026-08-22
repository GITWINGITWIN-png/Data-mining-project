"""Transform step — build both fact tables.

Fact_Facility_Monthly  periodic snapshot, one row per facility per published period
Fact_Penalty_Event     transaction, one row per penalty ever imposed

The two cannot be merged: one has a row every period whether or not anything
happened, the other only when an event occurred. Putting them in one table would
either force zeros into quiet periods or duplicate the status row when a period
holds two penalties, double counting nurse hours and beds immediately.
"""

from __future__ import annotations

import pandas as pd

import clean
from dimensions import SnapshotFrame
from runlog import RunLog

UNKNOWN_KEY = -1
INDEPENDENT_CHAIN_KEY = 0

# Rule Q4 — plausibility bounds. Rows outside them are flagged, never deleted.
OCCUPANCY_BOUNDS = (0.0, 1.2)      # 105% happens for real with temporary beds
TURNOVER_BOUNDS = (0.0, 200.0)     # exclusive at 0: a true 0% is a reporting artefact

# Rule Q8 — if a period's median turnover is below this it is stored as a
# proportion (0.52) rather than a percentage (52.0) and has to be scaled
TURNOVER_PROPORTION_THRESHOLD = 1.5


# =====================================================================
#  The single shared SCD2 lookup
# =====================================================================
def lookup_facility_key(
    dim_facility: pd.DataFrame,
    ccn: pd.Series,
    as_of: pd.Timestamp,
    log: RunLog,
    context: str,
) -> pd.Series:
    """Resolve CCN to the facility_key valid at `as_of`.

    This is the one place the SCD2 rule is written down:
        effective_date <= as_of < expiry_date
    The design document is explicit that this must live in a single function
    rather than being repeated as a predicate in every query.

    Facts dated before a facility's first known version are clamped to that
    first version rather than sent to the Unknown row. Otherwise most of
    Fact_Penalty_Event would point at Unknown, since penalties reach back
    further than the oldest snapshot we loaded.
    """
    dim = dim_facility[dim_facility["facility_key"] != UNKNOWN_KEY]

    valid = dim[
        (dim["effective_date"] <= as_of)
        & (dim["expiry_date"].isna() | (as_of < dim["expiry_date"]))
    ]
    mapping = dict(zip(valid["ccn"], valid["facility_key"]))
    keys = ccn.map(mapping)

    # Clamp anything earlier than the first known version
    unresolved = keys.isna()
    if unresolved.any():
        earliest = (
            dim.sort_values("effective_date")
            .drop_duplicates(subset="ccn", keep="first")
            .set_index("ccn")["facility_key"]
        )
        clamped = ccn[unresolved].map(earliest)
        keys.loc[unresolved] = clamped
        n_clamped = int(clamped.notna().sum())
        if n_clamped:
            log.add(
                step="integrate",
                rule="scd2_lookup",
                target=context,
                rows_affected=n_clamped,
                detail=(
                    f"dated before the first known version at {as_of.date()}, "
                    f"clamped to the earliest version"
                ),
            )

    missing = int(keys.isna().sum())
    if missing:
        log.add(
            step="integrate",
            rule="scd2_lookup",
            target=context,
            rows_affected=missing,
            detail=f"CCN absent from Dim_Facility, pointed at Unknown ({missing / len(ccn):.2%})",
        )
    return keys.fillna(UNKNOWN_KEY).astype("int64")


# =====================================================================
#  Other foreign keys
# =====================================================================
def _geography_key(df: pd.DataFrame, dim_geography: pd.DataFrame) -> pd.Series:
    """Match on (zip_code, city, state_code), the natural key of the dimension.

    The probe is built by the same function the dimension was built with, so the
    two sides cannot drift apart — see `clean.geography_key_columns`.
    """
    probe = clean.geography_key_columns(df)
    dim = dim_geography[["zip_code", "city", "state_code", "geography_key"]]
    merged = probe.merge(dim, on=["zip_code", "city", "state_code"], how="left")
    return merged["geography_key"].fillna(UNKNOWN_KEY).astype("int64").set_axis(df.index)


def _ownership_key(df: pd.DataFrame, dim_ownership: pd.DataFrame) -> pd.Series:
    mapping = dict(zip(dim_ownership["ownership_type"], dim_ownership["ownership_key"]))
    return (
        clean.normalize_text(df["ownership_type"])
        .map(mapping)
        .fillna(UNKNOWN_KEY)
        .astype("int64")
    )


def _chain_key(df: pd.DataFrame, dim_chain: pd.DataFrame, era_has_chain: bool) -> pd.Series:
    """Blank chain means Independent, but only when the era actually has the column.

    In the 2019 era there is no chain column at all, so every row points at
    Unknown: not knowing is not the same as knowing there is no chain.
    """
    if not era_has_chain:
        return pd.Series(UNKNOWN_KEY, index=df.index, dtype="int64")

    mapping = dict(zip(dim_chain["chain_name"], dim_chain["chain_key"]))
    names = clean.normalize_text(df["chain_name"], upper=True)
    keys = names.map(mapping)
    keys = keys.where(names.notna(), INDEPENDENT_CHAIN_KEY)   # blank -> Independent
    return keys.fillna(UNKNOWN_KEY).astype("int64")


def _date_key(dates: pd.Series) -> pd.Series:
    """YYYYMMDD as an integer; anything unparsed becomes the Unknown date key."""
    keys = pd.to_datetime(dates, errors="coerce").dt.strftime("%Y%m%d")
    return pd.to_numeric(keys, errors="coerce").fillna(UNKNOWN_KEY).astype("int64")


# =====================================================================
#  Fact 1 — periodic snapshot
# =====================================================================
def build_fact_facility_monthly(
    frames: list[SnapshotFrame], dims: dict[str, pd.DataFrame], log: RunLog
) -> pd.DataFrame:
    """One row per facility per published period.

    Grain: one row = one CCN as of one CMS Processing Date.
    """
    pieces = []

    for frame in sorted(frames, key=lambda f: f.processing_date):
        df = frame.provider_info
        stamp = frame.processing_date

        # The grain is one row per (period, CCN), so a period that repeats a CCN
        # would break the primary key. Rule Q1 has already logged the duplicate;
        # keep the first row, the same choice Dim_Facility makes.
        if df["ccn"].duplicated().any():
            n = int(df["ccn"].duplicated().sum())
            df = df.drop_duplicates(subset="ccn", keep="first")
            log.add(
                step="clean",
                rule="Q2",
                snapshot_date=frame.snapshot_date,
                target="Fact_Facility_Monthly",
                rows_affected=n,
                detail="CCN repeated inside one period — kept the first row to hold the grain",
            )

        beds = clean.to_number(df["certified_beds"])
        residents = clean.to_number(df["avg_residents_per_day"])
        turnover = _fix_turnover_units(df, frame, log)

        # M2 resident_days = average residents per day x days in the period.
        # "Days in the period" is read as the calendar month of the Processing
        # Date, which is the finest period CMS publishes. This is still an open
        # question in the design document, and M2 weights M1/M3/M4 and divides
        # M9, so changing it changes those four measures too.
        days_in_period = stamp.days_in_month

        fact = pd.DataFrame({
            "snapshot_date_key": _date_key(pd.Series([stamp] * len(df), index=df.index)),
            "facility_key": lookup_facility_key(
                dims["Dim_Facility"], df["ccn"], stamp, log, "Fact_Facility_Monthly"
            ),
            "geography_key": _geography_key(df, dims["Dim_Geography"]),
            "ownership_key": _ownership_key(df, dims["Dim_Ownership"]),
            "chain_key": _chain_key(df, dims["Dim_Chain"], frame.era != "2019"),
            "ccn": df["ccn"],
            "certified_beds": beds,
            "avg_residents_per_day": residents,
            "resident_days": residents * days_in_period,
            "reported_total_nurse_hprd": clean.to_number(df["reported_total_nurse_hprd"]),
            "reported_rn_hprd": clean.to_number(df["reported_rn_hprd"]),
            "total_nursing_turnover_pct": turnover,
            "overall_rating": clean.to_number(df["overall_rating"]),
            "staffing_rating": clean.to_number(df["staffing_rating"]),
            "health_inspection_rating": clean.to_number(df["health_inspection_rating"]),
            "cycle1_total_deficiencies": clean.to_number(df["cycle1_total_deficiencies"]),
        })
        fact["is_suspect"] = _flag_suspect(fact, frame.snapshot_date, log)
        pieces.append(fact)

        log.add(
            step="transform",
            rule="build_fact",
            snapshot_date=frame.snapshot_date,
            target="Fact_Facility_Monthly",
            rows_affected=len(fact),
            detail=(
                f"period {stamp.date()}: {len(fact):,} rows, "
                f"{days_in_period} days used for resident_days, "
                f"{int(fact['is_suspect'].sum()):,} flagged suspect"
            ),
        )

    out = pd.concat(pieces, ignore_index=True)
    log.add(
        step="transform",
        rule="build_fact",
        target="Fact_Facility_Monthly",
        rows_affected=len(out),
        detail=f"{out['snapshot_date_key'].nunique()} periods, {out['ccn'].nunique():,} CCN",
    )
    return out


def _fix_turnover_units(
    df: pd.DataFrame, frame: SnapshotFrame, log: RunLog
) -> pd.Series:
    """Rule Q8 — turnover is a percentage in some periods and a proportion in others.

    Decided per period from the median, then recorded. Never applied blindly:
    scaling an already-correct column by 100 is silent and disastrous.
    """
    turnover = clean.to_number(df["total_nursing_turnover_pct"])
    median = turnover.median()
    if pd.isna(median):
        log.add(
            step="clean",
            rule="Q8",
            snapshot_date=frame.snapshot_date,
            target="total_nursing_turnover_pct",
            detail="column absent or entirely null in this era — no scaling applied",
        )
        return turnover

    if median < TURNOVER_PROPORTION_THRESHOLD:
        log.add(
            step="clean",
            rule="Q8",
            snapshot_date=frame.snapshot_date,
            target="total_nursing_turnover_pct",
            rows_affected=int(turnover.notna().sum()),
            detail=f"median {median:.3f} looks like a proportion — multiplied by 100",
        )
        return turnover * 100

    log.add(
        step="clean",
        rule="Q8",
        snapshot_date=frame.snapshot_date,
        target="total_nursing_turnover_pct",
        rows_affected=int(turnover.notna().sum()),
        detail=f"median {median:.2f} is already a percentage — left as is",
    )
    return turnover


def _flag_suspect(fact: pd.DataFrame, snapshot_date: str, log: RunLog) -> pd.Series:
    """Rule Q4 — flag implausible rows, never drop them.

    Some outliers are real (105% occupancy happens with temporary beds), and the
    dashboard gets a switch to include or exclude them, which also makes the
    imperfection visible instead of hidden.
    """
    beds = fact["certified_beds"]
    occupancy = fact["avg_residents_per_day"] / beds.where(beds > 0)
    turnover = fact["total_nursing_turnover_pct"]

    bad_occupancy = occupancy.notna() & (
        (occupancy < OCCUPANCY_BOUNDS[0]) | (occupancy > OCCUPANCY_BOUNDS[1])
    )
    bad_beds = beds.notna() & (beds <= 0)
    bad_turnover = turnover.notna() & (
        (turnover <= TURNOVER_BOUNDS[0]) | (turnover > TURNOVER_BOUNDS[1])
    )

    for name, mask in [
        ("occupancy outside [0, 1.2]", bad_occupancy),
        ("certified_beds <= 0", bad_beds),
        ("turnover outside (0, 200]", bad_turnover),
    ]:
        n = int(mask.sum())
        if n:
            log.add(
                step="clean",
                rule="Q4",
                snapshot_date=snapshot_date,
                target="is_suspect",
                rows_affected=n,
                detail=f"{name} — flagged, not deleted",
            )
    return (bad_occupancy | bad_beds | bad_turnover).astype(bool)


# =====================================================================
#  Fact 2 — transaction
# =====================================================================
def build_fact_penalty_event(
    frames: list[SnapshotFrame], dims: dict[str, pd.DataFrame], log: RunLog
) -> pd.DataFrame:
    """One row per penalty ever imposed, accumulated across every period.

    The Penalties file is a rolling 3-year window, so a single penalty is
    reported again in up to 36 periods. Deduplication is therefore the whole
    job of this function, and it uses **one** identity for every era (rule Q2):

        ccn | penalty_date | penalty_type | fine_amount | occurrence

    `occurrence` is the row's position inside its own identical group, which is
    what keeps genuinely distinct penalties apart: one facility can be fined
    several times on the same day for the same amount (CCN 015060 was fined
    $5,782 three times on 2025-05-09), and the natural key without it would
    merge 286 real fines, 2.09%, worth $2.66M.

    Why not key on Fine ID: it exists only from period 202606 onward and is
    blank on every Payment Denial row. Keying on it *where available* and on the
    natural key elsewhere gives the same physical penalty two different
    identities depending on which period it was first seen in, so every penalty
    visible both before and after 202606 is stored twice — measured at 12,189
    surplus rows and $382M of double-counted fines. Fine ID is therefore carried
    as a descriptive attribute and used to *audit* this key, never to form it.
    """
    pieces = []
    for frame in sorted(frames, key=lambda f: f.processing_date):
        if frame.penalties is None:
            continue
        pen = frame.penalties
        df = pd.DataFrame({
            "ccn": pen["ccn"],
            "penalty_date": clean.parse_date(
                pen["penalty_date"], "penalty_date", frame.snapshot_date, log
            ),
            "penalty_type": clean.normalize_text(pen["penalty_type"]),
            "fine_id": clean.normalize_text(pen["fine_id"]) if "fine_id" in pen else pd.NA,
            "fine_amount_usd": clean.to_number(pen["fine_amount_usd"]),
            "payment_denial_days": _clean_denial_days(
                clean.to_number(pen["payment_denial_days"]), frame.snapshot_date, log
            ),
            "_snapshot": frame.snapshot_date,
            "_stamp": frame.processing_date,
            "_era": frame.era,
        })
        # Occurrence number inside an identical group, so two genuinely separate
        # penalties of the same type on the same day stay distinct rows.
        #
        # The amount is deliberately **not** part of this grouping, and must
        # stay out of it to match `_natural_key`. CMS revises fine amounts after
        # publication — Fine ID 24224 appears as $6,776 in one snapshot and
        # $10,425 in a later one — so grouping on the amount makes every
        # revision look like a brand new penalty.
        df["_occurrence"] = df.groupby(
            ["ccn", "penalty_date", "penalty_type"], dropna=False
        ).cumcount()
        pieces.append(df)

    if not pieces:
        log.add(
            step="transform",
            rule="build_fact",
            target="Fact_Penalty_Event",
            detail="no period carries a Penalties file — table left empty",
        )
        return _empty_penalty_fact()

    allrows = pd.concat(pieces, ignore_index=True)
    before = len(allrows)

    allrows["_dedup_key"] = _natural_key(allrows)

    # Where CMS supplies a Fine ID, that ID is the identity and the natural key
    # is only a stand-in for the eras that predate it. Folding every sighting of
    # one Fine ID onto a single key catches the three things the natural key
    # cannot see, because each of them changes a field the key is built from:
    #   * CMS restates the penalty date  (Fine ID 137711 moved 2026-01-07 -> 02-27)
    #   * a facility is re-certified under a new CCN and its fines follow it
    #     (146201 -> 14E812, three penalties in 2024)
    #   * one ID covers two same-day rows, which the occurrence counter splits
    # Twelve rows in 79,821 — small, but they are wrong rather than uncertain,
    # and leaving them would keep the Fine ID audit permanently red for a reason
    # nobody would remember.
    with_id = allrows["fine_id"].notna() & (allrows["fine_id"].astype("string") != "")
    if with_id.any():
        canonical = allrows.loc[with_id].groupby("fine_id")["_dedup_key"].transform("first")
        folded = int((canonical != allrows.loc[with_id, "_dedup_key"]).sum())
        allrows.loc[with_id, "_dedup_key"] = canonical
        if folded:
            log.add(
                step="integrate",
                rule="Q2",
                target="Fact_Penalty_Event",
                rows_affected=folded,
                detail=(
                    f"{folded} sightings shared a Fine ID but differed in CCN, date or "
                    "occurrence; folded onto the Fine ID's identity"
                ),
            )

    # Fine ID is not part of the identity, but an event first seen in a period
    # before 202606 would otherwise lose the ID that CMS assigned to it later.
    # Carry the first non-null ID of each event onto every sighting of it, so the
    # kept row keeps the CMS identifier as an attribute.
    filled = allrows.groupby("_dedup_key")["fine_id"].transform(
        lambda s: s.dropna().iloc[0] if s.notna().any() else pd.NA
    )
    backfilled = int((filled.notna() & allrows["fine_id"].isna()).sum())
    allrows["fine_id"] = filled
    allrows["fine_id_source"] = (
        allrows["fine_id"].notna().map({True: "fine_id", False: "natural_key"})
    )

    # Keep the LAST sighting of each event. The amount is the field CMS revises,
    # and the most recently published figure is the corrected one — it is also
    # what the facility's own CMS page shows. The time axis is unaffected:
    # analysis reads `penalty_date`, the day the penalty was issued, not the
    # period that happened to publish it.
    allrows = allrows.sort_values(["_stamp", "ccn", "penalty_date"])
    revised = _count_revised_amounts(allrows, log)
    deduped = allrows.drop_duplicates(subset="_dedup_key", keep="last").copy()

    if backfilled:
        log.add(
            step="integrate",
            rule="Q2",
            target="Fact_Penalty_Event",
            rows_affected=backfilled,
            detail=(
                "sightings that gained a Fine ID from a later period "
                "(the event predates the 202606 release that added the column)"
            ),
        )
    _audit_key_against_fine_id(allrows, log)

    log.add(
        step="clean",
        rule="Q2",
        target="Fact_Penalty_Event",
        rows_affected=before - len(deduped),
        detail=(
            f"{before:,} rows across periods reduced to {len(deduped):,} events "
            f"({(before - len(deduped)) / before:.1%} were the rolling window repeating itself)"
        ),
    )
    by_source = deduped["fine_id_source"].value_counts().to_dict()
    log.add(
        step="clean",
        rule="Q2",
        target="Fact_Penalty_Event",
        rows_affected=len(deduped),
        detail=f"dedup key source: {by_source}",
    )

    # Facility attributes are taken from the period the event was first seen in
    attrs = _penalty_attributes(frames, dims, log)
    deduped = deduped.merge(attrs, on=["ccn", "_snapshot"], how="left")

    facility_keys = []
    for stamp, group in deduped.groupby("_stamp", sort=True):
        facility_keys.append(
            lookup_facility_key(
                dims["Dim_Facility"], group["ccn"], stamp, log, "Fact_Penalty_Event"
            )
        )
    deduped["facility_key"] = pd.concat(facility_keys).sort_index()

    penalty_type_map = dict(
        zip(dims["Dim_Penalty_Type"]["penalty_type"], dims["Dim_Penalty_Type"]["penalty_type_key"])
    )

    out = pd.DataFrame({
        "penalty_date_key": _date_key(deduped["penalty_date"]),
        "facility_key": deduped["facility_key"].astype("int64"),
        "geography_key": deduped["geography_key"].fillna(UNKNOWN_KEY).astype("int64"),
        "ownership_key": deduped["ownership_key"].fillna(UNKNOWN_KEY).astype("int64"),
        "chain_key": deduped["chain_key"].fillna(UNKNOWN_KEY).astype("int64"),
        "penalty_type_key": deduped["penalty_type"].map(penalty_type_map)
                            .fillna(UNKNOWN_KEY).astype("int64"),
        "ccn": deduped["ccn"].values,
        "fine_id": deduped["fine_id"].values,
        "fine_id_source": deduped["fine_id_source"].values,
        "fine_amount_usd": deduped["fine_amount_usd"].values,
        "payment_denial_days": deduped["payment_denial_days"].values,
        "penalty_count": 1,
    })
    out = out.sort_values(["penalty_date_key", "ccn", "penalty_type_key"]).reset_index(drop=True)
    out.insert(0, "penalty_event_key", range(1, len(out) + 1))

    log.add(
        step="transform",
        rule="build_fact",
        target="Fact_Penalty_Event",
        rows_affected=len(out),
        detail=(
            f"{out['ccn'].nunique():,} CCN, "
            f"fines ${out['fine_amount_usd'].sum():,.0f}, "
            f"{int(out['payment_denial_days'].sum()):,} payment denial days"
        ),
    )
    return out


def _count_revised_amounts(rows: pd.DataFrame, log: RunLog) -> int:
    """Log how many penalties CMS restated, and by how much.

    Worth recording rather than silently collapsing: a revision is the source
    talking, and the difference between the first and last published figure is
    the size of the correction the warehouse is choosing to accept. Without
    this line in the run log there would be no way to tell a dedup that merged
    revisions from one that lost rows.
    """
    money = rows.dropna(subset=["fine_amount_usd"])
    spread = money.groupby("_dedup_key")["fine_amount_usd"].agg(["nunique", "first", "last"])
    changed = spread[spread["nunique"] > 1]
    if changed.empty:
        return 0
    delta = float((changed["last"] - changed["first"]).sum())
    log.add(
        step="integrate",
        rule="Q2",
        target="Fact_Penalty_Event",
        rows_affected=len(changed),
        detail=(
            f"{len(changed):,} penalties were restated by CMS between publications; "
            f"keeping the latest figure changes the stored total by ${delta:,.0f}"
        ),
    )
    return len(changed)


def _clean_denial_days(days: pd.Series, snapshot_date: str, log: RunLog) -> pd.Series:
    """Reject a negative payment-denial length instead of storing it.

    CMS publishes a handful of these: CCN 056056 carries -526 days from the
    2019-04 snapshot onward, which is a denial whose end date precedes its
    start. A length cannot be negative, so the value is not a small error to
    keep — it is not a length at all.

    Nulled rather than clamped to zero. Zero would assert the denial lasted no
    time, which is a claim the source does not make and which would quietly
    join the sum of denial days as if it were measured. Null says "this period
    did not report a usable length", which is what happened.
    """
    bad = days.notna() & (days < 0)
    if bad.any():
        log.add(
            step="clean",
            rule="Q4",
            snapshot_date=snapshot_date,
            target="Fact_Penalty_Event.payment_denial_days",
            rows_affected=int(bad.sum()),
            detail=(
                f"{int(bad.sum())} payment denial rows report a negative length "
                f"(worst {days[bad].min():.0f} days) — set to null, not clamped, "
                "because a negative length is not a measurement"
            ),
        )
        days = days.mask(bad)
    return days


def _natural_key(rows: pd.DataFrame) -> pd.Series:
    """The one identity of a penalty event, valid in every era.

    Built as text rather than a tuple so that a re-run produces byte-identical
    keys: every missing value becomes the same literal, so nothing depends on
    how pandas happens to render a value today.

    **The fine amount is not part of the identity.** It used to be, and with
    only four snapshots that looked fine. Across 33 snapshots it is not: CMS
    revises amounts after publication, so the same penalty arrives at $6,776 in
    one period and $10,425 in a later one, and an amount-bearing key files
    those as two penalties. That inflated the table to 110,193 events over
    77,975 real (facility, date, type) combinations and the stored total to
    $2.7bn. What identifies a penalty is who was penalised, when, and for what
    — the amount is an attribute of it, and one CMS reserves the right to
    change.
    """
    ccn = rows["ccn"].astype("string").fillna("?")
    date = rows["penalty_date"].dt.strftime("%Y-%m-%d").astype("string").fillna("?")
    ptype = rows["penalty_type"].astype("string").fillna("?")
    occurrence = rows["_occurrence"].astype("string")
    return ccn + "|" + date + "|" + ptype + "|" + occurrence


def _audit_key_against_fine_id(rows: pd.DataFrame, log: RunLog) -> None:
    """Use Fine ID to audit the natural key, in the periods that have both.

    Two ways the natural key could be wrong, and Fine ID can see both:
      * one key covering several Fine IDs -> the key merged distinct penalties
      * one Fine ID spread over several keys -> the key split a single penalty
        (would happen if CMS revised the amount or the date between periods)

    Reported rather than raised: this is evidence for the run log, and the
    assignment asks for the checks to be visible, not for the pipeline to hide
    a disagreement by refusing to finish.
    """
    known = rows[rows["fine_id"].notna()]
    if known.empty:
        log.add(
            step="load",
            rule="key_audit",
            target="Fact_Penalty_Event",
            detail="no period carries Fine ID — the natural key cannot be audited here",
        )
        return

    ids_per_key = known.groupby("_dedup_key")["fine_id"].nunique()
    keys_per_id = known.groupby("fine_id")["_dedup_key"].nunique()
    merged = int((ids_per_key > 1).sum())
    split = int((keys_per_id > 1).sum())

    log.add(
        step="load",
        rule="key_audit",
        target="Fact_Penalty_Event",
        rows_affected=merged + split,
        detail=(
            f"audited {len(ids_per_key):,} keys against {len(keys_per_id):,} Fine IDs: "
            f"{merged} keys covering more than one ID, "
            f"{split} IDs spread over more than one key"
        ),
    )
    if merged or split:
        log.add(
            step="load",
            rule="key_audit",
            target="Fact_Penalty_Event",
            rows_affected=merged + split,
            detail="! natural key disagrees with Fine ID — must be resolved before trusting M5",
        )

    # Residual: an occurrence whose identical siblings all got a Fine ID but it
    # did not. That means an earlier period reported more identical rows than the
    # period that can actually tell them apart, i.e. CMS corrected its own file.
    # Left in place and flagged rather than dropped, following the project's rule
    # that a suspect row keeps its value and gets a flag.
    group = ["ccn", "penalty_date", "penalty_type", "fine_amount_usd"]
    sibling_has_id = rows.groupby(group, dropna=False)["fine_id"].transform(
        lambda s: s.notna().any()
    )
    orphan = rows["fine_id"].isna() & sibling_has_id.fillna(False)
    n_orphan = int(rows.loc[orphan, "_dedup_key"].nunique())
    if n_orphan:
        money = rows.loc[orphan].drop_duplicates("_dedup_key")["fine_amount_usd"].sum()
        log.add(
            step="load",
            rule="key_audit",
            target="Fact_Penalty_Event",
            rows_affected=n_orphan,
            detail=(
                f"{n_orphan} event(s) worth ${money:,.0f} were reported as identical "
                f"duplicates by an earlier period but appear once in a period that has "
                f"Fine ID — kept and flagged, the source corrected itself"
            ),
        )


def _penalty_attributes(
    frames: list[SnapshotFrame], dims: dict[str, pd.DataFrame], log: RunLog
) -> pd.DataFrame:
    """Geography / ownership / chain keys per (ccn, period), for the penalty fact.

    The Penalties file has no ownership or chain columns, so those come from the
    ProviderInfo of the same period — attributes contemporaneous with the event
    rather than today's values.
    """
    rows = []
    for frame in frames:
        df = frame.provider_info
        rows.append(pd.DataFrame({
            "ccn": df["ccn"],
            "_snapshot": frame.snapshot_date,
            "geography_key": _geography_key(df, dims["Dim_Geography"]),
            "ownership_key": _ownership_key(df, dims["Dim_Ownership"]),
            "chain_key": _chain_key(df, dims["Dim_Chain"], frame.era != "2019"),
        }))
    attrs = pd.concat(rows, ignore_index=True)
    return attrs.drop_duplicates(subset=["ccn", "_snapshot"], keep="first")


def _empty_penalty_fact() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "penalty_event_key", "penalty_date_key", "facility_key", "geography_key",
            "ownership_key", "chain_key", "penalty_type_key", "ccn", "fine_id",
            "fine_id_source", "fine_amount_usd", "payment_denial_days", "penalty_count",
        ]
    )
