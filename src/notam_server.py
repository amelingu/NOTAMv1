#!/usr/bin/env python3
"""
NOTAM Briefing Server.
Serves the HTML briefing on http://localhost:<PORT> and acts as a CORS proxy
for api.autorouter.aero, the OurAirports CSV, and CartoCDN map tiles.
Also exposes /snapshot/* and /never/* persistence APIs (JSON files in snapshots/).

Start via the shell launcher (start_notam_linux.sh), not directly.
"""
import sys
sys.dont_write_bytecode = True  # no __pycache__/*.pyc for this process or
                                 # anything it imports below (azba, config,
                                 # etc.) -- must be set before those imports
import base64
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import EMAIL, PASSWORD, PORT
try:
    from config import GITHUB_REPO, GITHUB_TOKEN, GITHUB_BRANCH
except ImportError:
    GITHUB_REPO = GITHUB_TOKEN = GITHUB_BRANCH = ''
try:
    from config import ANTHROPIC_API_KEY
except ImportError:
    ANTHROPIC_API_KEY = ''
try:
    from config import OPENAIP_API_KEY
except ImportError:
    OPENAIP_API_KEY = ''
try:
    from config import AUGUR_USERNAME, AUGUR_PASSWORD
except ImportError:
    AUGUR_USERNAME = AUGUR_PASSWORD = ''

import azba
import raim

# ── Paths ─────────────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)   # project root (parent of src/)

def _github_enabled() -> bool:
    return bool(GITHUB_REPO and GITHUB_TOKEN)

def _git_blob_sha(content: str) -> str:
    """Compute the git blob SHA-1 — identical to GitHub's file SHA."""
    import hashlib
    data   = content.encode('utf-8')
    header = ('blob ' + str(len(data)) + chr(0)).encode('utf-8')
    return hashlib.sha1(header + data).hexdigest()

def _github_get_tree() -> dict:
    """Fetch the full snapshot tree from GitHub. Returns {rel_path: sha}."""
    branch = GITHUB_BRANCH or 'main'
    try:
        tree = _github_api('GET', f'git/trees/{branch}?recursive=1')
        return {f['path']: f['sha']
                for f in tree.get('tree', [])
                if f['path'].startswith('snapshots/') and f['path'].endswith('.json')}
    except Exception:
        return {}

def _github_api(method, path, body=None):
    url  = f'https://api.github.com/repos/{GITHUB_REPO}/{path}'
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', f'token {GITHUB_TOKEN}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, context=_ssl_ctx, timeout=15) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else {}

def github_pull():
    if not _github_enabled():
        return {'status': 'disabled'}
    try:
        remote_tree = _github_get_tree()  # one API call: all remote SHAs
        updated = 0
        for rel_path, remote_sha in remote_tree.items():
            if rel_path.endswith('_prefs.json'):
                continue  # handled separately by _pull_prefs_body
            local = os.path.join(ROOT, rel_path)
            # Compare local SHA with remote SHA before fetching content
            try:
                local_content = open(local, encoding='utf-8').read()
                if _git_blob_sha(local_content) == remote_sha:
                    continue  # identical — skip
            except FileNotFoundError:
                pass  # file doesn't exist locally — must fetch
            # Content differs or missing — fetch and write
            blob    = _github_api('GET', f'git/blobs/{remote_sha}')
            content = base64.b64decode(blob['content']).decode('utf-8')
            os.makedirs(os.path.dirname(local), exist_ok=True)
            with open(local, 'w', encoding='utf-8') as fh:
                fh.write(content)
            updated += 1
        return {'status': 'ok', 'files': len(remote_tree), 'updated': updated}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def github_push(changed_paths):
    if not _github_enabled():
        return {'status': 'disabled'}
    try:
        branch      = GITHUB_BRANCH or 'main'
        remote_tree = _github_get_tree()  # one API call: all remote SHAs
        pushed = 0
        for local_path in changed_paths:
            rel = os.path.relpath(local_path, ROOT).replace(os.sep, '/')
            try:
                content = open(local_path, encoding='utf-8').read()
            except FileNotFoundError:
                continue
            local_sha  = _git_blob_sha(content)
            remote_sha = remote_tree.get(rel)
            if local_sha == remote_sha:
                continue  # identical — skip
            encoded = base64.b64encode(content.encode()).decode()
            body = {'message': f'NOTAMv1: update {rel}', 'content': encoded, 'branch': branch}
            if remote_sha:
                body['sha'] = remote_sha  # required by GitHub API for updates
            _github_api('PUT', f'contents/{rel}', body)
            pushed += 1
        return {'status': 'ok', 'pushed': pushed}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def github_delete(rel_path: str) -> dict:
    """Delete a file from GitHub. Silently succeeds if file doesn't exist."""
    if not _github_enabled():
        return {'status': 'disabled'}
    try:
        branch = GITHUB_BRANCH or 'main'
        # Get file SHA — required by GitHub DELETE API
        try:
            file_info = _github_api('GET', f'contents/{rel_path}?ref={branch}')
            sha = file_info.get('sha')
        except Exception:
            return {'status': 'ok', 'deleted': False}  # already gone
        body = {'message': f'NOTAMv1: flush {rel_path}', 'sha': sha, 'branch': branch}
        _github_api('DELETE', f'contents/{rel_path}', body)
        return {'status': 'ok', 'deleted': True}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

def github_push_all() -> dict:
    """Push all local snapshot files to GitHub."""
    if not _github_enabled():
        return {'status': 'disabled'}
    snap_dir = os.path.join(ROOT, 'snapshots')
    if not os.path.isdir(snap_dir):
        return {'status': 'ok', 'pushed': 0}
    paths = [os.path.join(snap_dir, f)
             for f in os.listdir(snap_dir)
             if f.endswith('.json') and not f.endswith('_prefs.json')]
    return github_push(paths)

def _load_maprender() -> str:
    path = os.path.join(HERE, 'maprender.js')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return '/* maprender.js not found */'

MAPRENDER_JS = _load_maprender()

def _find_html():
    """Locate notam_briefing_v1.html: script dir, cwd, or Android ~/Download variants."""
    name = 'notam_briefing_v1.html'
    proj = 'NOTAMv1'
    candidates = [
        os.path.join(ROOT, name),
        os.path.join(HERE, name),
        os.path.join(os.getcwd(), name),
        os.path.join(os.path.expanduser('~'), 'downloads', proj, name),
        os.path.join(os.path.expanduser('~'), 'Download',  proj, name),
        os.path.join(os.path.expanduser('~'), 'Downloads', proj, name),
        os.path.join(os.path.expanduser('~'), 'storage', 'downloads', proj, name),
        os.path.join('/sdcard/Download', proj, name),
        os.path.join(os.path.expanduser('~'), 'downloads', name),
        os.path.join(os.path.expanduser('~'), 'Download',  name),
        os.path.join(os.path.expanduser('~'), 'Downloads', name),
        os.path.join('/sdcard/Download', name),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]  # will produce a clear FileNotFoundError on first use

HTML_FILE    = _find_html()
SNAPSHOT_DIR = os.path.join(ROOT, 'snapshots')

# ── SSL context (prefer certifi bundle if available) ──────────────────────────

try:
    import certifi
    _ssl_ctx = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _ssl_ctx = ssl.create_default_context()

_orig_urlopen = urllib.request.urlopen

