"""Source S4 — population aged 65 and over, by state and year.

This is the only source outside CMS. It is what turns "how many beds are there"
into "how many beds are there *per elderly person*", which is the demand side of
the market and the whole point of BQ1. CMS only ever looks at providers.

Two ways in, tried in this order:

  1. **Census ACS API** — needs a free key in the CENSUS_API_KEY environment
     variable. Without one the server answers HTTP 302 to a `missing_key.html`
     page, so a client that does not follow redirects sees an empty body and no
     error at all. That is exactly how this source looked "broken" for a while.
  2. **Population Estimates CSV** — a static file with no key system anywhere
     near it. Used automatically when there is no key.

Either way the numbers land in the same shape, so the warehouse does not care
which one ran, and the run log records which one it was.

    python population.py            load, preferring the API if a key is set
    python population.py --fallback force the no-key CSV

Grain: one row per (state, year). **Not** stored on Dim_Geography: that table is
at (ZIP, city, state) grain, so writing a state population onto every ZIP row
would let a careless SUM multiply the population of Texas by the number of ZIP
codes in it. It lives in its own table at its own grain instead, and BQ1 joins to
it on state.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import urllib.request

import duckdb
import pandas as pd

import config
from runlog import RunLog

TABLE = "Ref_State_Population"

# ---------------------------------------------------------------------
#  Census ACS — table B01001 splits people by sex and by narrow age bands,
#  so "65 and over" has to be assembled from twelve variables. Verified
#  against the published variable list for B01001.
# ---------------------------------------------------------------------
ACS_YEAR = 2023
ACS_BASE = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"
ACS_MALE_65PLUS = ["B01001_020E", "B01001_021E", "B01001_022E",
                   "B01001_023E", "B01001_024E", "B01001_025E"]
ACS_FEMALE_65PLUS = ["B01001_044E", "B01001_045E", "B01001_046E",
                     "B01001_047E", "B01001_048E", "B01001_049E"]
ACS_VARIABLES = ACS_MALE_65PLUS + ACS_FEMALE_65PLUS

# ---------------------------------------------------------------------
#  Fallback — Population Estimates, civilian population by age and sex.
#  SEX=0 is both sexes, AGE=999 is the all-ages total row that must be
#  excluded or every state doubles.
# ---------------------------------------------------------------------
POPEST_URL = (
    "https://www2.census.gov/programs-surveys/popest/datasets/"
    "2020-2024/state/asrh/sc-est2024-agesex-civ.csv"
)
POPEST_YEARS = [2020, 2021, 2022, 2023, 2024]

STATE_FIPS_TO_CODE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "72": "PR",
}


def _download(url: str, timeout: int = 180) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def from_acs_api(log: RunLog) -> pd.DataFrame | None:
    """Fetch 65+ by state from the ACS API. None if there is no key."""
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        log.add(
            step="extract",
            rule="S4",
            target=TABLE,
            detail=(
                "CENSUS_API_KEY is not set — the ACS API answers 302 to "
                "missing_key.html without one, so using the no-key fallback file"
            ),
        )
        return None

    url = f"{ACS_BASE}?get=NAME,{','.join(ACS_VARIABLES)}&for=state:*&key={key}"
    payload = json.loads(_download(url))
    header, *rows = payload
    raw = pd.DataFrame(rows, columns=header)

    counts = raw[ACS_VARIABLES].apply(pd.to_numeric, errors="coerce")
    out = pd.DataFrame({
        "state_fips": raw["state"],
        "state_name": raw["NAME"],
        "year": ACS_YEAR,
        "pop_65plus": counts.sum(axis=1),
        "source": f"ACS 5-year {ACS_YEAR} table B01001",
    })
    log.add(
        step="extract",
        rule="S4",
        target=TABLE,
        rows_affected=len(out),
        detail=f"ACS API returned {len(out)} states, summed from {len(ACS_VARIABLES)} variables",
    )
    return out


def from_popest_csv(log: RunLog) -> pd.DataFrame:
    """Fetch 65+ by state and year from the static Population Estimates file."""
    config.ensure_dirs()
    cached = config.EXTERNAL_DIR / "sc-est2024-agesex-civ.csv"
    if cached.exists():
        body = cached.read_bytes()
        log.add(step="extract", rule="S4", target=TABLE,
                detail=f"using the cached copy of {cached.name} (incremental load)")
    else:
        body = _download(POPEST_URL)
        cached.write_bytes(body)
        log.add(step="extract", rule="S4", target=TABLE,
                rows_affected=len(body),
                detail=f"downloaded {cached.name} ({len(body) / 1e6:.1f} MB), no API key needed")

    raw = pd.read_csv(io.BytesIO(body), dtype=str)
    numeric = raw.assign(
        SEX=pd.to_numeric(raw["SEX"], errors="coerce"),
        AGE=pd.to_numeric(raw["AGE"], errors="coerce"),
    )
    # SEX=0 is both sexes together; AGE=999 is the all-ages total row. Keeping
    # either alongside the detail rows would double or triple the count.
    elderly = numeric[
        (numeric["SEX"] == 0) & (numeric["AGE"] >= 65) & (numeric["AGE"] != 999)
    ].copy()

    pieces = []
    for year in POPEST_YEARS:
        column = f"POPEST{year}_CIV"
        if column not in elderly.columns:
            continue
        totals = (
            elderly.assign(pop=pd.to_numeric(elderly[column], errors="coerce"))
            .groupby(["STATE", "NAME"], as_index=False)["pop"].sum()
        )
        pieces.append(totals.assign(year=year))

    stacked = pd.concat(pieces, ignore_index=True)
    out = pd.DataFrame({
        "state_fips": stacked["STATE"],
        "state_name": stacked["NAME"],
        "year": stacked["year"],
        "pop_65plus": stacked["pop"],
        "source": "Census Population Estimates sc-est2024-agesex-civ",
    })
    # SUMLEV 010 is the national row; it has FIPS 00 and is not a state
    out = out[out["state_fips"].astype("string").str.strip().str.zfill(2) != "00"]
    log.add(
        step="extract",
        rule="S4",
        target=TABLE,
        rows_affected=len(out),
        detail=(
            f"{out['state_fips'].nunique()} states x {out['year'].nunique()} years "
            f"from the fallback file"
        ),
    )
    return out


def build(force_fallback: bool = False, log: RunLog | None = None) -> pd.DataFrame:
    """Return one row per (state, year), whichever source was available."""
    log = log or RunLog()
    frame = None if force_fallback else from_acs_api(log)
    if frame is None:
        frame = from_popest_csv(log)

    frame = frame.copy()
    # Rule Q1 again, from a different direction: the Population Estimates file
    # writes state FIPS without the leading zero, so Alabama is `1` and not `01`.
    # Mapping the raw value silently loses every state numbered 1 to 9 — Alabama,
    # Alaska, Arizona, Arkansas, California, Colorado, Connecticut — and the
    # national total quietly drops by about 11 million people with no error.
    frame["state_fips"] = frame["state_fips"].astype("string").str.strip().str.zfill(2)
    frame["state_code"] = frame["state_fips"].map(STATE_FIPS_TO_CODE)

    unmapped = sorted(frame.loc[frame["state_code"].isna(), "state_name"].unique())
    if unmapped:
        log.add(
            step="clean",
            rule="Q1",
            target=TABLE,
            rows_affected=len(unmapped),
            detail=f"dropped rows whose FIPS code is not a state: {unmapped[:5]}",
        )
    frame = frame[frame["state_code"].notna()]

    cols = ["state_code", "state_name", "year", "pop_65plus", "source"]
    out = (
        frame[cols]
        .sort_values(["state_code", "year"])
        .reset_index(drop=True)
    )
    out["pop_65plus"] = pd.to_numeric(out["pop_65plus"], errors="coerce").astype("int64")
    return out


def write(frame: pd.DataFrame, log: RunLog) -> None:
    config.ensure_dirs()
    con = duckdb.connect(str(config.DB_PATH))
    try:
        con.register("_pop", frame)
        con.execute(f"CREATE OR REPLACE TABLE {TABLE} AS SELECT * FROM _pop")
        con.unregister("_pop")
    finally:
        con.close()
    log.add(
        step="load",
        rule="write_table",
        target=TABLE,
        rows_affected=len(frame),
        detail=f"wrote {TABLE} into {config.DB_PATH.name}",
    )


def validate(frame: pd.DataFrame, log: RunLog) -> list[str]:
    """Sanity checks with answers known in advance."""
    problems = []

    duplicated = int(frame.duplicated(subset=["state_code", "year"]).sum())
    if duplicated:
        problems.append(f"{TABLE}: (state_code, year) duplicated on {duplicated} rows")

    latest = frame[frame["year"] == frame["year"].max()]
    n_states = latest["state_code"].nunique()
    if n_states < 51:
        problems.append(f"{TABLE}: only {n_states} states in the latest year, expected 51+")

    # The United States had roughly 61 million people aged 65+ in the mid 2020s.
    # A total far outside that range means the age bands were summed wrongly —
    # the most likely mistake being to leave the all-ages total row in.
    total = int(latest.loc[latest["state_code"] != "PR", "pop_65plus"].sum())
    if not 50e6 < total < 75e6:
        problems.append(
            f"{TABLE}: 50 states + DC total {total:,} is outside the plausible 50-75M range"
        )

    log.add(
        step="load",
        rule="post_load_check",
        target=TABLE,
        rows_affected=len(frame),
        detail=(
            f"{n_states} states in {int(latest['year'].iloc[0])}, "
            f"65+ total {total:,}, {len(problems)} problems"
        ),
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load population aged 65+ by state")
    parser.add_argument("--fallback", action="store_true",
                        help="skip the API and use the no-key CSV")
    args = parser.parse_args(argv)

    config.setup_console()
    log = RunLog()
    frame = build(force_fallback=args.fallback, log=log)
    problems = validate(frame, log)

    if problems:
        log.save_csv()
        print("\n! problems found — nothing was written to the database")
        for p in problems:
            print(f"  - {p}")
        return 2

    write(frame, log)
    csv_path = log.save_csv()
    print("\n" + "=" * 62)
    print(f"  {TABLE:<24} {len(frame):>9,} rows")
    print(f"  run log:  {csv_path.name}")
    print("\nAll checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
