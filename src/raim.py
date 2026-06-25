"""
raim.py — AUGUR RAIM prediction integration for NOTAMv1.

Fetches GNSS RAIM outage predictions from the EUROCONTROL AUGUR REST API
for a set of IFR aerodromes (ADEP, ADEST, alternates, ICAO waypoints).

Key design decisions:
- Single API call per briefing with all airports as one locations list.
- 30-minute cache keyed on (frozenset of airports, brief_start, brief_end).
  Cache is cleared on each new NOTAM fetch via raim.clear_cache().
- Background fetch only — never called synchronously in the HTTP request path
  since the API takes 15-30s for a typical airport set.
- Only used for IFR flights; caller is responsible for checking flight rules.
- Procedure: always RNP_APCH_03 (LNAV/VNAV), mask_angle=5.0°.
- Scenario staleness: if gps_status.end_time is more than 6 hours before
  brief_start, the result is flagged as potentially unreliable.

Config keys (config.py):
    AUGUR_USERNAME = "your@email.com"
    AUGUR_PASSWORD = "yourpassword"
"""

import datetime as dt
import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
try:
    from config import AUGUR_USERNAME, AUGUR_PASSWORD
except ImportError:
    AUGUR_USERNAME = ""
    AUGUR_PASSWORD = ""

try:
    from config import AUGUR_MASK_OVERRIDES
except ImportError:
    AUGUR_MASK_OVERRIDES = {}

BASE_URL      = "https://augur.eurocontrol.int/api/v1"
CACHE_TTL     = 30 * 60          # 30 minutes
ALGO_PARAMS_BASE = {
    "algorithm":              "FDE",
    "mask_angle":             5.0,
    "procedure":              "RNP_APCH_03",
    "selective_availability": "aware_OFF",
    # baro_aiding is set dynamically per request based on LNAV/VNAV equipment
}
# Scenario is considered stale if its end_time is more than 6h before brief_start
STALE_HORIZON = 6 * 3600

# ── Internal state ────────────────────────────────────────────────────────────
_cache_lock   = threading.Lock()
_cache        = {}    # key → {result, fetched_at}
_token        = None  # {"access": str, "expires_at": float}
_token_lock   = threading.Lock()

# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _post(url, payload, headers=None, timeout=90, retries=2):
    hdrs = dict(headers or {})
    hdrs["Content-Type"] = "application/json"
    data = json.dumps(payload).encode("utf-8")
    last_exc = None
    for attempt in range(1 + retries):
        if attempt > 0:
            wait = attempt * 10  # 10s, 20s between retries
            log.info("[raim] retry %d/%d after %ds (last: %s)", attempt, retries, wait, last_exc)
            print(f"[raim] retry {attempt}/{retries} after {wait}s", flush=True)
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < retries:
                last_exc = f"HTTP {e.code}"
                continue
            raise
        except (TimeoutError, OSError) as e:
            if attempt < retries:
                last_exc = str(e)
                continue
            raise
    raise RuntimeError(f"All retries exhausted: {last_exc}")

def _get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

# ── Auth ──────────────────────────────────────────────────────────────────────
def _get_token():
    """Return a valid JWT access token, refreshing if expired or missing."""
    global _token
    with _token_lock:
        now = time.time()
        # Access tokens from AUGUR appear to last ~5 minutes (208-char JWT);
        # refresh with 60s buffer.
        if _token and _token["expires_at"] > now + 60:
            return _token["access"]
        if not AUGUR_USERNAME or not AUGUR_PASSWORD:
            raise RuntimeError("AUGUR_USERNAME/AUGUR_PASSWORD not set in config.py")
        log.info("[raim] refreshing AUGUR JWT token")
        body = _post(f"{BASE_URL}/token/",
                     {"username": AUGUR_USERNAME, "password": AUGUR_PASSWORD},
                     timeout=15)
        access = body["access"]
        # Decode expiry from JWT payload (middle base64 segment) without PyJWT
        try:
            import base64
            seg = access.split(".")[1]
            seg += "=" * (-len(seg) % 4)  # pad to multiple of 4
            claims = json.loads(base64.urlsafe_b64decode(seg))
            exp = float(claims.get("exp", now + 300))
        except Exception:
            exp = now + 300  # 5-minute fallback
        _token = {"access": access, "expires_at": exp}
        log.info("[raim] token OK, expires in %.0fs", exp - now)
        return access

def _auth_headers():
    return {"Authorization": f"Bearer {_get_token()}"}