def _urlopen(req, **kw):
    if 'context' not in kw:
        kw['context'] = _ssl_ctx
    return _orig_urlopen(req, **kw)

urllib.request.urlopen = _urlopen

# ── Constants ─────────────────────────────────────────────────────────────────

CORS = {
    'Access-Control-Allow-Origin':  '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
}

AIRPORTS_URL    = 'https://davidmegginson.github.io/ourairports-data/airports.csv'
AIRPORTS_TTL    = 7 * 86400   # 7-day cache
TILE_SUBDOMAINS = ['a', 'b', 'c', 'd']

# ── In-memory caches ──────────────────────────────────────────────────────────

_airports_csv_cache: bytes = b''
_airports_csv_ts:    float = 0.0
_tile_idx:           int   = 0

# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence access log

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _send_headers(self, code: int, ctype: str):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, obj):
        data = json.dumps(obj).encode()
        self._send_headers(200, 'application/json')
        self.wfile.write(data)

    def _error(self, code: int, msg: str):
        self._send_headers(code, 'text/plain')
        self.wfile.write(msg.encode())

    def _read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(length)

    # ── Routing ───────────────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self._send_headers(204, 'text/plain')

    def do_GET(self):
        path = self.path.split('?')[0].rstrip('/')
        if   path in ('', '/', '/index.html', '/notam'): self._serve_html()
        elif path == '/airports-csv':                     self._proxy_airports()
        elif path == '/sup-check':                        self._sup_check()
        elif path.startswith('/ar/'):                     self._proxy_ar_get()
        elif path.startswith('/tile/'):                   self._proxy_tile(path)
        elif path == '/ai/config':   self._json({'enabled': bool(ANTHROPIC_API_KEY)})
        elif path == '/sync/pull':      self._json(github_pull())
        elif path == '/sync/pull-prefs': self._json(self._pull_prefs_body())
        elif path == '/sync/config':  self._json({'enabled': _github_enabled(), 'repo': GITHUB_REPO, 'branch': GITHUB_BRANCH or 'main'})
        elif path == '/map':                               self._serve_map()
        elif path == '/help':                              self._serve_help()
        elif path == '/azba/zones':                        self._azba_zones()
        elif path == '/azba/diff':                         self._azba_diff()
        elif path == '/azba/schedule':                     self._azba_schedule()
        elif path == '/raim/status':                       self._raim_status()
        else:                                             self._error(404, 'Not found')

    def do_POST(self):
        path = self.path.split('?')[0]
        if   path.startswith('/ar/'):                                        self._proxy_ar_post(path)
        elif path in ('/never/load', '/never/add', '/never/remove'):         self._handle_never(path)
        elif path in ('/prefs/load', '/prefs/save'):                              self._handle_prefs(path)
        elif path in ('/snapshot/load', '/snapshot/save',
                      '/snapshot/ack',  '/snapshot/flush'):                  self._handle_snapshot(path)
        elif path == '/snapshot/flush-all': self._json(self._flush_all_body())
        elif path == '/ai/summarise':   self._json(self._ai_summarise())
        elif path == '/ai/save_key':    self._json(self._ai_save_key())
        elif path == '/sync/push':      self._json(self._sync_push_body())
        elif path == '/sync/push-all':  self._json(github_push_all()); return  # ignore body
        elif path == '/azba/refresh':   self._json(azba.refresh_from_openaip(OPENAIP_API_KEY))
        elif path == '/azba/update-csv': self._json(azba.export_csv_from_cache())
        elif path == '/raim/fetch':     self._raim_fetch()
        else:                                                                 self._error(404, 'Not found')

    # ── GET handlers ──────────────────────────────────────────────────────────

    def _serve_html(self):
        try:
            data = _read_html_injected()
        except PermissionError:
            self._error(403, f'Cannot read {HTML_FILE}\n'
                             'On Android (Termux), move files to ~/downloads/ and retry.')
            return
        except FileNotFoundError:
            self._error(404, f'File not found: {HTML_FILE}\n'
                             'Make sure notam_briefing_v1.html is next to notam_server.py')
            return
        self.send_response(200)
        self.send_header('Content-Type',  'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma',        'no-cache')
        self.send_header('Expires',       '0')
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _flush_all_body(self) -> dict:
        try:
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            return flush_all_snapshots(body.get('email', ''))
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _sync_push_body(self) -> dict:
        try:
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            paths  = [os.path.join(ROOT, p) for p in body.get('paths', [])
                      if not os.path.isabs(p)]
            return github_push(paths)
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _ai_save_key(self) -> dict:
        """Write/update ANTHROPIC_API_KEY in config.py."""
        try:
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            key    = (body.get('api_key') or '').strip()
            if not key:
                return {'status': 'error', 'message': 'No API key provided'}

            config_path = os.path.join(ROOT, 'config.py')
            if not os.path.exists(config_path):
                return {'status': 'error', 'message': 'config.py not found'}

            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            pattern = re.compile(r"^\s*ANTHROPIC_API_KEY\s*=")
            new_line = f"ANTHROPIC_API_KEY = {key!r}\n"
            found = False
            for i, line in enumerate(lines):
                if pattern.match(line):
                    lines[i] = new_line
                    found = True
                    break
            if not found:
                if lines and not lines[-1].endswith('\n'):
                    lines[-1] += '\n'
                lines.append('\n# -- AI NOTAM summaries --\n')
                lines.append(new_line)

            with open(config_path, 'w', encoding='utf-8') as f:
                f.writelines(lines)

            print('  [ai] ANTHROPIC_API_KEY written to config.py', flush=True)
            return {'status': 'ok'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _ai_summarise(self) -> dict:
        """Call Anthropic API to summarise NOTAMs for one airfield."""
        if not ANTHROPIC_API_KEY:
            return {'status': 'disabled'}
        try:
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length)) if length else {}
            icao     = body.get('icao', '').upper()
            notams   = body.get('notams', [])   # list of dicts with metadata, pre-sorted by priority
            ai_lines = int(body.get('ai_lines', 2))
            ai_chars = int(body.get('ai_chars', 80))
            acft_cat = body.get('acft_cat', 'A').upper()
            rules    = body.get('rules', 'IFR').upper()
            dep_date = body.get('dep_date', '')
            dep_time = body.get('dep_time', '')
            max_dur  = body.get('max_dur', '')
            max_dur_is_auto = bool(body.get('max_dur_is_auto'))
            equipment = body.get('equipment', [])  # e.g. ['LPV', 'LNAV']
            brief_start = body.get('brief_start') or 0
            brief_end   = body.get('brief_end') or 0
            if not icao or not notams:
                return {'status': 'ok', 'summary': 'No significant NOTAM'}

            def _fmt(n):
                if isinstance(n, dict):
                    flags = []
                    if n.get('mapped'):   flags.append('[MAPPED]')
                    if n.get('closure'):  flags.append('[CLOSURE]')
                    if n.get('purpose') == 'B': flags.append('[PROCEDURE]')
                    # Indicate if the NOTAM's validity covers only part of the flight window
                    sv, ev = n.get('startvalidity'), n.get('endvalidity')
                    if brief_start and brief_end and sv is not None and ev is not None:
                        if sv > brief_start or ev < brief_end:
                            import datetime as _dt
                            sv_s = _dt.datetime.fromtimestamp(sv, _dt.timezone.utc).strftime('%H:%MZ') if sv > brief_start else 'start'
                            ev_s = _dt.datetime.fromtimestamp(ev, _dt.timezone.utc).strftime('%H:%MZ') if ev < brief_end else 'end'
                            flags.append(f'[PARTIAL: {sv_s}-{ev_s} of flight]')
                    prefix = ' '.join(flags) + ' ' if flags else ''
                    return prefix + n.get('text', '')
                return str(n)
            notam_text = '\n'.join(f'- {_fmt(n)}' for n in notams[:20])
            truncated  = len(notams) > 20
            max_chars  = ai_lines * ai_chars
            max_tok    = max(300, int(max_chars * 2) + 100)

            dur_desc = (f'{max_dur} (auto-calculated)' if max_dur_is_auto and max_dur
                        else f'{max_dur} (manually specified)' if max_dur
                        else 'not specified')

            equip_desc = ', '.join(equipment) if equipment else 'none ticked'

            prompt = (
                "I am a pilot planning a flight in the conditions defined by the flight "
                "parameters: I will take off on "
                f"{dep_date or '(date not set)'} at {dep_time or '(time not set)'} UTC, "
                f"and will be on the ground after a maximum duration of {dur_desc}. "
                f"My aircraft category is {acft_cat}, and I am well aware of it. "
                f"My flight rules are {rules}. My aircraft is equipped for the following "
                f"approach types: {equip_desc}.\n\n"
                "You are an AI agent. Your task is to provide a very concise summary for "
                f"the airfield {icao}, to help me identify the key elements I need to take "
                "into account or that require specific attention for the flight and for "
                "decision making. The NOTAM list below is the final, current list — NEW and "
                "GONE NOTAMs have already been acknowledged and the list reflects the "
                "present situation; do not refer to NOTAMs as 'new' or 'removed', and do "
                "not mention NOTAMs that have been hidden or placed in never-shown "
                "sections.\n\n"
                "Approach equipment relevance: only mention information relevant to the "
                "approach types my aircraft is equipped for (listed above). Ignore "
                "procedure or minima changes for approach types I am not equipped for.\n\n"
                "Time factor: if a condition (e.g. runway closure, equipment unserviceable, "
                "restricted zone active) applies only for part of the flight window rather "
                "than the whole time I could be at this airfield, summarise the relevant "
                "time window concisely (e.g. 'RWY09 clsd til 14:00Z') rather than presenting "
                "it as a constant condition. NOTAMs marked [PARTIAL: ...] indicate this.\n\n"
                "PAPI unserviceable is significant and must always be mentioned if present, "
                "regardless of other priorities.\n\n"
                f"STRICT FORMAT: maximum {ai_lines} line(s), each line maximum {ai_chars} "
                "characters. This format must be respected very strictly. Use abbreviations "
                "aggressively, even when there is room to spare (RWY, THR, TWY, AD, ACFT, "
                "OPS, AUTH, PPR, AVBL, SKED, NGT, DAY, TIL, WEF, PERM, TEMPO, U/S, O/S, LDG, "
                "TKOF, VIS, RVR, IMC, VMC, IFR, VFR, ATC, TWR, APP, GND, FLT, ALT, HGT, AMSL, "
                "AGL, FL). Never cut a word or abbreviation mid-way.\n\n"
                "The NOTAMs below are already ordered from most important to least important. "
                "If everything cannot fit in the allowed format, drop the least important "
                "information first. If some relevant information could not fit within the "
                f"maximum allowed space minus 2 characters, place a \" *\" (space + asterisk) "
                "at the very end of the last line.\n\n"
                f"For navaid U/S NOTAMs: give type and identifier only — no frequency "
                f"(e.g. 'VOR BZH U/S' not 'VOR BZH 110.65MHz U/S'). For minima/procedure "
                f"NOTAMs: extract only Cat {acft_cat} values, drop other categories. If "
                "nothing significant applies, reply with exactly: No significant NOTAM\n\n"
                "Reply with ONLY the summary text — no preamble, no explanation, no quotes, "
                "no markdown formatting.\n\n"
                f"NOTAMs for {icao} (most important first):\n{notam_text}"
            )

            req_body = json.dumps({
                'model': 'claude-haiku-4-5-20251001',
                'max_tokens': max_tok,
                'messages': [{'role': 'user', 'content': prompt}]
            }).encode()
            req = urllib.request.Request(
                'https://api.anthropic.com/v1/messages',
                data=req_body, method='POST'
            )
            req.add_header('Content-Type', 'application/json')
            req.add_header('x-api-key', ANTHROPIC_API_KEY)
            req.add_header('anthropic-version', '2023-06-01')
            with urllib.request.urlopen(req, context=_ssl_ctx, timeout=15) as r:
                resp = json.loads(r.read())
            raw = resp.get('content', [{}])[0].get('text', '').strip()

            # Safety net: enforce line count and per-line char limit
            lines = raw.split('\n')
            safe_lines = []
            for line in lines[:ai_lines]:
                if len(line) > ai_chars:
                    cut = line[:ai_chars].rsplit(' ', 1)[0] or line[:ai_chars]
                    safe_lines.append(cut)
                else:
                    safe_lines.append(line)
            summary = '\n'.join(safe_lines) if safe_lines else 'No significant NOTAM'

            if truncated and summary != 'No significant NOTAM' and not summary.endswith('*'):
                # Ensure room for ' *' within the last line's limit
                last = safe_lines[-1] if safe_lines else ''
                if len(last) > ai_chars - 2:
                    last = last[:ai_chars - 2].rsplit(' ', 1)[0] or last[:ai_chars - 2]
                safe_lines[-1] = last + ' *'
                summary = '\n'.join(safe_lines)

            return {'status': 'ok', 'icao': icao, 'summary': summary, 'truncated': truncated}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _pull_prefs_body(self) -> dict:
        """Pull only _prefs.json for this user. Returns updated=True if content changed."""
        if not _github_enabled():
            return {'status': 'disabled'}
        try:
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            email = qs.get('email', [''])[0]
            if not email:
                return {'status': 'error', 'message': 'No email'}
            safe = ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in email)
            rel = f'snapshots/{safe}_prefs.json'
            remote_tree = _github_get_tree()
            remote_sha = remote_tree.get(rel)
            if not remote_sha:
                return {'status': 'ok', 'updated': False}
            local_path = os.path.join(ROOT, rel)
            # Fetch remote content
            blob = _github_api('GET', f'git/blobs/{remote_sha}')
            remote_content = base64.b64decode(blob['content']).decode('utf-8')
            # If remote content is empty/corrupt, treat as "no remote prefs" —
            # never overwrite local or trigger a reload from garbage data.
            try:
                remote_data = json.loads(remote_content)
            except json.JSONDecodeError:
                return {'status': 'ok', 'updated': False}
            # Compare parsed JSON — immune to formatting differences from save_prefs()
            try:
                local_raw  = open(local_path, encoding='utf-8').read()
                local_data = json.loads(local_raw)
                if local_data == remote_data:
                    # Rewrite local from GitHub bytes to fix SHA drift
                    with open(local_path, 'w', encoding='utf-8') as f:
                        f.write(remote_content)
                    return {'status': 'ok', 'updated': False}
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            # Content genuinely different — write and signal reload
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, 'w', encoding='utf-8') as f:
                f.write(remote_content)
            return {'status': 'ok', 'updated': True}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        for k, v in CORS.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _serve_map(self):
        """Serve a standalone fullscreen Leaflet map page."""
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>NOTAM Map</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
body{margin:0;font-family:sans-serif}
#obstmap{height:100vh;width:100vw}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:3px}
.dot.red{background:#D32F2F}.dot.orange{background:#EF6C00}
.dot-sq{display:inline-block;width:10px;height:10px;background:rgba(76,175,80,.4);border:1.5px solid #2E7D32;margin-right:3px}
.legend{position:fixed;bottom:10px;left:10px;background:rgba(255,255,255,.9);padding:6px 10px;border-radius:6px;font-size:11px;display:flex;gap:10px;flex-wrap:wrap;z-index:9999;box-shadow:0 1px 4px rgba(0,0,0,.2)}
.zone-alt-label,.leaflet-tooltip{font-size:14px !important;font-weight:600 !important}
.zone-alt-label{background:rgba(255,255,255,.75);border:none;box-shadow:none;padding:2px 5px;border-radius:3px}
.zone-alt-label::before{display:none}
.zone-alt-label span,.leaflet-tooltip span{font-size:14px !important;white-space:pre;text-align:center;display:block}
@media (max-width:768px){
  .zone-alt-label,.leaflet-tooltip{font-size:18px !important}
  .zone-alt-label span,.leaflet-tooltip span{font-size:18px !important}
  .legend{font-size:14px !important}
}
.style-switch{position:fixed;top:10px;right:10px;background:rgba(255,255,255,.9);padding:5px;border-radius:6px;display:flex;gap:4px;align-items:center;z-index:9999;box-shadow:0 1px 4px rgba(0,0,0,.2)}
.style-switch label{font-size:11px;color:#444;display:flex;align-items:center;gap:3px;white-space:nowrap;padding:0 4px}
.map-style-btn{font-size:11px;padding:3px 8px;border-radius:4px;border:1px solid #ccc;background:#fff;color:#333;cursor:pointer}
.map-style-btn.active{background:#1565C0;color:#fff;border-color:#1565C0}
</style></head><body>
<div id="obstmap"></div>
<div class="legend" id="legend"></div>
<div class="style-switch" id="map-style-switch-standalone">
  <label><input type="checkbox" id="airport-layer-cb-standalone">Airports</label>
</div>
<script>
// ── Map-page context flag and safe function stubs ────────────────────────────
// Variables declared with let/const in maprender.js are NOT pre-declared here
// (would cause redeclaration errors). Only functions are stubbed.
var _IS_MAP_PAGE = true;
// These are function declarations in maprender.js — safe to pre-assign as var
// so module-level calls at the bottom of maprender.js don't crash.
var getArEmail    = function() { return localStorage.getItem('notam_ar_email') || ''; };
var getArPassword = function() { return localStorage.getItem('notam_ar_pass')  || ''; };
var saveArCreds   = function() {};
var loadArCreds   = function() {};
var loadDefaults  = async function() {};
var saveEmail     = async function() {};
var loadEmail     = async function() {};
var updateBuf     = function() {};
var viz           = function() {};
var setStat       = function() {};
var initSync      = async function() {};
var initAI        = async function() {};
// ─────────────────────────────────────────────────────────────────────────────
MAPRENDER_PLACEHOLDER
</script>
<script>
var key = new URLSearchParams(window.location.search).get('key');
var raw = key ? sessionStorage.getItem(key) : null;
if (raw) sessionStorage.removeItem(key);
var data = raw ? JSON.parse(raw) : null;
if (!data) {
  document.getElementById('obstmap').innerHTML = '<p style="padding:20px;font-size:14px">No map data found. Please open from the briefing page.</p>';
} else {
  document.getElementById('legend').innerHTML = data.legend || '';
  // Restore the corridor ICAO list so the all-airports overlay's colour
  // logic (grey/dark-green/light-green) works correctly on this page too --
  // window._nearbyIcaos is a separate global from window._lastMapArgs and
  // wouldn't otherwise exist here.
  window._nearbyIcaos = data.nearbyIcaos || [];
  // Restore flight window so _azbaZoneIntersectsFlight works correctly
  // (determines which RTBA zones are shown as active/red on the map).
  if (typeof _briefStart !== 'undefined') {
    _briefStart = data.brief_start || 0;
    _briefEnd   = data.brief_end   || 0;
  }
  // Tell renderObstMap to skip its internal fitBounds — we set the view ourselves
  window._mapPageRequestedView = true;
  // Load rendering functions from parent page then call renderObstMap
  // Map rendering JS is inlined by server
  if (typeof renderObstMap === 'function') {
    // Panes are created inside renderObstMap via the main HTML logic
    renderObstMap(data.args.allApts, data.args.thresh, data.args.coordMap,
                  data.args.radiusNm, data.args.legPairs);
    // Populate the standalone style switcher (uses the same MAP_STYLES /
    // setMapStyle from maprender.js, so the choice stays in sync with the
    // main briefing page via localStorage).
    var swEl = document.getElementById('map-style-switch-standalone');
    if (swEl && typeof MAP_STYLES !== 'undefined') {
      var curStyle = getMapStyle();
      Object.keys(MAP_STYLES).forEach(function(k) {
        var btn = document.createElement('button');
        btn.textContent = MAP_STYLES[k].label;
        btn.title = MAP_STYLES[k].label + ' background';
        btn.className = 'map-style-btn' + (k === curStyle ? ' active' : '');
        btn.dataset.style = k;
        btn.onclick = function() { setMapStyle(k); };
        swEl.appendChild(btn);
      });
    }
    // Wire up the "Airports" checkbox -- reads/writes the same
    // notam_airport_layer localStorage key as the main briefing page, so
    // the on/off state is shared between both.
    var airportCb = document.getElementById('airport-layer-cb-standalone');
    if (airportCb && typeof getAirportLayerOn === 'function') {
      airportCb.checked = getAirportLayerOn();
      airportCb.onchange = function() { setAirportLayerOn(airportCb.checked); };
    }
    // This page never runs the main page's startup sequence (loadAirportDb
    // requires UI elements that don't exist here), so _airportDb would
    // otherwise stay empty forever. Load it from the IndexedDB cache the
    // main page maintains, then refresh the overlay if it's turned on.
    if (typeof loadAirportDbSilent === 'function') {
      loadAirportDbSilent().then(function() {
        if (typeof _refreshAirportLayer === 'function') _refreshAirportLayer();
      });
    }
    if (window.obstMap) {
    window.obstMap.setView([data.center.lat, data.center.lng], data.zoom, { animate: false });
    // On HiDPI screens (Android), request higher-zoom tiles for sharper text
    var dpr = window.devicePixelRatio || 1;
    if (dpr >= 1.5) {
      window.obstMap.eachLayer(function(l) {
        if (l._url && l._url.indexOf('/tile/') >= 0) {
          l.options.zoomOffset = 1;
          l.options.tileSize = 512;
          l.redraw();
        }
      });
    }
  }
  }
}
</script>
</body></html>""".encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()
        html = html.replace(b'MAPRENDER_PLACEHOLDER', MAPRENDER_JS.encode('utf-8'))
        # Inject openAIP API key for the tile overlay (same key as AZBA refresh).
        # Done here on bytes rather than inside the triple-quoted string above,
        # to avoid breaking out of the string with triple-quote sequences.
        key_js = f'window._openAipApiKey={json.dumps(OPENAIP_API_KEY)};'.encode('utf-8')
        html = html.replace(b'var _IS_MAP_PAGE = true;', b'var _IS_MAP_PAGE = true;\n' + key_js, 1)
        self.wfile.write(html)

    def _serve_help(self):
        try:
            readme = os.path.join(ROOT, 'README.md')
            if not os.path.exists(readme):
                self._error(404, 'README.md not found'); return
            with open(readme, 'r', encoding='utf-8') as f:
                md_text = f.read()
            body = _md_to_html(md_text).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except Exception as ex:
            import traceback; traceback.print_exc()
            self._error(500, f'Help render error: {ex}')

    def _proxy_airports(self):
        global _airports_csv_cache, _airports_csv_ts
        if not _airports_csv_cache or (time.time() - _airports_csv_ts) > AIRPORTS_TTL:
            try:
                req = urllib.request.Request(AIRPORTS_URL,
                                             headers={'User-Agent': 'notam-server/2.0'})
                with _urlopen(req, timeout=30) as r:
                    _airports_csv_cache = r.read()
                _airports_csv_ts = time.time()
            except Exception as e:
                self._error(502, str(e))
                return
        self._send_headers(200, 'text/csv; charset=utf-8')
        self.wfile.write(_airports_csv_cache)

    def _azba_zones(self):
        """Return cached AZBA/RTBA zone geometry (LF-prefixed + bare names,
        floor/ceiling, polygon coordinates). See src/azba.py for the cache
        lifecycle -- this never re-parses the CSV on every request, just
        reads whatever is currently in data/azba_zones_cache.json (auto-
        seeding it from data/zones_RTBA_openAIP.csv on first-ever request
        if the cache doesn't exist yet)."""
        try:
            zones = azba.load_zones()
            meta  = azba.cache_meta()
            self._json({'zones': zones, 'meta': meta})
        except FileNotFoundError:
            self._error(404, f'AZBA seed CSV not found at {azba.SEED_CSV} -- '
                              f'see README for where to place it.')
        except Exception as ex:
            import traceback; traceback.print_exc()
            self._error(500, f'AZBA zones error: {ex}')

    def _azba_diff(self):
        """Compare the CSV on disk (re-parsed fresh, never touching the
        cache) against the currently cached zone data. Read-only on both
        sides -- this never modifies the CSV or the cache, it only reports
        differences so the UI can warn the user the CSV may be stale
        relative to the (trusted, possibly openAIP-refreshed) cache."""
        try:
            self._json(azba.check_csv_discrepancy())
        except Exception as ex:
            import traceback; traceback.print_exc()
            self._error(500, f'AZBA diff error: {ex}')

    def _azba_schedule(self):
        """Fetch (or return the cached copy of) the SIA AZBA activation
        schedule. Returns {ok, available, entries, error, fetched_at,
        source_url, debug} -- 'available: False' means the page didn't
        contain usable schedule data (e.g. a service outage), which the
        client should treat the same as a hard failure: don't plot RTBA
        zones, show the manual-check link instead.

        Accepts ?force=1 to bypass the 15-minute cache and re-fetch
        immediately -- useful when debugging why the page isn't reachable,
        without waiting for the cache to expire."""
        from urllib.parse import parse_qs
        qs    = parse_qs(self.path.split('?', 1)[1] if '?' in self.path else '')
        force = qs.get('force', ['0'])[0] == '1'
        try:
            self._json(azba.fetch_schedule(force=force))
        except Exception as ex:
            import traceback; traceback.print_exc()
            self._error(500, f'AZBA schedule error: {ex}')

    def _raim_fetch(self):
        """POST /raim/fetch — trigger a RAIM outage check for a list of airports.
        Body: {airports: ["LFRB", ...], brief_start: <epoch>, brief_end: <epoch>}
        Returns the raim.fetch_raim() result dict immediately (blocking).
        The client should call this in a background task; results are cached for
        30 minutes so subsequent calls are instant.
        Only meaningful for IFR flights; VFR filtering is the caller's responsibility.
        """
        if not AUGUR_USERNAME or not AUGUR_PASSWORD:
            self._json({'ok': False, 'error': 'AUGUR credentials not configured in config.py',
                        'airports': {}, 'scenario_start': None, 'scenario_end': None,
                        'scenario_stale': False, 'fetched_at': 0})
            return
        try:
            body        = json.loads(self._read_body())
            airports    = [a.strip().upper() for a in body.get('airports', []) if a.strip()]
            brief_start = float(body.get('brief_start', 0) or 0)
            brief_end   = float(body.get('brief_end',   0) or 0)
            baro_aiding = bool(body.get('baro_aiding', False))
            print(f'[raim] fetch request: {airports} brief={brief_start:.0f}→{brief_end:.0f} baro={baro_aiding}', flush=True)
            if not airports:
                self._json({'ok': False, 'error': 'No airports provided',
                            'airports': {}, 'scenario_start': None, 'scenario_end': None,
                            'scenario_stale': False, 'fetched_at': 0})
                return
            result = raim.fetch_raim(airports, brief_start, brief_end, baro_aiding)
            self._json(result)
        except Exception as e:
            import traceback; traceback.print_exc()
            self._error(500, f'RAIM fetch error: {e}')

    def _raim_status(self):
        """GET /raim/status — returns AUGUR availability and config status."""
        self._json({
            'configured': bool(AUGUR_USERNAME and AUGUR_PASSWORD),
            'username':   AUGUR_USERNAME or None,
        })


        from urllib.parse import parse_qs
        qs      = parse_qs(self.path.split('?', 1)[1] if '?' in self.path else '')
        primary = qs.get('url', [''])[0]
        alt     = qs.get('alt', [''])[0]
        target  = _resolve_sup_url(primary, alt)
        self.send_response(302)
        self.send_header('Location', target)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def _proxy_ar_get(self):
        ar_path = self.path[3:]   # strip /ar, keep query string
        url     = 'https://api.autorouter.aero/v1.0' + ar_path
        auth    = self.headers.get('Authorization', '')
        headers = {'Authorization': auth,
                   'User-Agent':    'notam-server/2.0',
                   'Accept':        'application/json'}
        self._proxy_fetch('GET', url, headers=headers)

    def _proxy_tile(self, path: str):
        global _tile_idx
        parts = path[6:].replace('.png', '').split('/')
        # New format: /tile/{style}/{z}/{x}/{y}.png (4 segments).
        # Old format: /tile/{z}/{x}/{y}.png (3 segments) -- kept working for
        # any cached/older client requests, defaults to the standard style.
        if len(parts) == 4:
            style, z, x, y = parts
        elif len(parts) == 3:
            style, (z, x, y) = 'standard', parts
        else:
            self._error(400, 'Bad tile path')
            return

        if style == 'satellite':
            url = f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        elif style == 'relief':
            url = f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}'
        elif style == 'openaip-overlay':
            # openAIP aviation overlay -- API key kept server-side so it never
            # needs to be in client URLs. Tiles only exist at zoom 8-16.
            s = ['a', 'b', 'c'][_tile_idx % 3]
            _tile_idx += 1
            url = f'https://{s}.api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey={OPENAIP_API_KEY}'
        elif style == 'openaip':
            # Base OSM tiles for the openAIP map style
            s = TILE_SUBDOMAINS[_tile_idx % 4]
            _tile_idx += 1
            url = f'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
        else:
            s = TILE_SUBDOMAINS[_tile_idx % 4]
            _tile_idx += 1
            url = f'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png'

        headers = {'User-Agent': 'Mozilla/5.0 (compatible; notam-server/2.0)',
                   'Referer':    'https://www.openstreetmap.org/',
                   'Accept':     'image/png,image/*'}
        try:
            req = urllib.request.Request(url, headers=headers)
            with _urlopen(req, timeout=15) as r:
                data = r.read()
                ct   = r.headers.get('Content-Type', 'image/png')
            self.send_response(200)
            self.send_header('Content-Type',  ct)
            self.send_header('Cache-Control', 'public,max-age=86400')
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print(f'  [tile] ERROR {style}/{z}/{x}/{y}: {e}', flush=True)
            self._error(502, str(e))

    # ── POST handlers ─────────────────────────────────────────────────────────

    def _proxy_ar_post(self, path: str):
        ar_path = path[3:]
        url     = 'https://api.autorouter.aero/v1.0' + ar_path
        body    = self._read_body()
        ctype   = self.headers.get('Content-Type', 'application/x-www-form-urlencoded')
        # Clear the RAIM cache whenever a new NOTAM fetch is triggered, since
        # the airport list and flight window may have changed.
        if '/briefing/' in ar_path or '/notam' in ar_path.lower():
            raim.clear_cache()
        self._proxy_fetch('POST', url, body=body,
                          headers={'Content-Type': ctype,
                                   'User-Agent':   'notam-server/2.0',
                                   'Accept':       'application/json'})

    def _handle_never(self, path: str):
        try:
            body  = json.loads(self._read_body())
            email = body.get('email', '') or EMAIL
            print(f'  [never] {path} email={repr(email)}', flush=True)
            if path == '/never/load':
                self._json(load_never(email))
            elif path == '/never/add':
                add_never(email, body.get('notam_id', ''), body.get('notam_data', {}))
                self._json({'ok': True})
            elif path == '/never/remove':
                remove_never(email, body.get('notam_id', ''))
                self._json({'ok': True})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._error(500, str(e))

    def _handle_snapshot(self, path: str):
        try:
            body  = json.loads(self._read_body())
            key   = snapshot_key(body.get('email',   ''),
                                 body.get('dep',     '').upper().strip(),
                                 body.get('dest',    '').upper().strip(),
                                 body.get('variant', 'Straight').strip())
            if   path == '/snapshot/load':  self._json(load_snapshot(key) or {})
            elif path == '/sync/push':   self._json(github_push([os.path.join(ROOT, p) for p in body.get('paths', []) if not os.path.isabs(p)]))
            elif path == '/snapshot/save':  save_snapshot(key, body); self._json({'ok': True})
            elif path == '/snapshot/ack':   self._json(ack_snapshot(key, body))
            elif path == '/snapshot/flush': flush_snapshot(key);                        self._json({'ok': True})

        except Exception as e:
            import traceback; traceback.print_exc()
            self._error(500, str(e))

    def _handle_prefs(self, path: str):
        try:
            body  = json.loads(self._read_body())
            email = body.get('email', '') or EMAIL
            if path == '/prefs/load':
                self._json(load_prefs(email))
            elif path == '/prefs/save':
                save_prefs(email, body.get('prefs', {}))
                self._json({'ok': True})
        except Exception as e:
            import traceback; traceback.print_exc()
            self._error(500, str(e))

    # ── Shared proxy helper ───────────────────────────────────────────────────

    def _proxy_fetch(self, method: str, url: str, body: bytes = None, headers: dict = None):
        try:
            req = urllib.request.Request(url, data=body, method=method,
                                         headers=headers or {})
            with _urlopen(req, timeout=20) as r:
                data = r.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            for k, v in CORS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self._send_headers(e.code, 'application/json')
            self.wfile.write(e.read())
        except Exception as e:
            self._error(502, str(e))

# ── HTML injection (pre-fill credentials in the browser form) ─────────────────

def _read_html_injected() -> bytes:
    """Read HTML and inject credentials so the login fields are pre-filled."""
    with open(HTML_FILE, 'rb') as f:
        data = f.read()
    # Inject a small script that pre-fills credentials after the page loads.
    # This approach is immune to changes in loadArCreds() structure.
    inject_script = (
        f"<script>"
        f"window._openAipApiKey={json.dumps(OPENAIP_API_KEY)};"  # expose for tile layer
        f"(function(){{"
        f"var _inj=function(){{"
        f"var ef=document.getElementById('ar-email');"
        f"var pf=document.getElementById('ar-pass');"
        f"var hadEmail=!!(ef&&ef.value);"
        f"if(ef&&!ef.value)ef.value={json.dumps(EMAIL)};"
        f"if(pf&&!pf.value)pf.value={json.dumps(PASSWORD)};"
        # If the email field was empty when the main script ran (loadDefaults/
        # loadEmail/initSync skipped their _prefs.json fetch because
        # getArEmail() returned ''), re-run them now that the email is set.
        f"if(!hadEmail&&ef&&ef.value&&typeof loadDefaults==='function'){{"
        f"loadDefaults();"
        f"if(typeof initSync==='function')initSync();"
        f"if(typeof loadEmail==='function')loadEmail().then(function(){{"
        f"if(typeof loadSnapshot==='function')loadSnapshot(true);"
        f"}});"
        f"}}"
        f"}};"
        f"if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_inj);"
        f"else _inj();"
        f"}})();"
        f"</script>"
    ).encode()
    # Insert just before </body>
    return data.replace(b'</body>', inject_script + b'</body>', 1)

# ── AIP SUP URL resolver ──────────────────────────────────────────────────────

def _resolve_sup_url(primary: str, alt: str) -> str:
    """Return primary URL if reachable, else alt."""
    if not (primary and alt):
        return primary or alt
    try:
        req = urllib.request.Request(primary, method='HEAD')
        urllib.request.urlopen(req, timeout=5)
        return primary
    except Exception:
        return alt

# ── Snapshot helpers ──────────────────────────────────────────────────────────

def snapshot_key(email: str, dep: str, dest: str, variant: str = 'Straight') -> str:
    safe = lambda s: ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in s)
    suffix = f'_{variant}' if variant and variant != 'Straight' else ''
    return f'{safe(email)}_{safe(dep)}_{safe(dest)}{suffix}'

def _snapshot_path(key: str) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    return os.path.join(SNAPSHOT_DIR, key + '.json')

def load_snapshot(key: str) -> dict | None:
    path = _snapshot_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def save_snapshot(key: str, body: dict):
    path   = _snapshot_path(key)
    notams = body.get('notams', {})
    data   = {'notams': notams}
    for meta in ('_waypoints', '_tkof', '_alt1', '_alt2', '_extras'):
        if meta in body:
            data[meta] = body[meta]
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f'  [snapshot] saved {len(notams)} NOTAMs → {path}', flush=True)

def flush_all_snapshots(email: str) -> dict:
    """Delete all snapshot files for a given email, locally and on GitHub."""
    if not email:
        return {'status': 'error', 'message': 'No email provided'}
    safe_email = ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in email)
    snap_dir = os.path.join(ROOT, 'snapshots')
    deleted_local = 0
    deleted_remote = 0
    errors = []
    if os.path.isdir(snap_dir):
        for fname in os.listdir(snap_dir):
            # Never delete _never.json (manual exclusions) or _prefs.json (personal preferences)
            if fname.endswith('_never.json') or fname.endswith('_prefs.json'):
                continue
            if fname.endswith('.json') and fname.startswith(safe_email + '_'):
                try:
                    os.remove(os.path.join(snap_dir, fname))
                    deleted_local += 1
                    rel = 'snapshots/' + fname
                    result = github_delete(rel)
                    if result.get('deleted'):
                        deleted_remote += 1
                except Exception as e:
                    errors.append(str(e))
    return {'status': 'ok', 'deleted_local': deleted_local,
            'deleted_remote': deleted_remote, 'errors': errors}

def flush_snapshot(key: str):
    path = _snapshot_path(key)
    if os.path.exists(path):
        os.remove(path)
    # Also delete from GitHub so it doesn't come back on next pull
    rel = os.path.relpath(path, ROOT).replace(os.sep, '/')
    github_delete(rel)

def ack_snapshot(key: str, body: dict) -> dict:
    stored = load_snapshot(key) or {}
    snap   = stored.get('notams', stored)   # handle both old and new format
    nid    = body.get('notam_id')
    if nid:
        if body.get('action') == 'add':
            snap[nid] = body.get('notam_data', {})
        else:
            snap.pop(nid, None)
        save_snapshot(key, {**stored, 'notams': snap})
    return {'ok': True, 'count': len(snap)}

# ── Never-show helpers ────────────────────────────────────────────────────────

def _never_path(email: str) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    safe = ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in email)
    return os.path.join(SNAPSHOT_DIR, safe + '_never.json')

def load_never(email: str) -> dict:
    path = _never_path(email)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return _purge_expired_never(data, path)
    except Exception:
        return {}

def _purge_expired_never(data: dict, path: str) -> dict:
    """Drop entries whose validity ended more than 60 days ago."""
    import datetime
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=60)
    clean  = {}
    purged = []
    for nid, entry in data.items():
        if _never_entry_expired(entry, cutoff):
            purged.append(nid)
        else:
            clean[nid] = entry
    if purged:
        print(f'  [never] purged {len(purged)} expired: {purged[:3]}', flush=True)
        with open(path, 'w') as f:
            json.dump(clean, f, indent=2)
    return clean

