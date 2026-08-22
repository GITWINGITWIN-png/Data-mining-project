"""Transform step — build all six dimension tables.

Principles applied throughout this file:
  * surrogate keys are assigned from a fixed ordering, so a re-run produces the
    same numbers
  * every dimension has a key = -1 "Unknown" row, so fact tables never have to
    leave a foreign key null (a null foreign key drops rows silently on join)
  * never invent a value — a column an era does not have stays null and is logged
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import clean
import config
from runlog import RunLog

UNKNOWN_KEY = -1
UNKNOWN_LABEL = clean.UNKNOWN_LABEL

# Attributes watched by SCD type 2 — any change opens a new version
SCD2_TRACKED = [
    "provider_name",
    "legal_business_name",
    "provider_type",
    "resides_in_hospital",
    "ccrc_flag",
    "special_focus_status",
    "abuse_icon",
    "bed_size_band",
]

# Of those, the ones that are genuinely true/false. They need naming because the
# "Unknown" row cannot carry the string "Unknown" in a boolean column: pandas
# widens the column to object, DuckDB then stores it as VARCHAR, and a dashboard
# filter written as `WHERE abuse_icon` stops working against 'True'/'False' text.
BOOLEAN_TRACKED = {"resides_in_hospital", "ccrc_flag", "abuse_icon"}

# Thai month labels for the dashboard. The column is named month_name_th in the
# design, so its content stays Thai on purpose; every other value is English.
THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


@dataclass
class SnapshotFrame:
    """One period after column renaming and cleaning."""

    snapshot_date: str             # date of the ZIP on the CMS server
    processing_date: pd.Timestamp  # Processing Date inside the file — the real time axis
    era: str
    provider_info: pd.DataFrame
    penalties: pd.DataFrame | None = None


# =====================================================================
#  Dim_Date
# =====================================================================
def build_dim_date(log: RunLog) -> pd.DataFrame:
    """Calendar from 1960 to 2027.

    It has to reach back to the 1960s because some facilities' first-approved
    date does. Building only the range with data would leave those foreign keys
    pointing at nothing.
    """
    dates = pd.date_range(config.DATE_DIM_START, config.DATE_DIM_END, freq="D")
    df = pd.DataFrame({"full_date": dates})
    df["date_key"] = df["full_date"].dt.strftime("%Y%m%d").astype("int64")
    df["year"] = df["full_date"].dt.year
    df["quarter"] = df["full_date"].dt.quarter
    df["month"] = df["full_date"].dt.month
    df["month_name_th"] = df["month"].map(lambda m: THAI_MONTHS[m - 1])
    df["year_month"] = df["full_date"].dt.strftime("%Y-%m")
    # The U.S. fiscal year starts on 1 October — the VBP file is keyed on it
    df["us_fiscal_year"] = df["year"] + (df["month"] >= 10).astype(int)
    df["covid_period"] = pd.cut(
        df["full_date"],
        bins=[
            pd.Timestamp("1900-01-01"),
            pd.Timestamp("2020-02-29"),
            pd.Timestamp("2021-12-31"),
            pd.Timestamp("2100-01-01"),
        ],
        labels=["Pre-COVID", "COVID", "Post-COVID"],
    ).astype("string")

    unknown = pd.DataFrame(
        [{
            "date_key": config.UNKNOWN_DATE_KEY,
            "full_date": pd.NaT,
            "year": pd.NA,
            "quarter": pd.NA,
            "month": pd.NA,
            "month_name_th": UNKNOWN_LABEL,
            "year_month": UNKNOWN_LABEL,
            "us_fiscal_year": pd.NA,
            "covid_period": UNKNOWN_LABEL,
        }]
    )

    cols = [
        "date_key", "full_date", "year", "quarter", "month",
        "month_name_th", "year_month", "us_fiscal_year", "covid_period",
    ]
    out = pd.concat([unknown, df[cols]], ignore_index=True)[cols]
    log.add(
        step="transform",
        rule="build_dim",
        target="Dim_Date",
        rows_affected=len(out),
        detail=f"calendar {config.DATE_DIM_START} to {config.DATE_DIM_END} plus Unknown row",
    )
    return out


# =====================================================================
#  Dim_Geography
# =====================================================================
def build_dim_geography(frames: list[SnapshotFrame], log: RunLog) -> pd.DataFrame:
    """SCD type 1 at ZIP grain — newer values overwrite older ones.

    Worth knowing: latitude/longitude in the source are *facility* coordinates,
    not ZIP coordinates. Collapsed to this grain they become the mean of the
    facilities in that ZIP — good enough to drop a map pin, not a precise point.
    """
    pieces = []
    for order, frame in enumerate(frames):
        df = frame.provider_info
        # The three key columns come from the shared normaliser, never inline —
        # the fact lookup uses the same function to build its probe
        piece = clean.geography_key_columns(df).assign(
            county_parish=clean.normalize_text(df["county_parish"]),
            urban_flag=df["urban_flag"] if "urban_flag" in df else pd.NA,
            latitude=clean.to_number(df["latitude"]) if "latitude" in df else pd.NA,
            longitude=clean.to_number(df["longitude"]) if "longitude" in df else pd.NA,
            _order=order,
        )
        pieces.append(piece)

    allrows = pd.concat(pieces, ignore_index=True)
    allrows = allrows[allrows["zip_code"].notna() & allrows["state_code"].notna()]

    keys = ["zip_code", "city", "state_code"]
    allrows = allrows.sort_values("_order")

    # Text: last non-null value wins (SCD1). Coordinates: mean across the ZIP.
    grouped = allrows.groupby(keys, dropna=False)
    out = grouped.agg(
        county_parish=("county_parish", "last"),
        urban_flag=("urban_flag", "last"),
        latitude=("latitude", "mean"),
        longitude=("longitude", "mean"),
    ).reset_index()

    out["state_name"] = out["state_code"].map(
        lambda s: clean.STATES.get(s, (UNKNOWN_LABEL, UNKNOWN_LABEL))[0]
    )
    out["census_region"] = out["state_code"].map(
        lambda s: clean.STATES.get(s, (UNKNOWN_LABEL, UNKNOWN_LABEL))[1]
    )
    urban = clean.normalize_yes_no(out["urban_flag"])
    out["urban_rural"] = (
        urban.map({True: "Urban", False: "Rural"}).astype("string").fillna(UNKNOWN_LABEL)
    )

    unknown_states = sorted(set(out.loc[out["state_name"] == UNKNOWN_LABEL, "state_code"].dropna()))
    if unknown_states:
        log.add(
            step="transform",
            rule="Q5",
            target="Dim_Geography",
            rows_affected=len(unknown_states),
            detail=f"state codes missing from the lookup table: {unknown_states}",
        )

    # Population 65+ deliberately does not live here. This table is at
    # (zip_code, city, state_code) grain, so a state population written onto it
    # is repeated across every ZIP row in that state and any plain SUM
    # multiplies it by the ZIP count. It lives in Ref_State_Population at its
    # own (state, year) grain instead; see population.py, and v_market_saturation
    # for the join.

    out = out.sort_values(["state_code", "zip_code", "city"]).reset_index(drop=True)
    out.insert(0, "geography_key", range(1, len(out) + 1))

    cols = [
        "geography_key", "zip_code", "city", "county_parish", "state_code",
        "state_name", "census_region", "urban_rural", "latitude", "longitude",
    ]
    out = _prepend_unknown(out[cols], "geography_key", {
        "zip_code": UNKNOWN_LABEL, "city": UNKNOWN_LABEL,
        "county_parish": UNKNOWN_LABEL, "state_code": UNKNOWN_LABEL,
        "state_name": UNKNOWN_LABEL, "census_region": UNKNOWN_LABEL,
        "urban_rural": UNKNOWN_LABEL,
    })
    log.add(
        step="transform",
        rule="build_dim",
        target="Dim_Geography",
        rows_affected=len(out),
        detail=f"{out['state_code'].nunique() - 1} states and territories",
    )
    return out


# =====================================================================
#  Dim_Ownership
# =====================================================================
def build_dim_ownership(frames: list[SnapshotFrame], log: RunLog) -> pd.DataFrame:
    """One row per ownership type, with the three groups from rule Q5."""
    values = pd.concat(
        [f.provider_info["ownership_type"] for f in frames], ignore_index=True
    )
    values = clean.normalize_text(values).dropna().drop_duplicates().sort_values()

    out = pd.DataFrame({"ownership_type": values.reset_index(drop=True)})
    out["ownership_group"] = out["ownership_type"].map(clean.OWNERSHIP_GROUP).fillna(UNKNOWN_LABEL)
    out["is_for_profit"] = out["ownership_group"] == clean.FOR_PROFIT_GROUP
    out.insert(0, "ownership_key", range(1, len(out) + 1))

    out = _prepend_unknown(out, "ownership_key", {
        "ownership_type": UNKNOWN_LABEL,
        "ownership_group": UNKNOWN_LABEL,
        "is_for_profit": False,
    })
    log.add(
        step="transform",
        rule="build_dim",
        target="Dim_Ownership",
        rows_affected=len(out),
        detail=f"{len(out) - 1} types collapsed into {out['ownership_group'].nunique() - 1} groups",
    )
    return out


# =====================================================================
#  Dim_Chain
# =====================================================================
def build_dim_chain(frames: list[SnapshotFrame], log: RunLog) -> pd.DataFrame:
    """One row per chain, plus a dedicated row for facilities in no chain.

    The 2019 era has no chain columns at all, so this table is built only from
    the periods that have them, and facilities in older periods point at the
    "Unknown" row rather than the "Independent" row — *not knowing* is different
    from *knowing there is no chain*.
    """
    pieces = []
    for order, frame in enumerate(frames):
        df = frame.provider_info
        if "chain_name" not in df.columns or df["chain_name"].isna().all():
            continue
        pieces.append(pd.DataFrame({
            "chain_name": clean.normalize_text(df["chain_name"], upper=True),
            "chain_id": clean.normalize_text(df["chain_id"]),
            "num_facilities_in_chain": clean.to_number(df["num_facilities_in_chain"]),
            "_order": order,
        }))

    if not pieces:
        log.add(
            step="transform",
            rule="Q7",
            target="Dim_Chain",
            detail="no period carries chain data — only the special rows are created",
        )
        out = pd.DataFrame(columns=["chain_name", "chain_id", "num_facilities_in_chain"])
    else:
        allrows = pd.concat(pieces, ignore_index=True)
        allrows = allrows[allrows["chain_name"].notna()].sort_values("_order")
        out = (
            allrows.groupby("chain_name", dropna=False)
            .agg(
                chain_id=("chain_id", "last"),
                num_facilities_in_chain=("num_facilities_in_chain", "last"),
            )
            .reset_index()
            .sort_values("chain_name")
            .reset_index(drop=True)
        )

    out["chain_size_band"] = _chain_size_band(out["num_facilities_in_chain"])
    out["is_independent"] = False
    out.insert(0, "chain_key", range(1, len(out) + 1))

    cols = ["chain_key", "chain_id", "chain_name", "num_facilities_in_chain",
            "chain_size_band", "is_independent"]
    out = out[cols]

    # Key 0 = known to belong to no chain (column present but blank)
    independent = pd.DataFrame([{
        "chain_key": 0,
        "chain_id": pd.NA,
        "chain_name": "Independent (no chain)",
        "num_facilities_in_chain": 1,
        "chain_size_band": "Independent (1)",
        "is_independent": True,
    }])
    out = pd.concat([independent, out], ignore_index=True)
    out = _prepend_unknown(out, "chain_key", {
        "chain_id": pd.NA,
        "chain_name": UNKNOWN_LABEL,
        "num_facilities_in_chain": pd.NA,
        "chain_size_band": UNKNOWN_LABEL,
        "is_independent": False,
    })
    log.add(
        step="transform",
        rule="build_dim",
        target="Dim_Chain",
        rows_affected=len(out),
        detail=f"{len(out) - 2} chains plus the Independent and Unknown rows",
    )
    return out


def _chain_size_band(counts: pd.Series) -> pd.Series:
    band = pd.Series(UNKNOWN_LABEL, index=counts.index, dtype="object")
    n = pd.to_numeric(counts, errors="coerce")
    band[n <= 1] = "Independent (1)"
    band[(n >= 2) & (n <= 9)] = "Small chain (2-9)"
    band[(n >= 10) & (n <= 49)] = "Medium chain (10-49)"
    band[n >= 50] = "Large chain (50+)"
    return band


# =====================================================================
#  Dim_Penalty_Type
# =====================================================================
def build_dim_penalty_type(frames: list[SnapshotFrame], log: RunLog) -> pd.DataFrame:
    """The two kinds of penalty: monetary fines and payment denials.

    is_monetary guards the mistake the design document warns about: summing both
    kinds together adds two different units.
    """
    values = []
    for frame in frames:
        if frame.penalties is None or "penalty_type" not in frame.penalties:
            continue
        values.append(clean.normalize_text(frame.penalties["penalty_type"]))

    if values:
        found = pd.concat(values, ignore_index=True).dropna().drop_duplicates().sort_values()
    else:
        found = pd.Series(["Fine", "Payment Denial"], dtype="string")
        log.add(
            step="transform",
            rule="build_dim",
            target="Dim_Penalty_Type",
            detail="no Penalties file in any period — falling back to the two known types",
        )

    out = pd.DataFrame({"penalty_type": found.reset_index(drop=True)})
    out["is_monetary"] = out["penalty_type"].str.strip().str.lower().eq("fine")
    out.insert(0, "penalty_type_key", range(1, len(out) + 1))
    out = _prepend_unknown(out, "penalty_type_key", {
        "penalty_type": UNKNOWN_LABEL,
        "is_monetary": False,
    })
    log.add(
        step="transform",
        rule="build_dim",
        target="Dim_Penalty_Type",
        rows_affected=len(out),
        detail=f"penalty types: {out['penalty_type'].tolist()}",
    )
    return out


# =====================================================================
#  Dim_Facility — SCD type 2
# =====================================================================
def build_dim_facility(frames: list[SnapshotFrame], log: RunLog) -> pd.DataFrame:
    """Build Dim_Facility as a type 2 slowly changing dimension.

    Walks the periods oldest first (the order matters — shuffle it and the result
    is wrong) and compares the tracked attributes against the open version of
    that CCN:
      * CCN never seen    -> open the first version
      * seen, same values -> nothing to do
      * seen, changed     -> close the old version (expiry = the new period's
                             date) and open a new one

    Two things that are *not* real changes have to be filtered out, otherwise
    crossing from the 2019 era to the 2026 era invents a version for every
    facility at once:
      1. the new period lacks the column (null because the old era did not store
         it) -> carry the previous value forward
      2. the value was unknown and is now known (abuse_icon, added after 2019)
         -> fill it into the open version in place, do not open a new one,
         because "unknown becomes known" is late-arriving data, not a facility
         changing state
    """
    open_versions: dict[str, dict] = {}   # ccn -> the version still open
    closed: list[dict] = []

    for frame in sorted(frames, key=lambda f: f.processing_date):
        stamp = frame.processing_date
        incoming = _facility_attrs(frame)

        new_count = changed_count = filled_count = 0
        for ccn, attrs in incoming.items():
            prev = open_versions.get(ccn)
            if prev is None:
                open_versions[ccn] = _new_version(ccn, attrs, stamp)
                new_count += 1
                continue

            # Compare only columns whose value is known on *both* sides
            changed = [
                col for col in SCD2_TRACKED
                if _has_value(attrs[col]) and _has_value(prev[col])
                and attrs[col] != prev[col]
            ]
            if changed:
                merged = {
                    col: (attrs[col] if _has_value(attrs[col]) else prev[col])
                    for col in SCD2_TRACKED
                }
                prev["expiry_date"] = stamp
                prev["is_current"] = False
                closed.append(prev)
                open_versions[ccn] = _new_version(ccn, merged, stamp)
                changed_count += 1
                continue

            # Nothing changed, but some values are newly known -> fill in place
            late = [col for col in SCD2_TRACKED
                    if _has_value(attrs[col]) and not _has_value(prev[col])]
            if late:
                for col in late:
                    prev[col] = attrs[col]
                filled_count += 1

        log.add(
            step="transform",
            rule="scd2",
            snapshot_date=frame.snapshot_date,
            target="Dim_Facility",
            rows_affected=len(incoming),
            detail=(
                f"period {stamp.date()}: {len(incoming):,} facilities, "
                f"{new_count:,} new, {changed_count:,} changed, "
                f"{filled_count:,} late-arriving values filled"
            ),
        )

    rows = closed + list(open_versions.values())
    out = pd.DataFrame(rows)
    out = out.sort_values(["ccn", "effective_date"]).reset_index(drop=True)
    out.insert(0, "facility_key", range(1, len(out) + 1))

    cols = ["facility_key", "ccn"] + SCD2_TRACKED + [
        "effective_date", "expiry_date", "is_current",
    ]
    out = out[cols]
    out = _prepend_unknown(out, "facility_key", {
        "ccn": UNKNOWN_LABEL,
        # Text attributes say "Unknown"; boolean ones stay null, because a
        # boolean column cannot hold that word without turning into text
        **{c: (pd.NA if c in BOOLEAN_TRACKED else UNKNOWN_LABEL) for c in SCD2_TRACKED},
        "effective_date": pd.NaT,
        "expiry_date": pd.NaT,
        "is_current": False,
    })
    out["is_current"] = out["is_current"].astype(bool)
    for col in BOOLEAN_TRACKED:
        out[col] = out[col].astype("boolean")

    log.add(
        step="transform",
        rule="build_dim",
        target="Dim_Facility",
        rows_affected=len(out),
        detail=(
            f"{out['ccn'].nunique() - 1:,} CCN across {len(out) - 1:,} versions "
            f"(more versions than CCN means SCD2 caught the changes)"
        ),
    )
    return out


def _facility_attrs(frame: SnapshotFrame) -> dict[str, dict]:
    """Tracked attributes of every facility in one period, keyed by CCN."""
    df = frame.provider_info
    work = pd.DataFrame({
        "ccn": df["ccn"],
        # Uppercased (rule Q5) because the 2019 era capitalises inconsistently.
        # Without this, about 17% of facilities look like they were renamed.
        "provider_name": clean.normalize_text(df["provider_name"], upper=True),
        "legal_business_name": clean.normalize_text(df["legal_business_name"], upper=True),
        "provider_type": clean.normalize_text(df["provider_type"]),
        "resides_in_hospital": clean.normalize_yes_no(df["resides_in_hospital"]),
        "ccrc_flag": clean.normalize_yes_no(df["ccrc_flag"]),
        "special_focus_status": clean.normalize_special_focus(df["special_focus_status"]),
        "abuse_icon": clean.normalize_yes_no(df["abuse_icon"]) if "abuse_icon" in df else pd.NA,
        "bed_size_band": _bed_size_band(clean.to_number(df["certified_beds"])),
    })
    # A period should not repeat a CCN; if it does, keep the first row —
    # rule Q1 has already logged it
    work = work.drop_duplicates(subset="ccn", keep="first")
    return work.set_index("ccn").to_dict("index")


def _bed_size_band(beds: pd.Series) -> pd.Series:
    """Band the bed count, leaving an unknown count *null* rather than "Unknown".

    The word would be a value like any other, so SCD2 would read a facility whose
    bed count arrives late as having changed band, and open a version for it. Null
    is what the "unknown becomes known" rule in `build_dim_facility` looks for.
    """
    band = pd.Series(pd.NA, index=beds.index, dtype="object")
    band[beds < 50] = "Small (<50 beds)"
    band[(beds >= 50) & (beds < 100)] = "Medium (50-99 beds)"
    band[(beds >= 100) & (beds < 200)] = "Large (100-199 beds)"
    band[beds >= 200] = "Very large (200+ beds)"
    return band


def _new_version(ccn: str, attrs: dict, stamp: pd.Timestamp) -> dict:
    version = {"ccn": ccn}
    version.update({col: attrs[col] for col in SCD2_TRACKED})
    version["effective_date"] = stamp
    version["expiry_date"] = pd.NaT
    version["is_current"] = True
    return version


def _has_value(value) -> bool:
    return value is not None and value is not pd.NA and pd.notna(value)


def _prepend_unknown(df: pd.DataFrame, key_col: str, values: dict) -> pd.DataFrame:
    """Put the key = -1 "Unknown" row at the top."""
    row = {key_col: UNKNOWN_KEY}
    row.update(values)
    unknown = pd.DataFrame([row])
    return pd.concat([unknown, df], ignore_index=True)[df.columns]