# ── Cache ─────────────────────────────────────────────────────────────────────
def clear_cache():
    """Call this when a new NOTAM fetch is triggered to invalidate stale results."""
    with _cache_lock:
        _cache.clear()
    log.info("[raim] cache cleared")

def _cache_key(airports, brief_start, brief_end, baro_aiding=False):
    return (frozenset(airports), int(brief_start or 0), int(brief_end or 0), baro_aiding)

# ── Core fetch ────────────────────────────────────────────────────────────────
def fetch_raim(airports, brief_start=0.0, brief_end=0.0, baro_aiding=False):
    """
    Fetch RAIM outage predictions for a list of ICAO airport codes.

    airports    : list of ICAO strings, e.g. ["LFRB", "LFBO"]
    brief_start : Unix timestamp of planned departure (UTC)
    brief_end   : Unix timestamp of planned end-of-flight (UTC)

    Returns a dict:
    {
      "ok":        bool,
      "error":     str | None,
      "airports":  {
        "LFRB": {
          "outages": [{"start": ISO8601, "end": ISO8601}, ...],
          "outages_in_window": [same, filtered to brief window],
        },
        ...
      },
      "scenario_start": ISO8601 | None,
      "scenario_end":   ISO8601 | None,
      "scenario_stale": bool,   # True if scenario ends >6h before brief_start
      "fetched_at":     float,
    }
    """
    airports = [a.upper().strip() for a in airports if a and a.strip()]
    if not airports:
        return {"ok": False, "error": "No airports provided", "airports": {},
                "scenario_start": None, "scenario_end": None,
                "scenario_stale": False, "fetched_at": time.time()}

    key = _cache_key(airports, brief_start, brief_end, baro_aiding)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and time.time() - cached["fetched_at"] < CACHE_TTL:
            log.info("[raim] cache hit for %s", airports)
            return cached["result"]

    log.info("[raim] fetching outages for %s (brief %.0f→%.0f)", airports, brief_start, brief_end)
    fetched_at = time.time()
    try:
        # Check /status/ first to know the expected fresh scenario window.
        # If the /outage/ response then returns a different (stale) scenario,
        # that confirms AUGUR backend load-balancing inconsistency.
        try:
            status_body = _get(f"{BASE_URL}/status/", headers=_auth_headers())
            expected_end = status_body.get("end_time")
        except Exception:
            expected_end = None

        # start_date is required to get predictions for the correct day.
        # Without it the API falls back to a hardcoded default (currently
        # June 15, a known bug AUGUR are fixing). Use the brief_start date
        # if available, otherwise today's UTC date.
        if brief_start:
            start_date = dt.datetime.fromtimestamp(
                brief_start, tz=dt.timezone.utc).strftime('%Y-%m-%dT00:00:00Z')
        else:
            start_date = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT00:00:00Z')
        print(f"[raim] start_date={start_date}", flush=True)

        algo_params_base = {**ALGO_PARAMS_BASE, "baro_aiding": baro_aiding}
        print(f"[raim] algo: {algo_params_base}, overrides: {AUGUR_MASK_OVERRIDES}", flush=True)

        # Group airports by effective mask angle — airports with overrides in
        # AUGUR_MASK_OVERRIDES get their own API call since the mask angle is
        # a single value per request. Capped at 12.5° (API maximum).
        from collections import defaultdict
        by_mask = defaultdict(list)
        for code in airports:
            mask = min(float(AUGUR_MASK_OVERRIDES.get(code, ALGO_PARAMS_BASE["mask_angle"])), 12.5)
            by_mask[mask].append(code)

        all_locations = []
        body = None
        for mask, group in by_mask.items():
            algo = {**algo_params_base, "mask_angle": mask}
            if len(by_mask) > 1:
                print(f"[raim] group mask={mask}°: {group}", flush=True)
            b = _post(f"{BASE_URL}/outage/", {
                "locations":  [{"code": c} for c in group],
                "algorithm":  algo,
                "start_date": start_date,
            }, headers=_auth_headers())
            all_locations.extend(b.get("locations") or [])
            if body is None:
                body = b  # keep first response for gps_status/algorithm metadata

        # Reconstruct a unified body with all locations merged
        if body is not None:
            body["locations"] = all_locations

        # Use /status/ end_time as the authoritative scenario horizon for
        # staleness checking. The gps_status embedded in the /outage/ response
        # appears to reflect the underlying almanac/computation metadata rather
        # than the prediction window itself, and is consistently older than
        # what /status/ reports -- so it's not a reliable staleness indicator.
        scenario_start = (body.get("gps_status") or {}).get("start_time")
        scenario_end   = expected_end  # from /status/, the authoritative horizon
        almanac_end    = (body.get("gps_status") or {}).get("end_time")  # almanac period

        print(f"[raim] scenario: status_end={expected_end} outage_gps_end={almanac_end}", flush=True)

        # Check for staleness: /status/ end_time vs brief_start
        stale = False
        if scenario_end and brief_start:
            try:
                end_ts = dt.datetime.fromisoformat(
                    scenario_end.replace("Z", "+00:00")).timestamp()
                stale = end_ts < brief_start - STALE_HORIZON
            except Exception:
                pass

        # Build per-airport outage map, filtering to the brief window.
        # Also record the effective mask angle used per airport.
        airport_masks = {c: min(float(AUGUR_MASK_OVERRIDES.get(c, ALGO_PARAMS_BASE["mask_angle"])), 12.5)
                         for c in airports}
        default_mask  = ALGO_PARAMS_BASE["mask_angle"]

        airport_data = {}
        for loc in body.get("locations") or []:
            code    = loc.get("code", "").upper()
            outages = loc.get("outages") or []
            in_window = []
            for o in outages:
                if brief_start and brief_end:
                    try:
                        o_start = dt.datetime.fromisoformat(
                            o["start"].replace("Z", "+00:00")).timestamp()
                        o_end   = dt.datetime.fromisoformat(
                            o["end"].replace("Z", "+00:00")).timestamp()
                        if o_start < brief_end and o_end > brief_start:
                            in_window.append(o)
                    except Exception:
                        pass
                else:
                    in_window = list(outages)
            airport_data[code] = {
                "outages":           outages,
                "outages_in_window": in_window,
                "mask_angle":        airport_masks.get(code, default_mask),
            }

        result = {
            "ok":             True,
            "error":          None,
            "airports":       airport_data,
            "scenario_start": scenario_start,
            "scenario_end":   scenario_end,
            "almanac_end":    almanac_end,
            "start_date":     start_date,
            "scenario_stale": stale,
            "baro_aiding":    baro_aiding,
            "fetched_at":     fetched_at,
        }

    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:   err_detail = json.loads(body_bytes).get("detail") or str(body_bytes[:200])
        except Exception: err_detail = body_bytes[:200].decode("utf-8", errors="replace")
        log.error("[raim] HTTP %s: %s", e.code, err_detail)
        result = {
            "ok": False, "error": f"HTTP {e.code}: {err_detail}",
            "airports": {}, "scenario_start": None, "scenario_end": None,
            "scenario_stale": False, "fetched_at": fetched_at,
        }
    except Exception as e:
        log.error("[raim] fetch failed: %s", e)
        result = {
            "ok": False, "error": str(e),
            "airports": {}, "scenario_start": None, "scenario_end": None,
            "scenario_stale": False, "fetched_at": fetched_at,
        }

    with _cache_lock:
        _cache[key] = {"result": result, "fetched_at": fetched_at}

    return result