def _never_entry_expired(entry: dict, cutoff) -> bool:
    """Return True if the entry's endvalidity is before cutoff."""
    import datetime
    end = entry.get('endvalidity')
    if not end or str(end).strip().upper() in ('', 'PERM', 'PERMANENT', 'NONE', 'NULL'):
        return False
    try:
        if isinstance(end, (int, float)):
            end_dt = datetime.datetime.fromtimestamp(end, tz=datetime.timezone.utc)
        else:
            try:
                end_dt = datetime.datetime.fromtimestamp(float(end), tz=datetime.timezone.utc)
            except (ValueError, TypeError):
                end_dt = datetime.datetime.fromisoformat(str(end).replace('Z', '+00:00'))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
        return end_dt < cutoff
    except Exception as ex:
        print(f'  [never] parse error: {ex}', flush=True)
        return False

def add_never(email: str, notam_id: str, data: dict):
    never = load_never(email)
    never[notam_id] = data
    with open(_never_path(email), 'w') as f:
        json.dump(never, f, indent=2)
    print(f'  [never] added {notam_id} ({len(never)} total)', flush=True)

def remove_never(email: str, notam_id: str):
    never = load_never(email)
    if notam_id in never:
        del never[notam_id]
        with open(_never_path(email), 'w') as f:
            json.dump(never, f, indent=2)

