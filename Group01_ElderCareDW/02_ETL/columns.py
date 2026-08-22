"""Per-era column name mapping (rule Q7).

The problem: the 2019 files have 78 columns named with uppercase abbreviations
(`PROVNUM`); the 2026 files have 99 columns named with full sentences
(`CMS Certification Number (CCN)`). It is not just added columns — the whole
naming scheme changed.

The fix: translate every era into one set of canonical names, so the rest of the
pipeline only ever knows the canonical ones. Columns an era does not have are
created empty, and the run log records which period was missing which column.

Never guess a column by fuzzy matching. This table has to name each column
explicitly, because matching the wrong column produces no error — just wrong
numbers, silently.
"""

from __future__ import annotations

import pandas as pd

from runlog import RunLog

# ---------------------------------------------------------------------
#  2026 era — full sentence names (seen in 2026-06-24 and 2026-07-29)
# ---------------------------------------------------------------------
PROVIDER_INFO_2026 = {
    "ccn": "CMS Certification Number (CCN)",
    "provider_name": "Provider Name",
    "address": "Provider Address",
    "city": "City/Town",
    "state_code": "State",
    "zip_code": "ZIP Code",
    "county_ssa": "Provider SSA County Code",
    "county_parish": "County/Parish",
    "urban_flag": "Urban",
    "ownership_type": "Ownership Type",
    "certified_beds": "Number of Certified Beds",
    "avg_residents_per_day": "Average Number of Residents per Day",
    "avg_residents_footnote": "Average Number of Residents per Day Footnote",
    "provider_type": "Provider Type",
    "resides_in_hospital": "Provider Resides in Hospital",
    "legal_business_name": "Legal Business Name",
    "first_approved_date": "Date First Approved to Provide Medicare and Medicaid Services",
    "chain_name": "Chain Name",
    "chain_id": "Chain ID",
    "num_facilities_in_chain": "Number of Facilities in Chain",
    "ccrc_flag": "Continuing Care Retirement Community",
    "special_focus_status": "Special Focus Status",
    "abuse_icon": "Abuse Icon",
    "changed_ownership_12mo": "Provider Changed Ownership in Last 12 Months",
    "overall_rating": "Overall Rating",
    "overall_rating_footnote": "Overall Rating Footnote",
    "health_inspection_rating": "Health Inspection Rating",
    "staffing_rating": "Staffing Rating",
    "staffing_rating_footnote": "Staffing Rating Footnote",
    "reported_total_nurse_hprd": "Reported Total Nurse Staffing Hours per Resident per Day",
    "reported_rn_hprd": "Reported RN Staffing Hours per Resident per Day",
    "total_nursing_turnover_pct": "Total nursing staff turnover",
    "cycle1_total_deficiencies": "Rating Cycle 1 Total Number of Health Deficiencies",
    "latitude": "Latitude",
    "longitude": "Longitude",
    "processing_date": "Processing Date",
}

# ---------------------------------------------------------------------
#  2019 era — uppercase abbreviations (seen in 2019-01-17)
#  Absent in this era: urban_flag, chain_*, abuse_icon, turnover, lat/long,
#  which matches what rule Q7 in the design document describes.
# ---------------------------------------------------------------------
PROVIDER_INFO_2019 = {
    "ccn": "PROVNUM",
    "provider_name": "PROVNAME",
    "address": "ADDRESS",
    "city": "CITY",
    "state_code": "STATE",
    "zip_code": "ZIP",
    "county_ssa": "COUNTY_SSA",
    "county_parish": "COUNTY_NAME",
    "ownership_type": "OWNERSHIP",
    "certified_beds": "BEDCERT",
    "avg_residents_per_day": "RESTOT",
    "provider_type": "CERTIFICATION",
    "resides_in_hospital": "INHOSP",
    "legal_business_name": "LBN",
    "first_approved_date": "PARTICIPATION_DATE",
    "ccrc_flag": "CCRC_FACIL",
    "special_focus_status": "SFF",
    "changed_ownership_12mo": "CHOW_LAST_12MOS",
    "overall_rating": "OVERALL_RATING",
    "overall_rating_footnote": "overall_rating_fn",
    "health_inspection_rating": "SURVEY_RATING",
    "staffing_rating": "STAFFING_RATING",
    "staffing_rating_footnote": "staffing_rating_fn",
    "reported_total_nurse_hprd": "TOTHRD",
    "reported_rn_hprd": "RNHRD",
    "cycle1_total_deficiencies": "CYCLE_1_DEFS",
    "processing_date": "FILEDATE",
}

