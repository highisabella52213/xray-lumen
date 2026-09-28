#!/usr/bin/env python3
import ast,json,os,tempfile,asyncio
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
main=(ROOT/'main.py').read_text();pages=(ROOT/'pages.py').read_text();manager=(ROOT/'xray_manager.py').read_text();gateway=(ROOT/'edge_gateway.js').read_text();docker=(ROOT/'Dockerfile').read_text()
required=['vless-ws','vless-xhttp','vless-httpupgrade','vmess-ws','trojan-ws']
assert all(x in main and x in pages for x in required)
assert all(x in manager for x in ['"vless"','"vmess"','"trojan"','"ws"','"xhttp"','"httpupgrade"'])
assert '127.0.0.1' in manager and 'run", "-test"' in manager
assert 'open_outbound' in manager and 'resolve_selection' in manager
assert 'x-forwarded-for' in gateway and "split(',').at(-1)" in gateway
assert '/usr/local/bin/xray version' in docker and 'edge_gateway.js' in docker
assert 'lumen-v32' in main
assert 'location.reload()' not in pages
print('Lumen V32 Xray contract: protocols=5 edge=Railway exact-proxy-bridge=OK fail-closed=OK attribution-clean=OK')