# ── Prefs helpers ────────────────────────────────────────────────────────────

def _prefs_path(email: str) -> str:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    safe = ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in email)
    return os.path.join(SNAPSHOT_DIR, safe + '_prefs.json')

def load_prefs(email: str) -> dict:
    path = _prefs_path(email)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def save_prefs(email: str, prefs: dict):
    path = _prefs_path(email)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(prefs, f, indent=2)
    print(f'  [prefs] saved for {email}', flush=True)
    if _github_enabled():
        try:
            github_push([path])
            print(f'  [prefs] pushed to GitHub', flush=True)
        except Exception as e:
            print(f'  [prefs] push failed: {e}', flush=True)

# ── Network helpers ─────────────────────────────────────────────────────────────

def get_lan_ips() -> list[str]:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return [ip]
    except Exception:
        return []

# ── Reusable HTTP server ──────────────────────────────────────────────────────

class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, 'SO_REUSEPORT'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception as ex:
            print(f'  [server] Warning: could not set socket options: {ex}', flush=True)
        super().server_bind()

    def handle_error(self, request, client_address):
        """Override the default (which prints a full traceback to stderr).

        A client disconnecting mid-response -- e.g. Safari getting
        suspended by iOS while the app is backgrounded, a tab being
        closed, or a mobile network hiccup -- causes a BrokenPipeError
        or ConnectionResetError when we try to write the response. This
        is routine on mobile/unreliable connections, not a real server
        error, so we log a single quiet line instead of a noisy traceback.
        """
        exc_type = _sys.exc_info()[0]
        if exc_type in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            print(f'  [server] client disconnected mid-response ({client_address[0]}) -- ignored', flush=True)
        else:
            super().handle_error(request, client_address)

