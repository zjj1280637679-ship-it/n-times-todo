# Small modules, explicit relations

## Modules

1. contracts: closed syntax / task normalization / canonical JSON; pure.
2. changes: sequential batch reducer + stable-ID diff; pure.
3. store: single SQLite transaction authority, version relation, CAS, request idempotency, hashes.
4. render: inert human-readable full page + automatic difference annotation; pure.
5. api / cli: narrow dispatch and transport; store path supplied by trusted host.
6. audit: independent read-only SQLite entrypoint; validates explicit evidence bindings and packages context without semantic judgments. See [audit](audit.md) for journal and concurrency boundaries.

There is no second independent journal or auto-retry agent. Historical task IDs remain reserved; restore is an explicit relation to a prior immutable snapshot. Module relationships are managed by store, not by arbitrary cross-imports.

## Write lifecycle

Validate request → BEGIN IMMEDIATE → verify existing chain and request index →
lookup request_id → [exact replay OR check expected head] → apply all changes /
copy restored tasks → append complete hashed snapshot + bound request index →
COMMIT → materialize complete JSON/Markdown pages → return commit receipt.

New writes save full snapshots and diff. The model need only send differences.
A read begins one SQLite read transaction so snapshot rows and request-index rows
come from a consistent view while another process commits.

Replay lookup precedes expected-version rejection: a request that committed but
whose response was lost can safely return its original version despite a newer
head. A changed payload with the same ID is a conflict, never another write.

There is no mutable latest pointer: latest is max committed version. SQLite atomic
commit is the linearization point. A process killed before commit leaves neither
new snapshot nor index; killed after commit leaves both. Materialized files are
not authoritative, so a crash or permission failure during export cannot lose
already committed history. Missing pages can be recreated. Existing conflicts
are reported, not overwritten. Temporary view bytes are fsynced before exclusive
hard-link publication, so ordinary failures do not expose partial final files.
An interrupted export may leave an unreferenced `.pending-*` temporary file;
normal sync does not delete unknown files. The database remains authoritative.

## Documents and provenance

Each version contains schema_version, stable version, parent_version/hash,
created_at UTC, kind, declared reason/actor, restored_from, request_id/digest,
complete tasks, and recomputed changes. Hashes use canonical UTF-8 JSON. Request
ID/digest is included in the hashed snapshot and must match the append-only
request index. Ordinary APIs and SQLite triggers disallow rewriting/deleting
history. Cross-version diffs do not replace full documents.

Trusted host configuration chooses the private local store. The plugin cannot
read another application's task store unless someone separately implements and
authorizes an adapter. Exported pages are inert task text, not executable HTML or
user-provided rich Markdown. Do not save credentials in immutable history.

This is consistency checking, not cryptographic authentication: the same OS user
can drop triggers, forge all hashes or replace the whole database with an older
backup. Actor/reason are declarations. External timestamping, signed actors,
remote replicas and disaster-recovery backups are outside this prototype. Use
SQLite backup tooling rather than racing copies of a live database/journal.
POSIX permissions are best effort (new directory0700/files0600); choose a private
parent directory. Existing directory permissions are not silently reconfigured.

## Verification scope

scripts/check_model.py is a finite two-writer/two-request/two-value abstraction,
with at most two committed updates, assuming atomic transactions. It explores
421 states / 1560 edges with witnesses for real commit, conflict, replay and abort;
mutants overwrite_history, repeat_request, ignore_cas each violate an invariant.
Budgets: 10000 states, 5 seconds and 32 MiB Python traced allocations; excess fails.
This does not prove SQLite, OS persistence, rendering or implementation refinement.

Implementation tests separately cover actual SQLite threads, process exits before
and after commit, request-index binding, view repair/conflict, and pure reducer/
diff behavior. Limits and absence of external application monitoring remain explicit.
