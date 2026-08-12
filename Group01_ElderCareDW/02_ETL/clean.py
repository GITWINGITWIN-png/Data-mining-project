"""Clean step — the data quality rules the dimensions depend on.

Covers the rules that apply directly to dimensions:
    Q1 key mismatch        — CCN and ZIP must stay text with leading zeros
    Q3 missing values      — never impute; leave null and record how many
    Q5 inconsistent labels — explicit lookup table, no fuzzy matching
    Q6 date formats        — try an explicit format list, never let pandas guess
(Q7 lives in columns.py; Q2, Q4 and Q8 belong to the fact tables, not built yet)

No function here mutates its input — each returns a new object, so calling twice
gives the same result.
"""

from __future__ import annotations

import pandas as pd

import config
from runlog import RunLog

# ---------------------------------------------------------------------
#  Q5 — collapse ownership type into three groups.
#  Lists all 13 values observed in the 2019 and 2026 files. Unknown values are
#  logged rather than guessed into a group.
# ---------------------------------------------------------------------
OWNERSHIP_GROUP = {
    "For profit - Corporation": "For profit",
    "For profit - Individual": "For profit",
    "For profit - Limited Liability company": "For profit",
    "For profit - Partnership": "For profit",
    "Non profit - Church related": "Non profit",
    "Non profit - Corporation": "Non profit",
    "Non profit - Other": "Non profit",
    "Government - City": "Government",
    "Government - City/county": "Government",
    "Government - County": "Government",
    "Government - Federal": "Government",
    "Government - Hospital district": "Government",
    "Government - State": "Government",
}

FOR_PROFIT_GROUP = "For profit"
UNKNOWN_LABEL = "Unknown"

# ---------------------------------------------------------------------
#  States and Census Bureau regions, used to fill Dim_Geography
# ---------------------------------------------------------------------
STATES = {
    "AL": ("Alabama", "South"), "AK": ("Alaska", "West"),
    "AZ": ("Arizona", "West"), "AR": ("Arkansas", "South"),
    "CA": ("California", "West"), "CO": ("Colorado", "West"),
    "CT": ("Connecticut", "Northeast"), "DE": ("Delaware", "South"),
    "DC": ("District of Columbia", "South"), "FL": ("Florida", "South"),
    "GA": ("Georgia", "South"), "HI": ("Hawaii", "West"),
    "ID": ("Idaho", "West"), "IL": ("Illinois", "Midwest"),
    "IN": ("Indiana", "Midwest"), "IA": ("Iowa", "Midwest"),
    "KS": ("Kansas", "Midwest"), "KY": ("Kentucky", "South"),
    "LA": ("Louisiana", "South"), "ME": ("Maine", "Northeast"),
    "MD": ("Maryland", "South"), "MA": ("Massachusetts", "Northeast"),
    "MI": ("Michigan", "Midwest"), "MN": ("Minnesota", "Midwest"),
    "MS": ("Mississippi", "South"), "MO": ("Missouri", "Midwest"),
    "MT": ("Montana", "West"), "NE": ("Nebraska", "Midwest"),
    "NV": ("Nevada", "West"), "NH": ("New Hampshire", "Northeast"),
    "NJ": ("New Jersey", "Northeast"), "NM": ("New Mexico", "West"),
    "NY": ("New York", "Northeast"), "NC": ("North Carolina", "South"),
    "ND": ("North Dakota", "Midwest"), "OH": ("Ohio", "Midwest"),
    "OK": ("Oklahoma", "South"), "OR": ("Oregon", "West"),
    "PA": ("Pennsylvania", "Northeast"), "RI": ("Rhode Island", "Northeast"),
    "SC": ("South Carolina", "South"), "SD": ("South Dakota", "Midwest"),
    "TN": ("Tennessee", "South"), "TX": ("Texas", "South"),
    "UT": ("Utah", "West"), "VT": ("Vermont", "Northeast"),
    "VA": ("Virginia", "South"), "WA": ("Washington", "West"),
    "WV": ("West Virginia", "South"), "WI": ("Wisconsin", "Midwest"),
    "WY": ("Wyoming", "West"),
    # U.S. territories — outside the four Census regions
    "PR": ("Puerto Rico", "Territory"), "GU": ("Guam", "Territory"),
    "VI": ("U.S. Virgin Islands", "Territory"),
    "AS": ("American Samoa", "Territory"),
    "MP": ("Northern Mariana Islands", "Territory"),
}