def _start_server() -> ReusableHTTPServer:
    """Attempt to bind the server, retrying up to 5 times."""
    for attempt in range(5):
        try:
            return ReusableHTTPServer(('0.0.0.0', PORT), Handler)
        except OSError:
            if attempt < 4:
                print(f'  Port {PORT} busy, retrying in 2s… ({attempt+1}/5)', flush=True)
                time.sleep(2)
    print(f'\n  ERROR: Port {PORT} still in use after 5 attempts.')
    print(f'  Run:  fuser -k {PORT}/tcp   (or restart the device).')
    raise SystemExit(1)

# ── Startup checks ────────────────────────────────────────────────────────────

def _verify_html():
    """Abort early with a clear message if the HTML file is missing or unreadable."""
    try:
        with open(HTML_FILE, 'rb') as f:
            f.read(4)
    except PermissionError:
        print(f'  ERROR: Cannot read {HTML_FILE}', flush=True)
        print('  On Android, move all files to ~/downloads/ and run from there.', flush=True)
        raise SystemExit(1)
    except FileNotFoundError:
        print(f'  ERROR: File not found: {HTML_FILE}', flush=True)
        print('  Make sure notam_briefing_v1.html is next to notam_server.py', flush=True)
        raise SystemExit(1)

def _ensure_logs_dir():
    try:
        os.makedirs(os.path.join(ROOT, 'logs'), exist_ok=True)
    except Exception:
        pass

