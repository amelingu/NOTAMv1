#!/usr/bin/env python3
"""Regenerate src/maprender.js from notam_briefing_v1.html.
Called automatically at server startup — no manual intervention needed."""
import os, sys

ROOT    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML    = os.path.join(ROOT, 'notam_briefing_v1.html')
OUT     = os.path.join(ROOT, 'src', 'maprender.js')
START   = '<!--MAPSCRIPT_START-->'
END     = '<!--MAPSCRIPT_END-->'

with open(HTML, 'r', encoding='utf-8') as f:
    html = f.read()

s = html.find(START)
e = html.find(END)
if s == -1 or e == -1:
    print('[maprender] ERROR: markers not found in HTML', flush=True)
    sys.exit(1)

content = html[s + len(START):e].strip('\n')
with open(OUT, 'w', encoding='utf-8') as f:
    f.write(content)

print(f'  [maprender] updated ({content.count(chr(10))+1} lines)', flush=True)
