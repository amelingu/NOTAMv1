"""
AZBA / RTBA (French military low-altitude training network) zone data.

Source of truth: a CSV exported from openAIP (zones_RTBA_openAIP.csv),
containing zone name, common name, floor/ceiling, vertex count, an "arc"
flag, and a DMS-format coordinate list. RTBA section boundaries are fixed
and published (not daily-changing), so this is parsed once and cached to
disk -- not re-fetched on every server start.

Periodic refresh against the openAIP API (to catch any AIRAC-cycle-driven
boundary changes) is a separate concern, layered on top of this cache --
see refresh_from_openaip() below.

Naming convention: each RTBA section is referenced internally with an "LF"
ICAO-region prefix prepended to its bare zone number, e.g. zone "R57"
becomes "LFR57". This mirrors how French airspace is generally referenced.
The bare name (no prefix) is kept alongside it for matching against the
SIA schedule page, which uses bare names like "R45A", "R69".
"""
import csv
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The cache file is the live, working copy of zone data. The CSV is only
# used to seed it the first time (or via an explicit re-seed), since the
# cache is what later gets updated by the openAIP refresh.
DATA_DIR    = os.path.join(ROOT, 'data')
CACHE_FILE  = os.path.join(DATA_DIR, 'azba_zones_cache.json')
SEED_CSV    = os.path.join(DATA_DIR, 'zones_RTBA_openAIP.csv')

# AIRAC cycles are 28 days. Re-check for updates this often at most.
AIRAC_CYCLE_SECONDS = 28 * 86400

DMS_RE = re.compile(r"""(\d+)\s*°\s*(\d+)\s*'\s*(\d+(?:\.\d+)?)\s*"\s*([NSEW])""")

# Floor is always either bare ground or "800ft AGL" in the source data.
# Ceiling is always "<N>ft AGL", "<N>ft AMSL", or "FL<NN>".
_LIMIT_RE = re.compile(r"""^(?:(\d+)\s*ft\s*(AGL|AMSL)|FL\s*(\d+)|SFC)$""", re.IGNORECASE)


def parse_dms(token: str) -> float:
    """Parse a single DMS token like 47°38'4"N into signed decimal degrees."""
    m = DMS_RE.match(token.strip())
    if not m:
        raise ValueError(f"Could not parse DMS token: {token!r}")
    deg, minutes, seconds, hemi = m.groups()
    val = float(deg) + float(minutes) / 60 + float(seconds) / 3600
    if hemi in ('S', 'W'):
        val = -val
    return val


def parse_coord_list(raw: str):
    """Parse 'DD°MM'SS"N , DDD°MM'SS"E - DD°MM'SS"N , DDD°MM'SS"E - ...'
    into a list of [lat, lon] pairs (decimal degrees)."""
    pairs = []
    for chunk in raw.split(' - '):
        chunk = chunk.strip()
        if not chunk:
            continue
        lat_str, lon_str = chunk.split(',')
        pairs.append([parse_dms(lat_str), parse_dms(lon_str)])
    return pairs


def parse_limit(raw: str) -> dict:
    """Normalize a floor/ceiling string into {value, unit, reference}.
    unit: 'ft' or 'FL'. reference: 'GND', 'AGL', 'AMSL', or 'STD' (for FL).
    SFC (surface) is represented as {value: 0, unit: 'ft', reference: 'GND'}.
    """
    raw = raw.strip()
    if raw.upper() == 'SFC':
        return {'value': 0, 'unit': 'ft', 'reference': 'GND'}
    m = _LIMIT_RE.match(raw)
    if not m:
        raise ValueError(f"Could not parse altitude limit: {raw!r}")
    ft_value, ft_ref, fl_value = m.groups()
    if fl_value is not None:
        return {'value': int(fl_value), 'unit': 'FL', 'reference': 'STD'}
    return {'value': int(ft_value), 'unit': 'ft', 'reference': ft_ref.upper()}


def format_limit(limit: dict) -> str:
    """Inverse of parse_limit, for display purposes."""
    if limit['unit'] == 'FL':
        return f"FL{limit['value']:03d}"
    if limit['value'] == 0 and limit['reference'] == 'GND':
        return 'SFC'
    return f"{limit['value']}ft {limit['reference']}"


# ── CSV export (cache -> CSV, the reverse direction of parse_csv) ───────────
#
# Used both by the standalone update_csv_from_cache.py script and by the
# server's /azba/update-csv endpoint (triggered from the UI when a
# discrepancy is found). Both call export_csv_from_cache() so there's a
# single implementation to keep correct.

