"""Paths and constants shared by every module in the ETL.

Rule: no path or URL is hard-coded anywhere else. Everything lives here so that
moving the project to another machine means editing exactly one file.
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------
#  Paths — resolved from this file's location, not the working directory,
#  because the scripts get launched from several places (VS Code, shell, tasks)
# ---------------------------------------------------------------------
ETL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ETL_DIR.parent

RAW_DIR = PROJECT_DIR / "01_Raw_Data"
SNAPSHOT_DIR = RAW_DIR / "cms_snapshots"          # downloaded ZIP files
EXTRACT_DIR = SNAPSHOT_DIR / "_extracted"         # unpacked CSVs (git ignored)
EXTERNAL_DIR = RAW_DIR / "external"               # population data etc.

WAREHOUSE_DIR = PROJECT_DIR / "03_Data_Warehouse"
DB_PATH = WAREHOUSE_DIR / "eldercare.duckdb"

LOG_DIR = ETL_DIR / "_logs"                       # ETL run log (git ignored)

# ---------------------------------------------------------------------
#  Sources
# ---------------------------------------------------------------------
CMS_BASE = "https://data.cms.gov"

# Endpoint A — the list of every archived snapshot CMS still publishes.
#
# The `/relative` variant this used to point at now answers 200 with
# `{"data": [], "total_items": 0}`: a successful-looking empty list, not an
# error. That silently capped the project at the four snapshots already on
# disk and is why 2020-2022 looked unobtainable. Without the suffix the same
# endpoint returns all 88 monthly archives, 2019-01-17 to 2026-08-06.
#
# Checked 22 Aug 2026. If this ever returns an empty list again, treat it as a
# fault and not as "CMS has no archives" — `fetch_snapshots.py` refuses to
# proceed on an empty list for exactly that reason.
ARCHIVE_API = f"{CMS_BASE}/provider-data/api/1/archive/aggregate/theme/nursing-homes"

# Endpoint C — current data. Only usable to validate the most recent period.
PROVIDER_INFO_DATASTORE = f"{CMS_BASE}/provider-data/api/1/datastore/query/4pq5-n9py/0"

# ---------------------------------------------------------------------
#  The 6 of 20 files this project actually uses.
#  Keys are the logical names the code refers to; values are regexes.
#
#  File names went through at least 3 naming eras, so plain glob cannot match:
#    2019          ProviderInfo_Download.csv
#    2026 normal   NH_ProviderInfo_Jun2026.csv
#    2026 some     4pq5-n9py_2026-07-29_NH_ProviderInfo_Jul2026.csv
# ---------------------------------------------------------------------
#  Two of these need more than a prefix and a month suffix, because 2019 renamed
#  the file itself rather than just decorating it:
#    state averages   StateAverages_Download.csv  ->  NH_StateUSAverages_Jun2026.csv
#                     (the "US" was added later, so it has to be optional)
#    VBP              SNF VBP Facility Performance.csv  (spaces, no FY prefix)
#                     ->  FY_2026_SNF_VBP_Facility_Performance.csv
#  "Facility" stays mandatory in the VBP pattern so it cannot match the
#  FY_YYYY_SNF_VBP_Aggregate_Performance.csv sitting beside it.
FILE_PATTERNS = {
    "provider_info": r"(?:^|_)(?:NH_)?ProviderInfo(?:_Download)?(?:_[A-Za-z]{3}\d{4})?\.csv$",
    "penalties": r"(?:^|_)(?:NH_)?Penalties(?:_Download)?(?:_[A-Za-z]{3}\d{4})?\.csv$",
    "survey_summary": r"(?:^|_)(?:NH_)?SurveySummary(?:_Download)?(?:_[A-Za-z]{3}\d{4})?\.csv$",
    "citations": r"(?:^|_)(?:NH_)?CitationDescriptions(?:_Download)?(?:_[A-Za-z]{3}\d{4})?\.csv$",
    "state_averages": r"(?:^|_)(?:NH_)?State(?:US)?Averages(?:_Download)?(?:_[A-Za-z]{3}\d{4})?\.csv$",
    "vbp": r"(?:FY[_ ]\d{4}[_ ])?SNF[_ ]VBP[_ ]Facility[_ ]Performance\.csv$",
}

# Without this file a period cannot produce dimensions at all — skip the period.
REQUIRED_FOR_DIMS = ["provider_info"]

# ---------------------------------------------------------------------
#  Reading files (rule Q1)
#  CMS CSVs are not pure UTF-8, and CCN has leading zeros, so every column
#  is read as text first and only genuinely numeric ones are converted later.
# ---------------------------------------------------------------------
CSV_ENCODING = "latin-1"
CCN_WIDTH = 6

# ---------------------------------------------------------------------
#  Dim_Date — has to reach further back than expected, because the
#  "first approved" date of some facilities goes back to the 1960s
# ---------------------------------------------------------------------
DATE_DIM_START = "1960-01-01"
DATE_DIM_END = "2027-12-31"
UNKNOWN_DATE_KEY = -1

# ---------------------------------------------------------------------
#  Canary row — always tied to a specific period. The CSV value and the API
#  value differ because they come from different periods.
# ---------------------------------------------------------------------
CANARY_CCN = "015009"
CANARY_SNAPSHOT = "2026-06-24"
CANARY_BEDS = 57
CANARY_RESIDENTS = 52.4
CANARY_OCCUPANCY = 0.9193  # 52.4 / 57 from the Jun 2026 CSV, not the API's 90.5%


def setup_console() -> None:
    """Force stdout to UTF-8.

    The Windows console defaults to cp1252, so printing Thai text kills the
    script with UnicodeEncodeError even when the pipeline itself is fine.
    Every entry point must call this first.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def ensure_dirs() -> None:
    """Create the directories the ETL writes into."""
    for d in (SNAPSHOT_DIR, EXTRACT_DIR, EXTERNAL_DIR, WAREHOUSE_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