# ---------------------------------------------------------------------
#  Q6 — accepted date formats, tried in this order.
#  Both ends of the range use YYYY-MM-DD, but the middle periods have not been
#  checked, so the list stays as a safety net. Never enable dayfirst or let
#  pandas infer: swapping day and month produces no error, just wrong dates.
# ---------------------------------------------------------------------
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"]


def normalize_ccn(series: pd.Series) -> pd.Series:
    """Rule Q1 — CCN must be 6-character text with leading zeros.

    `015009` read as a number becomes `15009`, which then fails to join with no
    error at all.
    """
    out = series.astype("string").str.strip()
    return out.str.zfill(config.CCN_WIDTH)


def normalize_zip(series: pd.Series) -> pd.Series:
    """Rule Q1 — 5-digit ZIP, same trap as CCN (`01234` in the Northeast)."""
    out = series.astype("string").str.strip().str.slice(0, 5)
    return out.str.zfill(5)


def normalize_text(series: pd.Series, upper: bool = False) -> pd.Series:
    """Trim and collapse whitespace (rule Q5, the chain-name half).

    upper=True is for values used as grouping keys, since the same name shows up
    with different capitalisation across periods.
    """
    out = series.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)
    out = out.replace({"": pd.NA})
    return out.str.upper() if upper else out


def to_number(series: pd.Series) -> pd.Series:
    """Convert to numeric; unparseable values become null, not zero (rule Q3)."""
    cleaned = (
        series.astype("string")
        .str.replace(r"[$,%\s]", "", regex=True)
        .replace({"": pd.NA})
    )
    return pd.to_numeric(cleaned, errors="coerce")


def parse_date(series: pd.Series, field: str, snapshot_date: str, log: RunLog) -> pd.Series:
    """Rule Q6 — one format is chosen for the whole column, never row by row.

    A column holds one format. Deciding per row looks more forgiving and is in
    fact the trap the rule exists to avoid: trying `%m/%d/%Y` first and letting
    the rest fall through to `%d/%m/%Y` means a column genuinely written as
    day/month is *half* misread, silently and with nothing logged, because only
    the rows whose day exceeds 12 fail the first format and reach the second.
    `01/02/2024` becomes 2 January and `13/02/2024` becomes 13 February, in the
    same column.

    So every candidate format is scored against the whole column, and the one
    that explains the most values wins. Values the winner cannot parse are left
    null and counted, rather than quietly handed to a different calendar.
    """
    raw = series.astype("string").str.strip().replace({"": pd.NA})
    total = int(raw.notna().sum())
    if not total:
        return pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")

    attempts = {}
    for fmt in DATE_FORMATS:
        parsed = pd.to_datetime(raw, format=fmt, errors="coerce")
        attempts[fmt] = (int(parsed.notna().sum()), parsed)

    best_fmt = max(attempts, key=lambda f: attempts[f][0])
    best_hits, result = attempts[best_fmt]

    if best_hits == 0:
        log.add(
            step="clean",
            rule="Q6",
            snapshot_date=snapshot_date,
            target=field,
            rows_affected=total,
            detail=(
                f"no accepted format parsed any value, e.g. "
                f"{raw.dropna().unique()[:3].tolist()} — left null"
            ),
        )
        return result

    # Two formats explaining the column equally well means the dates themselves
    # cannot say which calendar they were written in (every day <= 12). Report it
    # instead of letting the list order silently pick a winner.
    rivals = [
        f for f, (hits, parsed) in attempts.items()
        if f != best_fmt and hits == best_hits and not parsed.equals(result)
    ]
    if rivals:
        log.add(
            step="clean",
            rule="Q6",
            snapshot_date=snapshot_date,
            target=field,
            rows_affected=best_hits,
            detail=(
                f"! ambiguous: {best_fmt} and {rivals} each explain all "
                f"{best_hits:,} values but disagree — using {best_fmt}, needs a human decision"
            ),
        )

    unparsed = total - best_hits
    log.add(
        step="clean",
        rule="Q6",
        snapshot_date=snapshot_date,
        target=field,
        rows_affected=unparsed,
        detail=(
            f"format {best_fmt} chosen for the whole column "
            f"({best_hits:,} of {total:,} parsed, {unparsed:,} left null)"
        ),
    )
    return result


def normalize_yes_no(series: pd.Series) -> pd.Series:
    """Map Y/N (and Yes/No/True/False) to boolean; anything else becomes null."""
    text = series.astype("string").str.strip().str.upper()
    return text.map({"Y": True, "YES": True, "TRUE": True, "1": True,
                     "N": False, "NO": False, "FALSE": False, "0": False}).astype("boolean")


