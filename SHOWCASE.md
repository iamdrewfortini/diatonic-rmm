# Observer API showcase

The demo uses generated records and an in memory projection store. It is isolated to loopback and does not contact a real estate.

Synthetic snapshot -> allowlist, size and freshness checks -> tenant wall projection -> observer snapshot API (ETag and bounded cache)
Edge TCP checks -> authenticated observations

Demonstrated behavior: expiring bearer identities with server assigned tenant and actor; distinct observer and collector roles; allowlisted, bounded wall projections; freshness checks and conditional reads; timed edge checks without remote commands; and proposed actions that need a separate approver in the prototype store.

This is an API and wall projection slice. It does not provide endpoint enrollment, remote shell, software deployment, patch management, policy enforcement, or action execution.