# ---------------------------------------------------------------------
#  2020 era — full sentence names, but keyed on "Federal Provider Number"
#  (2020-10-09 through 2023-04-26, 11 of the 31 quarterly periods)
#
#  This era only appeared once the quarterly set was loaded. The original
#  four-period sample jumped straight from 2019-01 to 2026-06 and never saw it,
#  so the pipeline had no mapping and refused to read the file — correctly, and
#  loudly, which is what `MUST_HAVE` is for.
#
#  CMS renamed the key column twice: PROVNUM -> Federal Provider Number ->
#  CMS Certification Number (CCN). The middle era is otherwise close to the 2026
#  layout, but the address block differs ("Provider City" vs "City/Town") and it
#  carries no chain columns, no Urban flag and no separate latitude/longitude —
#  it has a combined "Location" field instead, which is deliberately not mapped
#  rather than parsed apart.
# ---------------------------------------------------------------------
PROVIDER_INFO_2020 = {
    "ccn": "Federal Provider Number",
    "provider_name": "Provider Name",
    "address": "Provider Address",
    "city": "Provider City",
    "state_code": "Provider State",
    "zip_code": "Provider Zip Code",
    "county_ssa": "Provider SSA County Code",
    "county_parish": "Provider County Name",
    "ownership_type": "Ownership Type",
    "certified_beds": "Number of Certified Beds",
    "avg_residents_per_day": "Average Number of Residents per Day",
    "avg_residents_footnote": "Average Number of Residents per Day Footnote",
    "provider_type": "Provider Type",
    "resides_in_hospital": "Provider Resides in Hospital",
    "legal_business_name": "Legal Business Name",
    "first_approved_date": "Date First Approved to Provide Medicare and Medicaid Services",
    "ccrc_flag": "Continuing Care Retirement Community",
    "special_focus_status": "Special Focus Status",
    "abuse_icon": "Abuse Icon",
    "changed_ownership_12mo": "Provider Changed Ownership in Last 12 Months",
    "overall_rating": "Overall Rating",
    "overall_rating_footnote": "Overall Rating Footnote",
    "health_inspection_rating": "Health Inspection Rating",
    "staffing_rating": "Staffing Rating",
    "staffing_rating_footnote": "Staffing Rating Footnote",
    "reported_total_nurse_hprd": "Reported Total Nurse Staffing Hours per Resident per Day",
    "reported_rn_hprd": "Reported RN Staffing Hours per Resident per Day",
    # Turnover arrives partway through this era (present by 2022-10, absent in
    # 2020-10). Mapped here because the name is identical once it appears;
    # periods without it get a null column and a Q7 log line.
    "total_nursing_turnover_pct": "Total nursing staff turnover",
    "cycle1_total_deficiencies": "Rating Cycle 1 Total Number of Health Deficiencies",
    "processing_date": "Processing Date",
}

PENALTIES_2026 = {
    "ccn": "CMS Certification Number (CCN)",
    "penalty_date": "Penalty Date",
    "penalty_type": "Penalty Type",
    "fine_id": "Fine ID",
    "fine_amount_usd": "Fine Amount",
    "payment_denial_start_date": "Payment Denial Start Date",
    "payment_denial_days": "Payment Denial Length in Days",
    "processing_date": "Processing Date",
}

