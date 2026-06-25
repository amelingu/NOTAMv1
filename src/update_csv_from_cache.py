#!/usr/bin/env python3
"""
Regenerate data/zones_RTBA_openAIP.csv from the CURRENT CACHE
(data/azba_zones_cache.json) -- i.e. write the freshest known-good zone
data back out to CSV, in the exact same format the original openAIP export
used (semicolon-delimited, DMS coordinates with decimal-second precision,
Windows line endings, UTF-8 BOM), so the result is a drop-in replacement
that NOTAMv1's existing parser (azba.parse_csv) reads identically to a
real openAIP export.

This is most useful right after a successful openAIP refresh (check the
cache's meta.last_refresh_success), since that's when the cache actually
holds independently-verified data worth promoting back into the CSV.

This same logic (azba.export_csv_from_cache) is also exposed from the app
itself via a button shown when a discrepancy is found -- this script is
just a manual/CLI way to trigger the same thing.

Usage (from the NOTAMv1 project root):
    python3 update_csv_from_cache.py            # writes to data/zones_RTBA_openAIP.csv
    python3 update_csv_from_cache.py --dry-run   # prints what would change, writes nothing
    python3 update_csv_from_cache.py --out other.csv
"""
import sys
import os
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
import azba


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default=azba.SEED_CSV, help='Output CSV path (default: data/zones_RTBA_openAIP.csv)')
    ap.add_argument('--dry-run', action='store_true', help="Print a summary without writing any file")
    args = ap.parse_args()

    cache = azba._load_cache_raw()
    if not cache or not cache.get('zones'):
        print("No cache found -- nothing to export. Run the server once (or azba.load_zones()) first.")
        sys.exit(1)

    zones = cache['zones']
    meta = cache.get('meta', {})
    openaip_count = sum(1 for z in zones.values() if z.get('source') == 'openaip')
    print(f"Cache holds {len(zones)} zones ({openaip_count} openAIP-verified, "
          f"{len(zones) - openaip_count} CSV-only).")
    if meta.get('last_refresh_success'):
        print(f"Last successful openAIP refresh: matched {meta.get('last_refresh_matched')}, "
              f"updated {meta.get('last_refresh_updated')}.")
    else:
        print("No successful openAIP refresh on record -- exporting will mostly just "
              "reformat the existing CSV-sourced data, not add new verified data.")
    print()

    if args.dry_run:
        print(f"[dry run] Would write {len(zones)} rows to {args.out}")
        for name in sorted(zones.keys())[:3]:
            z = zones[name]
            print(f"  {name}: floor={azba.format_limit(z['floor'])} ceiling={azba.format_limit(z['ceiling'])} "
                  f"vertices={len(z['polygon'])} arc={azba.infer_has_arc(len(z['polygon']))}")
        print("  ...")
        return

    result = azba.export_csv_from_cache(out_path=args.out)
    if not result['ok']:
        print(f"Export failed: {result['error']}")
        sys.exit(1)

    if result['backup_path']:
        print(f"Backed up existing CSV to {result['backup_path']}")
    print(f"Wrote {result['zones_written']} zones to {args.out}")

    if result['mismatches']:
        print(f"\nWARNING: {len(result['mismatches'])} round-trip mismatch(es) detected:")
        for m in result['mismatches'][:20]:
            print("  -", m)
    else:
        print("Round-trip check passed: re-parsing the new CSV reproduces the exported data "
              "(within decimal-second DMS rounding, ~15cm max).")


if __name__ == '__main__':
    main()
