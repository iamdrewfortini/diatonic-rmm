# Diatonic RMM 0.1.0

**Chromatix Studios · Diatonic OS · 10-day Meta Hackathon exploration**

This first public source package presents a focused observer slice of the Diatonic OS IT operations platform.

- Authenticated API with expiring bearer identities, tenant scoping, and role checks.
- Bounded, allowlisted wall projections with freshness checks and ETag-based reads.
- Edge watcher with timed TCP checks and a bounded last known good snapshot cache.
- Local synthetic demo and security-focused standard-library test suite.

The scope is intentionally narrow. This release does not enroll or manage a fleet, execute endpoint actions, run remote commands, or provide patch management. The demo uses synthetic data and binds to loopback.

Copyright © 2026 Chromatix Studios. Founder attribution: Drew Fortini, @iamdrewfortini. See LICENSE.md and SECURITY.md.