def _write_pid():
    try:
        with open(os.path.join(ROOT, 'notam_server.pid'), 'w') as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

def _write_info(url: str, lan: list):
    info_path = os.path.join(ROOT, 'notam_server_url.txt')
    with open(info_path, 'w') as f:
        f.write(f'NOTAM Briefing running at:\n  {url}\n')
        for ip in lan:
            f.write(f'  http://{ip}:{PORT}  (LAN / mobile)\n')
        f.write('\nStop the server via stop_notam_linux.sh\n')
    return info_path

# ── Entry point ───────────────────────────────────────────────────────────────

# ── Markdown to HTML (lightweight renderer for README) ────────────────────────

def _md_to_html(md: str) -> str:
    import html as _html
    import re

    lines    = md.split('\n')
    out      = []
    in_code  = False
    in_ul    = False
    in_ol    = False
    in_table = False
    _slug_seen = {}

    def close_lists():
        nonlocal in_ul, in_ol, in_table
        if in_ul:    out.append('</ul>');               in_ul    = False
        if in_ol:    out.append('</ol>');               in_ol    = False
        if in_table: out.append('</tbody></table>');    in_table = False

    def slugify(text):
        # Mirrors GitHub's heading-anchor algorithm closely enough for our TOC:
        # strip markdown formatting/links, lowercase, spaces -> hyphens,
        # drop characters that aren't letters/digits/hyphen/underscore/space.
        plain = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        plain = re.sub(r'`(.+?)`', r'\1', plain)
        plain = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', plain)
        slug = plain.strip().lower()
        slug = re.sub(r'[^\w\- ]', '', slug)  # drop punctuation (emoji, /, (), etc.)
        slug = re.sub(r'\s+', '-', slug)
        slug = slug.strip('-')
        if slug in _slug_seen:
            _slug_seen[slug] += 1
            slug = f'{slug}-{_slug_seen[slug]}'
        else:
            _slug_seen[slug] = 0
        return slug

    def inline(s):
        s = _html.escape(s)
        s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
        s = re.sub(r'`(.+?)`',        r'<code>\1</code>',    s)
        def _link(m):
            href, label = m.group(2), m.group(1)
            target = '' if href.startswith('#') else ' target="_blank"'
            return f'<a href="{href}"{target}>{label}</a>'
        s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _link, s)
        return s

    for line in lines:
        if line.startswith('```'):
            if not in_code:
                close_lists(); out.append('<pre><code>'); in_code = True
            else:
                out.append('</code></pre>');              in_code = False
            continue
        if in_code:
            out.append(_html.escape(line)); continue

        if   line.startswith('### '): close_lists(); _t = line[4:]; out.append(f'<h3 id="{slugify(_t)}">{inline(_t)}</h3>'); continue
        elif line.startswith('## '):  close_lists(); _t = line[3:]; out.append(f'<h2 id="{slugify(_t)}">{inline(_t)}</h2>'); continue
        elif line.startswith('# '):   close_lists(); _t = line[2:]; out.append(f'<h1 id="{slugify(_t)}">{inline(_t)}</h1>'); continue
        elif line.startswith('> '):   close_lists(); out.append(f'<blockquote>{inline(line[2:])}</blockquote>'); continue
        elif line.startswith('---'):  close_lists(); out.append('<hr>'); continue

        if '|' in line and line.strip().startswith('|'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if re.match(r'^[\s\-|:]+$', line):
                continue
            if not in_table:
                close_lists()
                out.append('<table><thead><tr>')
                out.append(''.join(f'<th>{inline(c)}</th>' for c in cells))
                out.append('</tr></thead><tbody>')
                in_table = True
            else:
                out.append('<tr>' + ''.join(f'<td>{inline(c)}</td>' for c in cells) + '</tr>')
            continue

        m = re.match(r'^[-*] (.+)', line)
        if m:
            if in_ol: out.append('</ol>'); in_ol = False
            if not in_ul: out.append('<ul>'); in_ul = True
            out.append(f'<li>{inline(m.group(1))}</li>'); continue

        m = re.match(r'^\d+\. (.+)', line)
        if m:
            if in_ul: out.append('</ul>'); in_ul = False
            if not in_ol: out.append('<ol>'); in_ol = True
            out.append(f'<li>{inline(m.group(1))}</li>'); continue

        close_lists()
        out.append('<p></p>' if not line.strip() else f'<p>{inline(line)}</p>')

    close_lists()
    body = '\n'.join(out)
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>NOTAMv1 Help</title>\n'
        '<style>\n'
        'body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1.5rem;color:#111;line-height:1.6}\n'
        'h1{border-bottom:2px solid #1565C0;padding-bottom:.4rem;color:#1565C0}\n'
        'h2{border-bottom:1px solid #ddd;padding-bottom:.2rem;margin-top:2rem}\n'
        'h3{margin-top:1.5rem}\n'
        'code{background:#f0f2f5;padding:2px 5px;border-radius:3px;font-size:.9em}\n'
        'pre{background:#f0f2f5;padding:1rem;border-radius:6px;overflow-x:auto}\n'
        'pre code{background:none;padding:0}\n'
        'table{border-collapse:collapse;width:100%;margin:1rem 0}\n'
        'th,td{border:1px solid #ddd;padding:6px 10px;text-align:left}\n'
        'th{background:#f0f2f5}\n'
        'blockquote{border-left:4px solid #1565C0;margin:1rem 0;padding:.5rem 1rem;background:#f7f9ff;border-radius:0 6px 6px 0}\n'
        'a{color:#1565C0}\n'
        'hr{border:none;border-top:1px solid #ddd;margin:1.5rem 0}\n'
        '</style></head>\n'
        f'<body>{body}</body></html>'
    )
