"""Extract step — discover, download and unpack CMS snapshots.

Calls archive API endpoint A for the full list of snapshots, downloads only the
periods that are missing (incremental load), then unpacks only the 6 files this
project actually uses.

Usage:
    python fetch_snapshots.py --list                 show every snapshot
    python fetch_snapshots.py --quarterly            one per quarter (default)
    python fetch_snapshots.py --dates 2019-01-17 2026-06-24
    python fetch_snapshots.py --all                  all 88 periods (~1 GB)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import zipfile
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class Snapshot:
    """One published period of CMS data."""

    date: str          # YYYY-MM-DD
    url: str           # absolute URL
    size_bytes: int
    kind: str          # theme = per period, annual_theme = yearly bundle

    @property
    def zip_path(self):
        return config.SNAPSHOT_DIR / f"nursing-homes_{self.date}.zip"

    @property
    def extract_path(self):
        return config.EXTRACT_DIR / self.date


def list_snapshots(theme_only: bool = True) -> list[Snapshot]:
    """Call endpoint A and return the snapshots oldest first.

    The endpoint returns 96 entries: 88 of type `theme` (one per published
    period) and 8 of type `annual_theme` (yearly bundles that duplicate the
    per-period ones, so they are dropped by default).
    """
    with urllib.request.urlopen(config.ARCHIVE_API, timeout=120) as resp:
        payload = json.load(resp)

    rows = payload["data"] if isinstance(payload, dict) else payload
    snapshots = []
    for row in rows:
        kind = row.get("type", "")
        if theme_only and kind != "theme":
            continue
        snapshots.append(
            Snapshot(
                date=row["date"],
                url=config.CMS_BASE + row["url"],
                size_bytes=int(row.get("size") or 0),
                kind=kind,
            )
        )
    return sorted(snapshots, key=lambda s: s.date)


def pick_quarterly(snapshots: list[Snapshot]) -> list[Snapshot]:
    """Keep the first period of each quarter — 88 periods down to about 30.

    Monthly resolution is finer than the trend questions need, and downloading
    all of it grows to roughly 1 GB.
    """
    seen: set[tuple[int, int]] = set()
    picked = []
    for s in snapshots:
        year, month = int(s.date[:4]), int(s.date[5:7])
        bucket = (year, (month - 1) // 3)
        if bucket not in seen:
            seen.add(bucket)
            picked.append(s)
    return picked


def download(snapshot: Snapshot, force: bool = False) -> bool:
    """Download one period's ZIP. True if downloaded, False if already present.

    This is the heart of the incremental load: a complete local file is skipped.
    """
    path = snapshot.zip_path
    if not force and path.exists():
        # Compare sizes too — an interrupted download leaves a short file
        if snapshot.size_bytes == 0 or path.stat().st_size == snapshot.size_bytes:
            print(f"  skip {snapshot.date} (already present)")
            return False
        print(f"  re-download {snapshot.date} (size mismatch — previous run interrupted)")

    config.ensure_dirs()
    tmp = path.with_suffix(".zip.part")
    print(f"  download {snapshot.date} ({snapshot.size_bytes / 1e6:.1f} MB) ...", flush=True)
    urllib.request.urlretrieve(snapshot.url, tmp)
    tmp.replace(path)  # only publish the final name once complete, so no half ZIPs
    return True


def classify(filename: str) -> str | None:
    """Map a file name inside the ZIP to the logical name the code uses.

    The naming scheme changed at least 3 times, so this needs per-era regexes
    rather than a glob or positional string slicing.
    """
    base = filename.rsplit("/", 1)[-1]
    for logical, pattern in config.FILE_PATTERNS.items():
        if re.search(pattern, base, flags=re.IGNORECASE):
            return logical
    return None


def extract(snapshot: Snapshot, force: bool = False) -> dict[str, str]:
    """Unpack only the files we use. Returns {logical name: extracted file name}.

    Periods with missing files are normal (2026-08-06 holds only 4 files and no
    Penalties at all), so this returns whatever is there and lets the caller
    decide whether to skip the period or just the affected fact.
    """
    out_dir = snapshot.extract_path
    out_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, str] = {}

    with zipfile.ZipFile(snapshot.zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            logical = classify(info.filename)
            if logical is None:
                continue  # the other 14 files, including HealthCitations at 165 MB
            target = out_dir / info.filename.rsplit("/", 1)[-1]
            if force or not target.exists():
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
            found[logical] = target.name

    missing = [k for k in config.REQUIRED_FOR_DIMS if k not in found]
    if missing:
        print(f"  ! period {snapshot.date} is missing required files: {missing}")
    return found


def find_file(snapshot_date: str, logical: str):
    """Locate an already extracted file for one period. None if absent."""
    out_dir = config.EXTRACT_DIR / snapshot_date
    if not out_dir.is_dir():
        return None
    for path in sorted(out_dir.iterdir()):
        if classify(path.name) == logical:
            return path
    return None


def local_snapshot_dates() -> list[str]:
    """Extracted periods on this machine, oldest first — SCD2 depends on order."""
    if not config.EXTRACT_DIR.is_dir():
        return []
    return sorted(p.name for p in config.EXTRACT_DIR.iterdir() if p.is_dir())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and unpack CMS snapshots")
    parser.add_argument("--list", action="store_true", help="list snapshots and exit")
    parser.add_argument("--all", action="store_true", help="every period (~1 GB)")
    parser.add_argument("--quarterly", action="store_true", help="one per quarter (default)")
    parser.add_argument("--dates", nargs="+", metavar="YYYY-MM-DD", help="explicit periods")
    parser.add_argument("--force", action="store_true", help="re-download and re-extract")
    args = parser.parse_args(argv)

    config.setup_console()
    config.ensure_dirs()
    print("Calling archive API endpoint A for the snapshot list ...")
    snapshots = list_snapshots()
    print(f"Found {len(snapshots)} periods from {snapshots[0].date} to {snapshots[-1].date}")

    if args.list:
        for s in snapshots:
            print(f"  {s.date}  {s.size_bytes / 1e6:8.1f} MB")
        return 0

    if args.dates:
        wanted = [s for s in snapshots if s.date in set(args.dates)]
        unknown = set(args.dates) - {s.date for s in wanted}
        if unknown:
            print(f"! unknown periods: {sorted(unknown)}", file=sys.stderr)
    elif args.all:
        wanted = snapshots
    else:
        wanted = pick_quarterly(snapshots)

    print(f"Processing {len(wanted)} periods")
    for s in wanted:
        download(s, force=args.force)
        found = extract(s, force=args.force)
        print(f"  {s.date}: extracted {len(found)} files {sorted(found)}")

    print("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
