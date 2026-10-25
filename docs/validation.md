# Prototype validation

Run from the repository root:

```sh
PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
python scripts/check_model.py
python scripts/build_release.py
python scripts/check_installed.py
```

The absolute PYTHONPATH matters because a subprocess test intentionally changes to
an unrelated working directory. Runtime dependencies are standard library only;
building needs installed setuptools (>=68), wheel support and pip. The release
builder disables package-index access and uses a fixed ZIP timestamp.

## Tested boundaries

- Complete snapshots and stable IDs; title edits do not create another identity.
- Add/remove/edit/order; completion/reopening; inserted IDs do not falsely mark all
  surviving tasks as moved.
- Restoring old tasks appends a new version while earlier snapshots remain intact.
- One request ID commits once; changed payload conflicts; generated IDs replay.
- CAS stale writes reject; threaded writers/readers use consistent SQLite snapshots.
- Process termination before commit rolls back; after commit retains snapshot and
  request binding, with missing Markdown/JSON rebuilt later.
- Mirror read/write errors return a durable commit with pending views; conflicting
  files are preserved; existing equal files do not get rewritten.
- Snapshot hashes also bind request IDs/digests; index tampering fails verification.
- Nonfinite numbers, duplicate JSON keys, unknown fields, invalid types and oversized
  full snapshots fail without partially committing a batch.
- User text cannot inject Markdown images/links/HTML into generated pages.
- Unknown future database version is rejected without a silent schema downgrade.
- Fresh wheel installation outside source tree exercises the intended tool surface.

The finite model has 421 reachable states and 1560 edges; all three injected
mutations are detected. It models atomic transactions as a premise, not an
implementation of SQLite or a proof about arbitrary hardware failures.

Independent review used synthetic stores, including eight concurrent processes
and abnormal input combinations. Review found post-commit export errors and
Markdown injection; fixes have dedicated regression tests. No real user todo,
credentials, private system file, external application hook or cloud-plugin
installation was used to establish these results.

This is a working local prototype. Do not infer whole-system formal correctness,
OS-level immutable storage, authenticated authorship, or external app monitoring.

## Read-only audit export

The audit extension adds 28 synthetic acceptance tests to the original 35 tests
(63 total). Run with an absolute source path so subprocesses outside the checkout
can import the runtime:

```sh
PYTHONPATH="$PWD/src" python -m unittest discover -s tests -v
python scripts/build_release.py
python scripts/check_installed.py
PYTHONPATH="$PWD/src" python examples/audit-demo.py
```

Audit cases cover full snapshots and stable IDs, original goal/provenance retention,
current-version evidence bindings, missing/uncertain inputs, intermediate changes
hidden by endpoint diffs, and restoration. File-tree fingerprints check names,
bytes, modes and modification times across repeated reads and failures, including
conflicting/missing pages and read-only filesystem permissions. Atime is excluded.
Missing stores are never initialized. WAL is rejected before a connection can
create shared-memory files. Hash/index tampering, future schemas, invalid JSON,
strict UTF-8 stdin failures, oversized context and Python version integers, stale
heads and incorrect evidence references fail explicitly.

Two audit readers and a writer exercise actual SQLite concurrency. Installed-wheel
smoke checks run outside the source checkout with PYTHONPATH removed, covering
audit CLI/Python availability, no-write export and missing-store failure. These
checks establish packaging and consistency behavior, not evidence truth or semantic
findings. See [audit boundaries](audit.md) for unsupported external journal-mode
changes, database substitution, and the limits of source declarations.
