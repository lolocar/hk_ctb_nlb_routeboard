# Route Board — CTB & NLB Bus Arrival Times

A bus-arrival web app covering two Hong Kong operators: **Citybus (CTB)** and **New World First Bus (NLB)**. It shows expected arrival times (ETAs) for the routes serving a bus stop, finds stops near you via GPS, and lets you subscribe to the stops you care about.

## Features

- **Nearby stops** — uses your GPS location to list stops within 500 m (sorted by distance)
- **Subscriptions** — star any stop (list view or detail view); subscribed stops are pinned to the top and persisted in a browser cookie
- **Stop search** — fuzzy match on Traditional/Simplified Chinese names, English names, or stop ID
- **Live ETAs** — the stop detail page shows the next arrival per route, auto-refreshing every 30 s
- **i18n** — Traditional Chinese and English UI (your choice is remembered)
- **Automatic data refresh** — the server checks for new routes daily (05:00 HKT) and updates the stop data; the running page picks up changes within 10 minutes

## Data Sources

All data comes from the HK Government real-time transport data portal (`rt.data.gov.hk`):

| Data | Endpoint | Notes |
|------|----------|-------|
| CTB route list | `v1/transport/citybus-nwfb/route/ctb` | each record = one direction; no separate route ID |
| NLB route list | `v2/transport/nlb/route.php?action=list` | has numeric `routeId`; same route number can have several IDs (outbound/return/variants) |
| CTB stop sequence | `v1/transport/citybus-nwfb/route-stop/ctb/{route}/{inbound\|outbound}` | per route × direction, stop IDs only |
| CTB stop detail | `v1/transport/citybus-nwfb/stop/{stop_id}` | name + coordinates; **no batch endpoint**, one request per stop |
| NLB stop list | `v2/transport/nlb/stop.php?action=list&routeId={routeId}` | coordinates included inline |
| Live ETAs | `v1/transport/batch/stop-eta/{ctb\|nlb}/{stop_id}?lang={zh-hant\|zh-hans\|en}` | fetched directly by the browser, never stored |

ETAs are real-time and are **not** cached in the database — the frontend calls the API directly (the API is HTTPS with `Access-Control-Allow-Origin: *`).

## Project Layout

```
routeboard.db           # SQLite database
db.py                   # schema + connection + upsert helpers (route / stop / route_stop)
import_routes.py        # CLI: import route lists        (python import_routes.py [ctb|nlb])
import_stops.py         # CLI: import stops + stop order (python import_stops.py [ctb|nlb])
export_stops.py         # CLI: export stops.json + stops_meta.json for the web app
maintenance.py          # detect new routes → import their stops → re-export (CLI: python maintenance.py [--check-only])
importers/
  ctb.py                # Citybus route list fetch/parse/import
  nlb.py                # New World First Bus route list fetch/parse/import
  ctb_stops.py          # Citybus stops: two-phase import (sequences, then per-stop details)
  nlb_stops.py          # New World First Bus stops: one-phase import
webapp/
  index.html            # single-file web app (HTML + CSS + JS, zero dependencies)
  stops.json            # exported stop data (generated, do not edit)
  stops_meta.json       # lightweight metadata (count + updated), polled by the frontend
  serve_https.py        # HTTPS server (self-signed cert) + daily maintenance thread
  server.crt / server.key
```

### Database model (SQLite)

- **route** — unified route record: `operator`, `source_route_id`, `route_no`, `direction`, trilingual origin/destination, `overnight`, `special`. Unique on `(operator, source_route_id, direction)`.
- **stop** — physical stop shared across routes: trilingual name, optional road segment (NLB only), coordinates. Unique on `(operator, source_stop_id)`.
- **route_stop** — route × direction × stop association with travel order `seq`.

Import strategy is full-replace per operator (delete then insert). Stop import depends on the route table, so run `import_routes.py` first.

## Setup

Requires Python 3.10+ (standard library only, no third-party packages).

```bash
# 1. Import route lists
python3 import_routes.py          # both operators (or: ctb / nlb)

# 2. Import stops + stop order
#    CTB takes several minutes: ~406 routes × 2 directions + one request per unique stop
python3 import_stops.py

# 3. Export stop data for the web app
python3 export_stops.py
```

## Running the Web App

```bash
cd webapp
python3 serve_https.py            # https://127.0.0.1:9443
python3 serve_https.py --check-now   # also run a new-route check at startup
python3 serve_https.py 8443 0.0.0.0  # custom port / bind address
```

Open `https://127.0.0.1:9443` in a browser. The self-signed certificate will show a warning — click **Advanced → Proceed**.

The HTTPS server also runs a background maintenance thread:

- every day at **05:00 HKT** it fetches both route lists, detects routes missing from the database, imports their stops (only stop details not already in the DB are requested), and re-exports `stops.json` / `stops_meta.json`
- a running page polls `stops_meta.json` every 10 minutes and refreshes its stop list when the data changed

If you only need plain HTTP (e.g. local dev), any static file server works:

```bash
cd webapp && python3 -m http.server 8090   # http://127.0.0.1:8090
```

### Regenerating the self-signed certificate

```bash
cd webapp
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout server.key -out server.crt \
  -days 365 \
  -subj "/CN=routeboard.local/O=RouteBoard" \
  -addext "subjectAltName=DNS:localhost,DNS:routeboard.local,IP:127.0.0.1"
```

For public HTTPS, deploy `webapp/` to any static host (Vercel, Netlify, GitHub Pages — free automatic HTTPS) or put Caddy/Nginx in front with a Let's Encrypt certificate.

## Deployment Notes

- The web app is a single static file plus `stops.json` — no build step, no backend required for serving pages or ETAs
- Subscriptions and language preference live in browser cookies; there is no account system
- Without location permission, the nearby section is hidden and search still works
- Stops of discontinued routes are kept (the physical stop still exists); the detail page only shows routes that the live ETA API reports
- Single-direction routes legitimately have stops in only one direction; a few special routes have no stop data at all — both are expected

## Reference

Project notes and API findings (field definitions, quirks such as CTB's `lat`/`long` field names and NLB's per-direction `routeId`s) are documented in [NOTES.md](NOTES.md).