PENALTIES_2020 = {
    "ccn": "Federal Provider Number",
    "penalty_date": "Penalty Date",
    "penalty_type": "Penalty Type",
    "fine_amount_usd": "Fine Amount",
    "payment_denial_start_date": "Payment Denial Start Date",
    "payment_denial_days": "Payment Denial Length in Days",
    "processing_date": "Processing Date",
    # No Fine ID: CMS added that column with the 202606 release, which is the
    # whole reason Fact_Penalty_Event needs a natural key at all.
}

PENALTIES_2019 = {
    "ccn": "provnum",
    "penalty_date": "pnlty_date",
    "penalty_type": "pnlty_type",
    "fine_amount_usd": "fine_amt",
    "payment_denial_start_date": "payden_strt_dt",
    "payment_denial_days": "payden_days",
    "processing_date": "filedate",
}

# Newest era first. The era is detected from the columns actually present.
ERAS = {
    "provider_info": [
        ("2026", PROVIDER_INFO_2026),
        ("2020", PROVIDER_INFO_2020),
        ("2019", PROVIDER_INFO_2019),
    ],
    "penalties": [
        ("2026", PENALTIES_2026),
        ("2020", PENALTIES_2020),
        ("2019", PENALTIES_2019),
    ],
}

# Without these a file is unusable
MUST_HAVE = {
    "provider_info": ["ccn", "provider_name", "state_code", "ownership_type"],
    "penalties": ["ccn", "penalty_date", "penalty_type"],
}


def detect_era(logical: str, columns) -> tuple[str, dict[str, str]]:
    """Work out which era a file belongs to from the column names it has.

    The period's date is deliberately not used, because CMS did not change the
    format on calendar-year boundaries. Looking at the file itself is safer.
    """
    available = set(columns)

    # The eras share many column names — "Provider Name" and "Ownership Type"
    # are identical in 2020 and 2026 — so a plain hit count can be won by the
    # wrong map. The key column is what actually separates them (PROVNUM ->
    # Federal Provider Number -> CMS Certification Number (CCN)), and a map
    # whose key column is absent cannot be the right one at any hit count.
    candidates = [
        (name, mapping) for name, mapping in ERAS[logical]
        if mapping["ccn"] in available
    ] or list(ERAS[logical])

    best_name, best_map, best_hits = "unknown", {}, -1
    for name, mapping in candidates:
        hits = sum(1 for src in mapping.values() if src in available)
        if hits > best_hits:
            best_name, best_map, best_hits = name, mapping, hits
    if best_hits <= 0:
        raise ValueError(f"unrecognised column layout for {logical}: {sorted(available)[:8]} ...")
    return best_name, best_map


def canonicalize(
    df: pd.DataFrame,
    logical: str,
    snapshot_date: str,
    log: RunLog,
) -> tuple[pd.DataFrame, str]:
    """Rename columns to the canonical names. Returns (DataFrame, era name).

    Columns the era does not have are created empty, so downstream code can be
    written once instead of being littered with per-era branches.
    """
    df = df.rename(columns=lambda c: c.strip())
    era, mapping = detect_era(logical, df.columns)

    present = {canon: src for canon, src in mapping.items() if src in df.columns}
    out = df[list(present.values())].copy()
    out.columns = list(present.keys())

    missing_required = [c for c in MUST_HAVE[logical] if c not in out.columns]
    if missing_required:
        raise ValueError(
            f"period {snapshot_date} file {logical} is missing required columns: {missing_required}"
        )

    # Fill in columns this era lacks — never substitute a guessed value
    all_canon = {c for _, m in ERAS[logical] for c in m}
    missing = sorted(all_canon - set(out.columns))
    for col in missing:
        out[col] = pd.NA

    if missing:
        log.add(
            step="clean",
            rule="Q7",
            snapshot_date=snapshot_date,
            target=logical,
            rows_affected=len(missing),
            detail=f"era {era}: columns absent, filled with nulls: {missing}",
        )
    log.add(
        step="clean",
        rule="Q7",
        snapshot_date=snapshot_date,
        target=logical,
        rows_affected=len(df.columns),
        detail=f"detected era {era} ({len(df.columns)} source columns, {len(present)} mapped)",
    )
    return out, era
