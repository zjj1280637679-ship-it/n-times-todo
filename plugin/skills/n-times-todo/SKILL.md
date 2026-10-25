---
name: n-times-todo
description: Maintain an append-only todo notebook with full timestamped snapshots, automatic diffs and restore-as-new-version using a configured local N-times Todo runtime.
---

# N次贴的待办事项

This is an instruction-only skill plus a separately installed local Python runtime,
not an autonomous background monitor or native MCP server. A trusted host supplies
one private store directory. Do not point it at another app's internal files.

Use `n-times-todo --store <configured-private-directory> call` with one JSON request
on stdin. Run `tools` to inspect the closed argument lists. `todo.get` returns the
full current snapshot; `todo.history` paginates version metadata; `todo.diff` compares
any two stored versions. `todo.update` sends only intended changes, automatically
saving the complete resulting version and human-readable JSON/Markdown pages.

Typical request:

{"tool":"todo.update","arguments":{"expected_version":0,"request_id":"unique-request-1","reason":"用户确认下一步","changes":[{"op":"add","task":{"title":"检查示例交付"}}]}}

Supported changes: add task; edit stable id with set {title/status/notes}; remove
stable id; move id before another id (null means end). Status is todo/doing/blocked/done.
Generated task IDs are returned in the automatic changes. Reuse task IDs to retain
identity when titles change. A retry must use the identical request_id AND payload.
A conflicting expected_version requires reading the new state and reconciling user
intent; do not blindly rewrite or silently apply stale changes.

`todo.restore` takes version, expected_version, request_id and optional reason/actor.
It appends a new snapshot copying the old tasks; it never rewinds or deletes history.
An intentional unchanged update also creates a version. A repeated request does not.

Every successful write returns version/hash/changes/replayed/views. `views.status`
can be pending after the database commit: the history is saved, but some readable
pages need repair. Use `todo.sync` to recreate missing pages, not another new write.
Existing conflicting pages are not overwritten. Never claim sync succeeded if it
reports conflicts. `todo.verify` verifies stored hashes and chain consistency, not
who authored a claim or whether an older whole database was substituted.

Automatic capture covers this runtime's write API only. External app edits are not
captured unless an explicit adapter calls it. Keep user records private; the public
repository contains only software and synthetic examples. Follow host permission,
secret-handling and publication rules. This skill grants no new permissions.