def format_dms(decimal_deg: float, is_lat: bool, decimals: int = 2) -> str:
    """Inverse of parse_dms -- decimal degrees back to DD°MM'SS.ss"H
    format, keeping decimal precision in the seconds field.

    Whole-number seconds (the original openAIP export's convention) lose
    up to ~15m of precision on round-trip -- enough to trip the
    discrepancy check's ~10m polygon tolerance even when a zone hasn't
    actually changed. parse_dms already accepts decimal seconds, so
    keeping them here is a strict precision improvement.
    """
    hemi = ('N' if decimal_deg >= 0 else 'S') if is_lat else ('E' if decimal_deg >= 0 else 'W')
    abs_deg = abs(decimal_deg)
    deg = int(abs_deg)
    minutes_full = (abs_deg - deg) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60, decimals)
    if seconds >= 60:
        seconds -= 60
        minutes += 1
    if minutes >= 60:
        minutes -= 60
        deg += 1
    seconds_str = f"{seconds:.{decimals}f}".rstrip('0').rstrip('.')
    if seconds_str == '' or seconds_str == '-0':
        seconds_str = '0'
    return f"{deg}°{minutes}'{seconds_str}\"{hemi}"


def format_coord_list(polygon) -> str:
    """Inverse of parse_coord_list."""
    return ' - '.join(
        f"{format_dms(lat, is_lat=True)} , {format_dms(lon, is_lat=False)}"
        for lat, lon in polygon
    )


def infer_has_arc(vertex_count: int, threshold: int = 50) -> str:
    """Heuristic matching the original CSV's apparent convention: zones
    with a circular boundary segment get densified into many points by
    openAIP. Not a perfect reconstruction from geometry alone, but a high
    vertex count is a reasonable proxy (the 6 known arc zones all have
    200+ vertices vs. a handful for simple polygons)."""
    return 'Oui' if vertex_count >= threshold else 'Non'


def export_csv_from_cache(out_path: str = SEED_CSV, make_backup: bool = True) -> dict:
    """Write the current cache's zone data back out to CSV, in the same
    format as the original openAIP export (semicolon-delimited, DMS
    coordinates with decimal seconds, CRLF line endings, UTF-8 BOM) --
    a drop-in replacement for data/zones_RTBA_openAIP.csv.

    Backs up any existing file at out_path + '.bak' first (unless
    make_backup is False). Performs a round-trip sanity check after
    writing (re-parses its own output and compares against what was
    exported) and includes the result in the returned report.

    Returns: {ok, zones_written, backup_path, mismatches, error}
    """
    cache = _load_cache_raw()
    if not cache or not cache.get('zones'):
        return {'ok': False, 'error': 'No cache to export from', 'zones_written': 0,
                'backup_path': None, 'mismatches': []}

    zones = cache['zones']
    fieldnames = [
        'Nom de zone', 'Nom usuel', 'Plancher', 'Plafond', 'Nb sommets',
        'Contient un arc (limite circulaire densifiée)',
        'Liste de coordonnées (telle que publiée, source openAIP/AIP)',
    ]

    import io
    buf = io.StringIO()
    buf.write(';'.join(fieldnames) + '\r\n')
    for name in sorted(zones.keys()):
        z = zones[name]
        polygon = z['polygon']
        coord_field = format_coord_list(polygon)
        escaped_coord = '"' + coord_field.replace('"', '""') + '"'
        row_values = [
            name, z['common_name'], format_limit(z['floor']), format_limit(z['ceiling']),
            str(len(polygon)), infer_has_arc(len(polygon)), escaped_coord,
        ]
        buf.write(';'.join(row_values) + '\r\n')

    backup_path = None
    try:
        if make_backup and os.path.exists(out_path):
            backup_path = out_path + '.bak'
            with open(out_path, 'rb') as src, open(backup_path, 'wb') as dst:
                dst.write(src.read())

        with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
            f.write(buf.getvalue())
    except Exception as ex:
        return {'ok': False, 'error': str(ex), 'zones_written': 0,
                'backup_path': backup_path, 'mismatches': []}

    # Round-trip sanity check
    mismatches = []
    try:
        reparsed = parse_csv(out_path)
        for name, z in zones.items():
            rz = reparsed.get(name)
            if not rz:
                mismatches.append(f"{name}: missing from re-parsed output")
                continue
            if rz['floor'] != z['floor']:
                mismatches.append(f"{name}: floor mismatch after round-trip")
            if rz['ceiling'] != z['ceiling']:
                mismatches.append(f"{name}: ceiling mismatch after round-trip")
            if len(rz['polygon']) != len(z['polygon']):
                mismatches.append(f"{name}: vertex count mismatch after round-trip")
            else:
                for (lat1, lon1), (lat2, lon2) in zip(rz['polygon'], z['polygon']):
                    if abs(lat1 - lat2) > 1e-6 or abs(lon1 - lon2) > 1e-6:
                        mismatches.append(f"{name}: polygon point mismatch after round-trip")
                        break
    except Exception as ex:
        mismatches.append(f"Round-trip re-parse failed: {ex}")

    return {
        'ok': True, 'error': None, 'zones_written': len(zones),
        'backup_path': backup_path, 'mismatches': mismatches,
    }


def parse_csv(path: str = SEED_CSV) -> dict:
    """Parse the openAIP-sourced RTBA CSV into a dict keyed by bare zone name."""
    zones = {}
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            name = row['Nom de zone'].strip()
            polygon = parse_coord_list(row['Liste de coordonnées (telle que publiée, source openAIP/AIP)'])
            zones[name] = {
                'name':        name,                   # bare name, e.g. "R45A"
                'lf_name':     'LF' + name,             # LF-prefixed, e.g. "LFR45A"
                'common_name': row['Nom usuel'].strip(),
                'floor':       parse_limit(row['Plancher']),
                'ceiling':     parse_limit(row['Plafond']),
                'has_arc':     row['Contient un arc (limite circulaire densifiée)'].strip().lower() == 'oui',
                'polygon':     polygon,                 # list of [lat, lon], closed ring
                'source':      'csv',
                'updated_at':  None,
            }
    return zones