def fetch_raim_background(airports, brief_start=0.0, brief_end=0.0,
                          baro_aiding=False, callback=None):
    """
    Trigger a background fetch (non-blocking).
    callback(result) is called when the fetch completes, if provided.
    """
    def _run():
        result = fetch_raim(airports, brief_start, brief_end, baro_aiding)
        if callback:
            try:
                callback(result)
            except Exception as e:
                log.error("[raim] callback error: %s", e)
    t = threading.Thread(target=_run, daemon=True, name="raim-fetch")
    t.start()
    return t


# ── Convenience: format a result for display ──────────────────────────────────
def fmt_outage_window(o):
    """Format a single outage dict as a human-readable UTC string."""
    try:
        s = dt.datetime.fromisoformat(o["start"].replace("Z", "+00:00"))
        e = dt.datetime.fromisoformat(o["end"].replace("Z", "+00:00"))
        dur = int((e - s).total_seconds() / 60)
        return f"{s.strftime('%H:%Mz')} – {e.strftime('%H:%Mz')} ({dur} min)"
    except Exception:
        return f"{o.get('start', '?')} – {o.get('end', '?')}"


def raim_status_for_brief(airports, brief_start, brief_end):
    """
    High-level helper: returns the RAIM result for the given briefing window,
    using cache if available. Non-blocking if cache is warm; blocking otherwise.
    Suitable for calling from the /raim endpoint.
    """
    return fetch_raim(airports, brief_start, brief_end)