def normalize_special_focus(series: pd.Series) -> pd.Series:
    """Unify the special focus domain across eras.

    In 2019 the SFF column holds Y/N; in 2026 it holds three text values
    (`SFF`, `SFF Candidate`, blank). Without unifying them, SCD2 would see a
    fake change on every facility at the era boundary.

    A blank only becomes "None" when the era actually carries the column. If the
    column is absent altogether every value is null, and reading that as "None"
    would claim we know a facility has no special focus status when in truth we
    have not been told — the null has to survive so that SCD2 treats it as
    unknown rather than as a state.
    """
    text = series.astype("string").str.strip()
    mapped = text.replace(
        {
            "Y": "SFF",
            "N": "None",
            "": pd.NA,
        }
    )
    era_has_column = text.notna().any()
    return mapped.fillna("None") if era_has_column else mapped


def geography_key_columns(df: pd.DataFrame) -> pd.DataFrame:
    """The natural key of Dim_Geography, normalised identically on both sides.

    Rule Q5. City has to be upper-cased for exactly the reason facility names
    are: CMS changes the capitalisation between periods — ZIP 72143 in Arkansas
    is `SEARCY` in the April 2026 file and `Searcy` in the June 2026 file — and a
    dimension keyed on the raw spelling splits one real place into several rows.
    The same facility then gets a different geography_key from one period to the
    next, and every trend at city or ZIP grain silently breaks in half.

    Both the dimension builder and the fact lookup must call this. If the two
    sides ever normalise differently, no row matches and every geography_key
    resolves to Unknown — with no error, which is the whole reason this lives in
    one function instead of being written out twice.
    """
    return pd.DataFrame(
        {
            "zip_code": normalize_zip(df["zip_code"]),
            "city": normalize_text(df["city"], upper=True),
            "state_code": normalize_text(df["state_code"], upper=True),
        },
        index=df.index,
    )


def normalize_ownership(
    df: pd.DataFrame, snapshot_date: str, log: RunLog
) -> pd.DataFrame:
    """Rule Q5 — collapse ownership type into three groups via explicit lookup."""
    out = df.copy()
    out["ownership_type"] = normalize_text(out["ownership_type"])
    out["ownership_group"] = out["ownership_type"].map(OWNERSHIP_GROUP)

    unmapped = out.loc[out["ownership_group"].isna() & out["ownership_type"].notna(),
                       "ownership_type"]
    if len(unmapped):
        log.add(
            step="clean",
            rule="Q5",
            snapshot_date=snapshot_date,
            target="ownership_type",
            rows_affected=len(unmapped),
            detail=f"values missing from the lookup table: {sorted(unmapped.unique())[:5]}",
        )
    out["ownership_group"] = out["ownership_group"].fillna(UNKNOWN_LABEL)
    out["is_for_profit"] = (out["ownership_group"] == FOR_PROFIT_GROUP)
    return out


def report_nulls(
    df: pd.DataFrame, fields: list[str], snapshot_date: str, log: RunLog
) -> None:
    """Rule Q3 — report how many rows are null in key columns, imputing nothing.

    Filling a new facility's star rating with a mean or a zero skews every
    average. The correct handling is to leave it null and exclude it from the
    denominator at query time.
    """
    for field in fields:
        if field not in df.columns:
            continue
        n = int(df[field].isna().sum())
        if n:
            log.add(
                step="clean",
                rule="Q3",
                snapshot_date=snapshot_date,
                target=field,
                rows_affected=n,
                detail=f"null in {n / len(df):.1%} of rows — left null, not imputed",
            )


def check_ccn_quality(df: pd.DataFrame, snapshot_date: str, log: RunLog) -> None:
    """Rule Q1 — check that CCN is in a shape that can actually join.

    The design document's threshold is a join rate above 99%. This checks the
    source side first: 6 characters wide and unique within the period.
    """
    ccn = df["ccn"]
    bad_len = int((ccn.str.len() != config.CCN_WIDTH).sum())
    dup = int(ccn.duplicated().sum())
    log.add(
        step="clean",
        rule="Q1",
        snapshot_date=snapshot_date,
        target="ccn",
        rows_affected=len(df),
        detail=f"{len(df):,} CCN rows, {bad_len} wrong width, {dup} duplicated in period",
    )
    if bad_len or dup:
        log.add(
            step="clean",
            rule="Q1",
            snapshot_date=snapshot_date,
            target="ccn",
            rows_affected=bad_len + dup,
            detail="! malformed or duplicated CCN — must be resolved before joining",
        )