# ── Cache lifecycle ──────────────────────────────────────────────────────────

def _load_cache_raw() -> dict:
    """Read the cache file as-is. Returns {} if missing or corrupt."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CACHE_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CACHE_FILE)  # atomic on POSIX; avoids a half-written cache file


def seed_cache_from_csv(path: str = SEED_CSV, force: bool = False) -> dict:
    """Create the cache file from the CSV if it doesn't exist yet (or if
    force=True). Returns the resulting cache structure (zones + metadata).
    Does NOT overwrite an existing cache unless force=True -- the cache is
    the live copy and may have been updated by refresh_from_openaip() since
    the CSV was last relevant.
    """
    existing = _load_cache_raw()
    if existing and not force:
        return existing

    zones = parse_csv(path)
    cache = {
        'zones': zones,
        'meta': {
            'seeded_from': os.path.basename(path),
            'seeded_at':   time.time(),
            'last_refresh_attempt': None,
            'last_refresh_success': None,
        },
    }
    _save_cache(cache)
    return cache


def load_zones(auto_seed: bool = True) -> dict:
    """Return the current zones dict (bare name -> zone data), loading from
    the persistent cache. If the cache doesn't exist yet and auto_seed is
    True, it is created from the bundled CSV first.
    """
    cache = _load_cache_raw()
    if not cache and auto_seed:
        cache = seed_cache_from_csv()
    return cache.get('zones', {})


def cache_meta() -> dict:
    cache = _load_cache_raw()
    return cache.get('meta', {})


def needs_refresh() -> bool:
    """True if it's been at least one AIRAC cycle since the last successful
    refresh attempt (or if there's no record of one yet)."""
    meta = cache_meta()
    last = meta.get('last_refresh_attempt')
    if not last:
        return True
    return (time.time() - last) > AIRAC_CYCLE_SECONDS


# ── CSV vs. cache discrepancy check ──────────────────────────────────────────
#
# The cache is the live working copy (it may have drifted from the CSV via
# an openAIP refresh). The CSV on disk is what the user would manually
# replace if it's gone stale. This check re-parses the CSV fresh -- without
# touching the cache -- and reports any zone where floor/ceiling/polygon
# differ from what's currently cached, so the user can be told "the cache
# (trusted, openAIP-derived) disagrees with your CSV file -- consider
# updating it."

def _polygon_differs(a, b, tolerance_deg: float = 1e-4) -> bool:
    """Compare two polygons for meaningful difference, allowing tiny
    floating-point/rounding noise (tolerance_deg ~ 1e-4 degrees is roughly
    10 meters -- comfortably tighter than any real boundary change, but
    loose enough to ignore rounding artifacts from DMS<->decimal conversion
    or unit conversion through openAIP).
    """
    if len(a) != len(b):
        return True
    for (lat1, lon1), (lat2, lon2) in zip(a, b):
        if abs(lat1 - lat2) > tolerance_deg or abs(lon1 - lon2) > tolerance_deg:
            return True
    return False


def check_csv_discrepancy(csv_path: str = SEED_CSV) -> dict:
    """Re-parse the CSV fresh and compare it against the current cache --
    but ONLY for zones whose cached data actually came from an independent
    source (openAIP), not from the CSV itself.

    This distinction matters: if the cache was seeded (or re-seeded) from
    the CSV, comparing the CSV against that cache entry is comparing the
    CSV against a copy of itself -- it can never detect that the CSV has
    gone stale, no matter how wrong the CSV is. Only a zone that has been
    through a successful openAIP refresh (source == 'openaip') represents
    a second, independent opinion worth comparing the CSV against.

    Returns:
        {ok, checked_at, differing, unverified_count, verified_count, error}
    'differing' lists zones where the CSV disagrees with openAIP-sourced
    cache data. 'unverified_count' is how many zones in the CSV have never
    been independently checked (cache source is still 'csv') -- a high
    unverified_count with zero differing zones means "nothing has actually
    been checked yet", not "everything is confirmed correct". The caller
    (UI) should distinguish these states rather than report a blanket
    "up to date".

    Never modifies the cache or the CSV -- read-only on both sides. Does
    NOT auto-seed a missing cache (see needs_refresh / load_zones for that)
    -- a missing cache is reported as a hard error, since seeding it here
    from this very CSV would make any subsequent comparison meaningless.
    """
    try:
        csv_zones = parse_csv(csv_path)
    except Exception as ex:
        return {'ok': False, 'error': str(ex), 'checked_at': time.time(),
                'differing': [], 'unverified_count': 0, 'verified_count': 0}

    raw_cache = _load_cache_raw()
    if not raw_cache or not raw_cache.get('zones'):
        return {
            'ok': False,
            'error': 'No cache exists yet to compare against -- reload the page or restart the server to rebuild it.',
            'checked_at': time.time(),
            'differing': [], 'unverified_count': 0, 'verified_count': 0,
        }
    cached_zones = raw_cache['zones']
    differing = []
    unverified_count = 0
    verified_count = 0

    for name, csv_z in csv_zones.items():
        cache_z = cached_zones.get(name)
        if not cache_z:
            differing.append({
                'name': name, 'lf_name': csv_z['lf_name'],
                'fields': ['missing_from_cache'],
                'csv': {'floor': csv_z['floor'], 'ceiling': csv_z['ceiling']},
                'cache': None,
            })
            continue

        if cache_z.get('source') != 'openaip':
            # This zone has never been independently verified -- the cache
            # is just a copy of some CSV (possibly this very one). Comparing
            # would be meaningless, so we count it separately rather than
            # silently treating it as "matches".
            unverified_count += 1
            continue

        verified_count += 1
        fields = []
        if csv_z['floor'] != cache_z['floor']:
            fields.append('floor')
        if csv_z['ceiling'] != cache_z['ceiling']:
            fields.append('ceiling')
        if _polygon_differs(csv_z['polygon'], cache_z['polygon']):
            fields.append('polygon')

        if fields:
            differing.append({
                'name': name, 'lf_name': csv_z['lf_name'],
                'fields': fields,
                'csv':   {'floor': csv_z['floor'],   'ceiling': csv_z['ceiling']},
                'cache': {'floor': cache_z['floor'], 'ceiling': cache_z['ceiling']},
            })

    # Zones present in the cache but absent from the CSV entirely (e.g. CSV
    # was trimmed) are also worth flagging -- only meaningful for
    # openAIP-sourced entries, same reasoning as above.
    for name, cache_z in cached_zones.items():
        if name not in csv_zones and cache_z.get('source') == 'openaip':
            differing.append({
                'name': name, 'lf_name': cache_z['lf_name'],
                'fields': ['missing_from_csv'],
                'csv': None,
                'cache': {'floor': cache_z['floor'], 'ceiling': cache_z['ceiling']},
            })

    return {
        'ok': True, 'error': None, 'checked_at': time.time(),
        'differing': differing,
        'unverified_count': unverified_count,
        'verified_count': verified_count,
    }


# ── openAIP refresh ──────────────────────────────────────────────────────────

OPENAIP_AIRSPACES_URL = 'https://api.core.openaip.net/api/airspaces'
OPENAIP_RESTRICTED_TYPE = 1  # "Restricted" airspace type, per openAIP's enum
OPENAIP_PAGE_LIMIT = 200


def _extract_bare_name(openaip_name: str, known_names) -> 'str | None':
    """Given an openAIP airspace name like 'LF-R45N2 ARDENNES (MON-FRI)',
    extract the bare RTBA zone code (e.g. 'R45N2') IF AND ONLY IF it exactly
    matches one of our already-known zone names. This is deliberately
    conservative: we never add zones we don't already track, and we never
    guess at a fuzzy match -- a wrong match would silently corrupt geometry
    for the wrong zone, which is worse than missing an update entirely.
    """
    name = openaip_name.strip()
    name = re.sub(r'^LF-?\s*', '', name, flags=re.IGNORECASE)
    parts = name.split()
    token = parts[0] if parts else ''
    return token if token in known_names else None


def _openaip_limit_to_internal(limit_obj) -> dict:
    """Convert an openAIP vertical limit object
    ({value, unit: 0|1|6, referenceDatum: 0|1|2}) into our internal
    {value, unit: 'ft'|'FL', reference: 'GND'|'AGL'|'AMSL'|'STD'} shape.
    Note: openAIP doesn't distinguish GND from AGL in referenceDatum (both
    map to its enum value 0) -- we keep 'GND' only for an explicit value=0,
    otherwise 'AGL', matching how the CSV source already records SFC vs
    '800ft AGL'.
    """
    value = limit_obj.get('value', 0)
    unit_code = limit_obj.get('unit', 1)
    ref_code  = limit_obj.get('referenceDatum', 0)

    if unit_code == 6:  # Flight Level
        return {'value': value, 'unit': 'FL', 'reference': 'STD'}

    # unit_code 0 = Meter, 1 = Feet -- the CSV source is always feet, and
    # openAIP's French RTBA entries are expected to be too, but convert
    # defensively just in case a future entry comes back in meters.
    ft_value = value if unit_code == 1 else round(value * 3.28084)

    if ref_code == 1:
        reference = 'AMSL'
    elif ref_code == 2:
        reference = 'STD'
    else:
        reference = 'GND' if ft_value == 0 else 'AGL'

    return {'value': int(ft_value), 'unit': 'ft', 'reference': reference}


def _openaip_geometry_to_polygon(geometry: dict):
    """Convert an openAIP GeoJSON Polygon geometry
    (coordinates: [[[lon, lat], [lon, lat], ...]]) into our internal
    [[lat, lon], [lat, lon], ...] polygon format (note the swapped order)."""
    rings = geometry.get('coordinates') or []
    if not rings:
        return []
    outer_ring = rings[0]  # we only use the outer ring; RTBA zones have no holes
    return [[pt[1], pt[0]] for pt in outer_ring]


def _fetch_openaip_airspaces(api_key: str, timeout: int = 20):
    """Fetch all French Restricted airspaces from openAIP, paginating as
    needed. Returns a list of raw airspace dicts. Raises on network/auth
    failure -- callers should catch and record this without touching the
    existing cache.

    Sends the API key BOTH as the 'apiKey' query parameter (the method
    openAIP's own blog documents explicitly: .../airports?apiKey=YOUR_KEY)
    and as the 'x-openaip-api-key' header, since different parts of
    openAIP's own documentation/community disagree on the exact expected
    method -- sending both is harmless and maximizes the chance of actually
    authenticating instead of getting a silent 403.
    """
    import urllib.request
    import urllib.error
    import urllib.parse
    import json as _json

    results = []
    page = 1
    while True:
        qs = urllib.parse.urlencode({
            'country': 'FR',
            'type':    OPENAIP_RESTRICTED_TYPE,
            'page':    page,
            'limit':   OPENAIP_PAGE_LIMIT,
            'apiKey':  api_key,
        })
        url = f'{OPENAIP_AIRSPACES_URL}?{qs}'
        req = urllib.request.Request(url, headers={
            'x-openaip-api-key': api_key,
            # openAIP sits behind Cloudflare. A bare urllib request with no
            # User-Agent (or Python's default 'Python-urllib/x.y') gets
            # blocked by Cloudflare's bot protection with HTTP 403 / Ray
            # error code 1010 -- this has nothing to do with the API key
            # being right or wrong, Cloudflare rejects it before the
            # request ever reaches openAIP's own application code.
            'User-Agent': 'Mozilla/5.0 (compatible; notam-server/2.0; +https://github.com/amelingu/NOTAMv1)',
            'Accept': 'application/json',
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = _json.loads(resp.read())
        except urllib.error.HTTPError as e:
            # Surface whatever openAIP actually said, not just "403
            # Forbidden" -- their error body usually explains why (bad key,
            # wrong format, no permission for this endpoint, etc).
            try:
                detail = e.read().decode('utf-8', errors='replace')[:500]
            except Exception:
                detail = ''
            raise RuntimeError(f'HTTP {e.code} {e.reason}' + (f' -- {detail}' if detail else '')) from e

        # openAIP's list responses are expected to carry the page of items
        # under 'items' (typical for this API style) -- be tolerant of a
        # bare list too, in case the exact wrapper key differs.
        items = body.get('items') if isinstance(body, dict) else body
        if not items:
            break
        results.extend(items)

        if len(items) < OPENAIP_PAGE_LIMIT:
            break  # last page
        page += 1
        if page > 20:  # safety valve -- French restricted airspaces won't paginate this deep
            break

    return results


def refresh_from_openaip(api_key: str) -> dict:
    """Refresh cached zone geometry/floor/ceiling from openAIP, for zones we
    already know about (see _extract_bare_name -- never adds new zones).

    On any failure (network, auth, parsing), the existing cache is left
    completely untouched and the failure is recorded in cache metadata.
    Returns a small report dict: {ok, matched, updated, error}.
    """
    meta_update = {'last_refresh_attempt': time.time()}

    if not api_key:
        _record_refresh_meta(meta_update, success=False, error='No OPENAIP_API_KEY configured')
        return {'ok': False, 'matched': 0, 'updated': 0, 'error': 'No OPENAIP_API_KEY configured'}

    cache = _load_cache_raw()
    zones = cache.get('zones', {})
    if not zones:
        # Nothing to refresh against -- seed first, then bail (caller can
        # re-run refresh afterward if desired).
        seed_cache_from_csv()
        cache = _load_cache_raw()
        zones = cache.get('zones', {})

    known_names = set(zones.keys())

    try:
        airspaces = _fetch_openaip_airspaces(api_key)
    except Exception as ex:
        _record_refresh_meta(meta_update, success=False, error=str(ex))
        return {'ok': False, 'matched': 0, 'updated': 0, 'error': str(ex)}

    matched = 0
    updated = 0
    for item in airspaces:
        raw_name = item.get('name', '')
        bare_name = _extract_bare_name(raw_name, known_names)
        if not bare_name:
            continue
        matched += 1

        try:
            geometry = item.get('geometry') or {}
            polygon  = _openaip_geometry_to_polygon(geometry)
            floor    = _openaip_limit_to_internal(item.get('lowerLimit', {}))
            ceiling  = _openaip_limit_to_internal(item.get('upperLimit', {}))
        except Exception:
            # Skip this single zone on a parse error rather than aborting
            # the whole refresh -- one malformed entry shouldn't block
            # updates to the other 64 zones.
            continue

        if not polygon or len(polygon) < 3:
            continue  # refuse to overwrite good geometry with something degenerate

        existing = zones[bare_name]
        existing['polygon']    = polygon
        existing['floor']      = floor
        existing['ceiling']    = ceiling
        existing['source']     = 'openaip'
        existing['updated_at'] = time.time()
        updated += 1

    cache['zones'] = zones
    _record_refresh_meta(meta_update, success=True, error=None, extra={
        'last_refresh_matched': matched,
        'last_refresh_updated': updated,
    })
    cache['meta'] = {**cache.get('meta', {}), **meta_update}
    _save_cache(cache)

    return {'ok': True, 'matched': matched, 'updated': updated, 'error': None}


def _record_refresh_meta(meta_update: dict, success: bool, error, extra: dict = None):
    """Helper to merge refresh-attempt bookkeeping into the cache's meta
    block without disturbing the zones themselves. Always persists, even
    on failure, so needs_refresh() reflects reality and repeated failures
    don't retry every single request."""
    meta_update['last_refresh_success'] = success
    meta_update['last_refresh_error']   = error
    if extra:
        meta_update.update(extra)

    cache = _load_cache_raw()
    if not cache:
        # No cache exists at all yet -- nothing meaningful to attach this
        # metadata to; the next load_zones() call will seed fresh anyway.
        return
    cache['meta'] = {**cache.get('meta', {}), **meta_update}
    _save_cache(cache)


# ── SIA AZBA activation schedule (SOFIA API) ─────────────────────────────────
#
# Activation data is fetched from the JSON REST API behind the SIA's azbaEx
# widget (https://www.sia.aviation-civile.gouv.fr/azbaEx/), discovered by
# inspecting the widget's XHR calls in browser DevTools. Two endpoints:
#
#   1. GET /api/v3/custom/currentDate
#      Returns the currently-published AZBA window: {rtba, startDate, endDate}
#      where endDate is the exact cutoff beyond which activations aren't yet known.
#
#   2. GET /api/v3/r_t_b_as?itemsPerPage=600
#                           &debutIntervalTemps=<ISO8601 UTC>
#                           &finIntervalTemps=<ISO8601 UTC>
#      Returns a Hydra collection of active RTBA zones for the given interval:
#      {hydra:totalItems, hydra:member: [{zone entry}, ...]}
#
# Both require Basic auth with a hardcoded read-only credential embedded in
# the azbaEx frontend JS -- this is intentional and public (the widget works
# without any user account), equivalent to a public API key for anonymous
# read access to a government open-data service.

SOFIA_API_BASE  = 'https://bo-prod-sofia-vac.sia-france.fr'
SOFIA_API_AUTH  = 'Basic YXBpOkw0YjZQIWQ5K1l1aUc4LU0='  # api:L4b6P!d9+YuiG8-M, embedded in azbaEx JS
SOFIA_DATE_URL  = f'{SOFIA_API_BASE}/api/v3/custom/currentDate'
SOFIA_RTBA_URL  = f'{SOFIA_API_BASE}/api/v3/r_t_b_as'
SOFIA_OFFICIAL_MAP_URL = 'https://www.sia.aviation-civile.gouv.fr/azbaEx/'

SCHEDULE_CACHE_TTL = 15 * 60   # 15 minutes

_schedule_cache = {'fetched_at': 0.0, 'result': None}
_publication_window_cache = {'fetched_at': 0.0, 'window': None}


_SOFIA_SHARE_SECRET = "Y9Q3Ve72nN3PnTXmEtKnS4sggmdsigRMWH9kCDGHpCHyenFKKGhDq5vgBWZ4"


def _sofia_auth_token(url: str) -> str:
    """Generate the per-URL AUTH token the azbaEx widget sends on every API
    request, replicating the JS logic found in the widget's bundle:
        base64(JSON.stringify({ tokenUri: sha512(share_secret + "/api/" + path_after_api) }))
    The share_secret is hardcoded in the widget's environment config.
    """
    import hashlib as _hashlib
    import base64 as _base64
    import json as _json
    path_after_api = url.split("/api/")[1]
    payload   = _SOFIA_SHARE_SECRET + "/api/" + path_after_api
    token_uri = _hashlib.sha512(payload.encode()).hexdigest()
    return _base64.b64encode(
        _json.dumps({"tokenUri": token_uri}, separators=(',', ':')).encode()
    ).decode()


def _sofia_request(url: str, timeout: int = 15) -> dict:
    """Make an authenticated GET request to the SOFIA API, returning the
    parsed JSON body. Raises on any network/HTTP error.

    Sends both the Basic auth credential (hardcoded in the azbaEx JS) and
    the per-URL AUTH token (computed from the share_secret + URL path via
    SHA-512, also hardcoded in the widget), exactly as the browser does.
    """
    import urllib.request
    import json as _json
    auth_token = _sofia_auth_token(url)
    req = urllib.request.Request(url, headers={
        'Authorization': SOFIA_API_AUTH,
        'AUTH':          auth_token,
        'Accept':        'application/json',
        'User-Agent':    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0',
        'Origin':        'https://www.sia.aviation-civile.gouv.fr',
        'Referer':       'https://www.sia.aviation-civile.gouv.fr/',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _json.loads(resp.read())


def fetch_publication_window(force: bool = False) -> 'dict | None':
    """Fetch the currently-published AZBA time window from the SOFIA API.
    Returns {rtba, startDate, endDate} as returned by /api/v3/custom/currentDate,
    or None on failure. Cached for SCHEDULE_CACHE_TTL.

    endDate is the precise cutoff: anything beyond it is genuinely not yet
    published (not a guess -- it's the server's own stated boundary).
    """
    print(f'[azba-schedule] fetch_publication_window called (force={force}), url={SOFIA_DATE_URL}', flush=True)
    now = time.time()
    if not force and _publication_window_cache['window'] is not None and \
            (now - _publication_window_cache['fetched_at']) < SCHEDULE_CACHE_TTL:
        return _publication_window_cache['window']
    try:
        window = _sofia_request(SOFIA_DATE_URL)
        _publication_window_cache.update(fetched_at=now, window=window)
        print(f"[azba-schedule] publication window: {window.get('startDate')} -> {window.get('endDate')}", flush=True)
        return window
    except Exception as ex:
        import traceback
        import urllib.error as _ue
        print(f'[azba-schedule] fetch_publication_window FAILED', flush=True)
        print(f'[azba-schedule]   exception type: {type(ex).__name__}', flush=True)
        print(f'[azba-schedule]   exception detail: {ex}', flush=True)
        if isinstance(ex, _ue.HTTPError):
            print(f'[azba-schedule]   HTTP status: {ex.code} {ex.reason}', flush=True)
            try:
                body = ex.read().decode('utf-8', errors='replace')
                print(f'[azba-schedule]   response body (first 500 chars): {body[:500]}', flush=True)
            except Exception:
                pass
        print(f'[azba-schedule]   URL attempted: {SOFIA_DATE_URL}', flush=True)
        traceback.print_exc()
        return _publication_window_cache.get('window')  # return stale if available


def fetch_schedule(force: bool = False) -> dict:
    """Fetch active RTBA zones for the current publication window.

    Calls /api/v3/r_t_b_as with debutIntervalTemps = now and
    finIntervalTemps = endDate from the publication window (i.e. "what's
    active between now and the end of what's published?").

    Returns:
        {ok, available, entries, error, fetched_at, source_url,
         publication_window, debug}

    Each entry in 'entries' is:
        {zone, lf_name, debut, fin}
    where debut/fin are ISO8601 UTC strings as returned by the API.

    NOTE: the exact field names on each zone member entry are not yet
    confirmed from a live non-empty response -- 'identifiant', 'debut'/'fin'
    are reasonable guesses based on the API naming we've seen. This will
    need to be verified and adjusted once a real active-zones response is
    observed (when some zones are actually activated).
    """
    now = time.time()
    if not force and _schedule_cache['result'] is not None and \
            (now - _schedule_cache['fetched_at']) < SCHEDULE_CACHE_TTL:
        return _schedule_cache['result']

    import urllib.parse
    import urllib.error
    import datetime as _dt
    debug = {'attempted_at': now}

    # Step 1: get the publication window
    window = fetch_publication_window(force=force)
    debug['publication_window_url'] = SOFIA_DATE_URL
    if not window:
        result = {
            'ok': False, 'available': False, 'entries': [],
            'error': 'Could not fetch publication window from SOFIA API',
            'fetched_at': now, 'source_url': SOFIA_OFFICIAL_MAP_URL,
            'publication_window': None, 'debug': debug,
        }
        _schedule_cache.update(fetched_at=now, result=result)
        return result

    # Step 2: query active RTBA zones for [now, endDate]
    now_iso = _dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S+00:00')
    end_iso  = window.get('endDate', '')
    qs = urllib.parse.urlencode({
        'itemsPerPage':       600,
        'debutIntervalTemps': now_iso,
        'finIntervalTemps':   end_iso,
    })
    url = f'{SOFIA_RTBA_URL}?{qs}'
    debug['url'] = url

    t0 = time.time()
    try:
        data = _sofia_request(url)
        debug['elapsed_seconds'] = round(time.time() - t0, 2)
        debug['http_status'] = 200
        # The API may return either a Hydra collection {hydra:member: [...]}
        # or a plain JSON array [...] depending on the endpoint version/config.
        # Handle both defensively.
        if isinstance(data, list):
            raw_members = data
            debug['response_shape'] = f'plain list, {len(data)} items'
        else:
            raw_members = data.get('hydra:member', [])
            debug['response_shape'] = f'hydra dict, totalItems={data.get("hydra:totalItems")}'
        debug['total_items'] = len(raw_members)
        print(f'[azba-schedule] r_t_b_as response: {debug["response_shape"]}', flush=True)
        if raw_members:
            print(f'[azba-schedule] first member keys: {list(raw_members[0].keys())}', flush=True)
    except urllib.error.HTTPError as e:
        debug['elapsed_seconds'] = round(time.time() - t0, 2)
        debug['http_status'] = e.code
        try:
            debug['response_snippet'] = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            pass
        error_msg = f'HTTP {e.code} {e.reason}'
        print(f'[azba-schedule] FETCH FAILED: {error_msg} | debug: {debug}', flush=True)
        result = {
            'ok': False, 'available': False, 'entries': [], 'error': error_msg,
            'fetched_at': now, 'source_url': SOFIA_OFFICIAL_MAP_URL,
            'publication_window': window, 'debug': debug,
        }
        _schedule_cache.update(fetched_at=now, result=result)
        return result
    except Exception as ex:
        debug['elapsed_seconds'] = round(time.time() - t0, 2)
        debug['exception_type'] = type(ex).__name__
        print(f'[azba-schedule] FETCH FAILED: {ex} | debug: {debug}', flush=True)
        result = {
            'ok': False, 'available': False, 'entries': [], 'error': str(ex),
            'fetched_at': now, 'source_url': SOFIA_OFFICIAL_MAP_URL,
            'publication_window': window, 'debug': debug,
        }
        _schedule_cache.update(fetched_at=now, result=result)
        return result

    # Known naming discrepancies between the AZBA/SIA system (codeId field)
    # and the openAIP data we use for zone geometry.
    # Format: {AZBA_bare_name: openAIP_bare_name}
    # The AZBA name is what pilots/ATC use and should be displayed;
    # the openAIP name is what our zone geometry cache uses as its key.
    AZBA_NAME_ALIASES = {
        'R145B': 'R145',   # SIA calls it R145B; openAIP has it as R145
    }

    # Parse members into confirmed field names from the real API response.
    # Actual structure: {codeId: "LFR139A", timeSlots: [{startTime, endTime}, ...], ...}
    # Each zone may have multiple timeSlots (e.g. two separate activation windows).
    known_names = set(load_zones().keys())
    entries = []
    for item in raw_members:
        code_id = item.get('codeId', '')  # e.g. "LFR139A" or "LFR145B"
        bare_azba = re.sub(r'^LF-?\s*', '', str(code_id), flags=re.IGNORECASE).strip()
        # Resolve alias: find the matching openAIP zone name for geometry lookup
        bare_openaip = AZBA_NAME_ALIASES.get(bare_azba, bare_azba)
        if not bare_openaip or bare_openaip not in known_names:
            print(f'[azba-schedule] unrecognised zone codeId={code_id!r} bare_azba={bare_azba!r} bare_openaip={bare_openaip!r}', flush=True)
            bare_openaip = bare_azba or 'UNKNOWN'
        # Display the AZBA official name (from codeId), not the openAIP name
        display_lf = code_id if code_id else f'LF{bare_azba}'
        for slot in item.get('timeSlots', []):
            entries.append({
                'zone':         bare_openaip,   # key for geometry/map lookup
                'zone_azba':    bare_azba,       # official AZBA name for display
                'lf_name':      display_lf,      # e.g. "LFR145B"
                'debut':        slot.get('startTime', ''),
                'fin':          slot.get('endTime',   ''),
            })

    total = len(raw_members)
    print(f'[azba-schedule] OK: {total} active zone(s), window {now_iso} -> {end_iso}', flush=True)
    print(f'[azba-schedule] parsed entries (first 3): {entries[:3]}', flush=True)

    result = {
        'ok': True, 'available': True, 'entries': entries, 'error': None,
        'fetched_at': now, 'source_url': SOFIA_OFFICIAL_MAP_URL,
        'publication_window': window, 'debug': debug,
    }
    _schedule_cache.update(fetched_at=now, result=result)
    return result


def find_intersecting_zones(schedule_entries, window_start_utc, window_end_utc, ref_date_utc=None):
    """Given parsed schedule entries (from fetch_schedule) and a flight
    time window [window_start_utc, window_end_utc) as datetime.datetime
    (UTC, naive or aware -- treated as UTC), return the subset of entries
    whose activation period overlaps that window.

    Entries currently carry only a time-of-day (no confirmed date field --
    see _parse_schedule_html's note), so for now this assumes all entries
    apply to ref_date_utc (defaulting to window_start_utc's date) and
    compares time-of-day overlap only. This is a reasonable approximation
    for typical short VFR/IFR flight windows that don't span midnight, but
    should be revisited once we can see the page's real date-grouping
    structure and attach a proper date to each entry.
    """
    import datetime as _dt

    if ref_date_utc is None:
        ref_date_utc = window_start_utc.date()

    intersecting = []
    for entry in schedule_entries:
        sh, sm = map(int, entry['start_utc'].split(':'))
        eh, em = map(int, entry['end_utc'].split(':'))
        slot_start = _dt.datetime.combine(ref_date_utc, _dt.time(sh, sm))
        slot_end   = _dt.datetime.combine(ref_date_utc, _dt.time(eh, em))
        if slot_end <= slot_start:
            slot_end += _dt.timedelta(days=1)  # overnight slot

        if slot_start < window_end_utc and slot_end > window_start_utc:
            intersecting.append(entry)

    return intersecting


if __name__ == '__main__':
    # Quick manual sanity check when run directly: python3 src/azba.py
    zones = load_zones()
    print(f"Loaded {len(zones)} zones from cache (auto-seeded if needed).")
    for name in ('R45A', 'R57', 'R69'):
        z = zones.get(name)
        if z:
            print(f"  {name} -> {z['lf_name']}: floor={format_limit(z['floor'])} "
                  f"ceiling={format_limit(z['ceiling'])} vertices={len(z['polygon'])}")
    print("Cache meta:", cache_meta())
