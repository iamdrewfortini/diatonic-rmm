# Diatonic RMM

Version 0.1.0 · Part of the Diatonic OS 10-day Meta Hackathon build exploration.

Diatonic OS is an original IT operations platform by Chromatix Studios. Founder attribution: Drew Fortini (@iamdrewfortini).

Source mirrors: [Drew Fortini’s GitHub repository](https://github.com/iamdrewfortini/diatonic-rmm) · [Chromatix Studios GitLab namespace](https://git.chromatixstudios.com/chromatix-studios/diatonic-rmm).

This public exploration demonstrates an authenticated observer API, bounded read only wall projections, and an edge watcher that keeps a last known good snapshot. It is an early prototype, not a complete RMM product or production deployment.

The prototype displays synthetic topology and accepts bounded observations. It does not enroll a fleet, run remote commands, patch endpoints, control guests, or execute remediation. Technician and approver routes manage prototype records and proposed actions only; there is no execution endpoint.

## Local synthetic demo

Requires Python 3.11 or newer. Runtime and tests use only the Python standard library. Run python3 ops/demo.py. The demo binds only to 127.0.0.1:8765, generates its bearer token at startup, creates a synthetic in memory snapshot, and makes no production connections. Keep it running and use the printed curl command. Stop it with Ctrl+C.

The local demo intentionally uses plain HTTP only on loopback. The standalone service configuration requires TLS 1.2 or newer and reads its identity registry, database, Valkey password, certificate, key, bind address and port from RMM_CONFIG. Never reuse demo credentials.

## API sketch

- GET /api/wall/snapshot — fresh observer projection with an ETag for conditional reads.
- POST /api/wall/projection — collector role only; bounded, allowlisted topology.
- POST /api/observations — observer role only; up to 16 named status checks.
- Prototype record routes are role checked, tenant isolated, and audit recorded. Proposed actions require a different approver identity. No route executes an action.

The edge watcher requires HTTPS, checks configured TCP services, polls the observer snapshot, and stores a bounded last known good cache. Configure hosts, ports, certificate trust and paths for your environment before running it. No targets are hardcoded in this export.

## Tests and CI

Run python3 -m unittest discover -s tests -v. The GitHub Actions and GitLab CI definitions target Python 3.11, install no project dependencies, and require no private registry. Runner provisioning is separate from these repository definitions. Both pipelines package the approved tracked source as diatonic-rmm-0.1.0.tar.gz.

## Attribution and license

Copyright © 2026 Chromatix Studios. Diatonic OS is a Chromatix Studios product. Founder attribution: Drew Fortini, @iamdrewfortini.

No open source license is granted by this repository. It contains no third party assets. Product descriptions and demo fixture data are original showcase material.