if __name__ == '__main__':
    _ensure_logs_dir()
    _verify_html()
    server   = _start_server()
    url      = f'http://localhost:{PORT}'
    lan      = get_lan_ips()
    info_path = _write_info(url, lan)
    _write_pid()

    print(f'  HTML:  {HTML_FILE}',   flush=True)
    print(f'  Local: {url}',         flush=True)
    for ip in lan:
        print(f'  LAN:   http://{ip}:{PORT}', flush=True)
    print('READY', flush=True)

    # AZBA/RTBA zone data: ensure the cache exists, and kick off an openAIP
    # refresh in the background if one is due and a key is configured. This
    # runs in a thread so a slow/unreachable openAIP API never delays
    # startup or blocks READY from being printed -- worst case, the cache
    # just keeps using whatever it already had (CSV-seeded or a previous
    # successful refresh).
    try:
        azba.load_zones()  # ensure cache exists (auto-seeds from CSV if missing)
        if OPENAIP_API_KEY and azba.needs_refresh():
            def _bg_azba_refresh():
                try:
                    result = azba.refresh_from_openaip(OPENAIP_API_KEY)
                    if result.get('ok'):
                        print(f"  [azba] openAIP refresh: matched {result['matched']}, "
                              f"updated {result['updated']} zones", flush=True)
                    else:
                        print(f"  [azba] openAIP refresh failed: {result.get('error')}", flush=True)
                except Exception as ex:
                    print(f"  [azba] openAIP refresh error: {ex}", flush=True)
            import threading as _threading
            _threading.Thread(target=_bg_azba_refresh, daemon=True).start()
    except Exception as ex:
        print(f'  [azba] startup check failed (non-fatal): {ex}', flush=True)

    pid_path = os.path.join(ROOT, 'notam_server.pid')

    def _cleanup(*_args):
        try:
            os.remove(info_path)
        except Exception:
            pass
        try:
            os.remove(pid_path)
        except Exception:
            pass
        raise SystemExit(0)

    import signal as _signal
    try:
        _signal.signal(_signal.SIGTERM, _cleanup)
    except (ValueError, AttributeError):
        pass  # SIGTERM not available on this platform/thread

    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            os.remove(info_path)
        except Exception:
            pass
        try:
            os.remove(pid_path)
        except Exception:
            pass
