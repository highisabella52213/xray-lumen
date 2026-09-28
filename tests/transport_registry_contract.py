#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transports import TRANSPORTS,TransportValidationError,TransportUnavailableError
available={'vless-ws','vless-xhttp','vless-httpupgrade','vmess-ws','trojan-ws'}
cap={x['id']:x for x in TRANSPORTS.capabilities()}
assert {k for k,v in cap.items() if v['available']}==available
for protocol in available:
    ident,settings=TRANSPORTS.validate(protocol,{})
    assert ident==protocol and settings=={}
    p=TRANSPORTS.vless_parameters(protocol,uuid='11111111-2222-3333-4444-555555555555',transport_host='relay.example',location_id='loc-a')
    assert p['path'].endswith('/loc-a') and p['host']=='relay.example'
for protocol in ('vless-tcp','vless-grpc','vless-kcp'):
    try: TRANSPORTS.validate(protocol,{})
    except TransportUnavailableError: pass
    else: raise AssertionError(protocol+' unexpectedly available')
try: TRANSPORTS.validate('unknown',{})
except TransportValidationError: pass
else: raise AssertionError('unknown transport accepted')
print('transport registry V32: VLESS/VMess/Trojan + WS/XHTTP/HTTPUpgrade=OK Railway-incompatible transports=blocked')
