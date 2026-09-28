# Lumen V32 — Xray Railway Data Plane

Lumen V32 preserves the existing Lumen dashboard and management APIs while replacing the public connection path with a pinned Xray-core runtime behind a small Railway edge gateway.

## Supported combinations

- VLESS / WebSocket
- VLESS / XHTTP
- VLESS / HTTPUpgrade
- VMess / WebSocket
- Trojan / WebSocket

Raw TCP, gRPC pass-through and UDP/KCP are intentionally unavailable on the Railway HTTP edge.

## Traffic path

`Railway TLS → edge_gateway.js → exact loopback Xray inbound → per-route SOCKS bridge → existing Lumen exact outbound resolver`

Each saved configuration owns an exact unguessable path and one loopback inbound. Xray is never exposed directly. The bridge retains direct/custom/managed exact-proxy behavior, Multi-Location selection and fail-closed routing. Quota, speed limit and IP-limit checks remain enforced in the data path.

## Runtime safety

- Xray is pinned and installed during Docker build, not downloaded during startup.
- Generated config is validated with `xray run -test` before activation.
- Config and route-map files are replaced atomically.
- All Xray inbounds and route bridges bind only to `127.0.0.1`.
- `/health` includes safe Xray readiness/revision information.
- The proxy repository accepts credentials only from Railway environment variables; no private credential is embedded.

## Required Railway variables

Normal deployment needs no protocol-specific variable. Keep a persistent volume at `/data`.
For the optional private proxy repository set: `LUMEN_S3_ENDPOINT`, `LUMEN_S3_REGION`, `LUMEN_S3_BUCKET`, `LUMEN_S3_OBJECT_KEY`, `LUMEN_S3_ACCESS_KEY_ID`, and `LUMEN_S3_SECRET_ACCESS_KEY`.
