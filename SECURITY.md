# Security and data retention

Keep each store private. No network or external model is used by this runtime.
Do not include API keys, credentials, or data that must later be permanently erased.
Removing a current task intentionally retains it in historical snapshots.

Hash verification detects consistency failures, not the identity of an author or
rollback of the entire database. An owner of the filesystem can change the program,
remove triggers or forge a new history. No claim of WORM storage is made.

Exported JSON/Markdown are derived pages; editing them is not an update API and is
not watched. Conflicting materialized pages are preserved and reported. Backups
should use a consistent SQLite backup, not an arbitrary copy of a live journal.

Report a vulnerability privately to the repository owner before including sensitive
examples in a public issue. Use synthetic reproductions; never publish a user store.
