"""Pure change application and stable-ID, field-level diff."""
import copy
import uuid
from . import contracts as c
from .errors import TodoError

def apply(before, changes, request_id, reserved_ids=None):
    if not isinstance(changes, list) or not 1 <= len(changes) <= c.MAX_CHANGES:
        raise TodoError("invalid_arguments", "changes must contain 1..200 operations")
    result = copy.deepcopy(c.tasks(before))
    used_ids = set(reserved_ids or ()) | {t["id"] for t in result}
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            raise TodoError("invalid_arguments", "change")
        op = change.get("op")
        lookup = {t["id"]: n for n, t in enumerate(result)}
        if op == "add":
            c.obj(change, ("op", "task"), ("op", "task"))
            item = copy.deepcopy(change["task"])
            if not isinstance(item, dict):
                raise TodoError("invalid_arguments", "task")
            item.setdefault("id", "t-" + uuid.uuid5(uuid.NAMESPACE_URL, request_id + ":" + str(index)).hex)
            item = c.task(item)
            if item["id"] in used_ids:
                raise TodoError("duplicate_task_id")
            result.append(item)
            used_ids.add(item["id"])
        elif op == "edit":
            c.obj(change, ("op", "id", "set"), ("op", "id", "set"))
            key = c.identifier(change["id"])
            if key not in lookup:
                raise TodoError("task_not_found", key)
            patch = c.obj(change["set"], ("title", "status", "notes"))
            if not patch:
                raise TodoError("invalid_arguments", "empty edit")
            result[lookup[key]] = c.task({**result[lookup[key]], **patch})
        elif op == "remove":
            c.obj(change, ("op", "id"), ("op", "id"))
            key = c.identifier(change["id"])
            if key not in lookup:
                raise TodoError("task_not_found", key)
            result.pop(lookup[key])
        elif op == "move":
            c.obj(change, ("op", "id", "before"), ("op", "id", "before"))
            key = c.identifier(change["id"])
            target = change["before"]
            if target is not None:
                c.identifier(target)
            if key not in lookup or (target is not None and target not in lookup):
                raise TodoError("task_not_found")
            if target == key:
                continue
            item = result.pop(lookup[key])
            position = len(result) if target is None else next(i for i, t in enumerate(result) if t["id"] == target)
            result.insert(position, item)
        else:
            raise TodoError("invalid_arguments", "unknown change operation")
        if len(result) > c.MAX_TASKS:
            raise TodoError("invalid_arguments", "too many tasks")
    return result

def diff(before, after):
    before, after = c.tasks(before), c.tasks(after)
    old, new = ({t["id"]: t for t in group} for group in (before, after))
    shared = old.keys() & new.keys()
    modified = []
    completed, reopened = [], []
    for item in after:
        key = item["id"]
        if key not in shared:
            continue
        fields = {field: {"before": old[key][field], "after": new[key][field]}
                  for field in ("title", "status", "notes") if old[key][field] != new[key][field]}
        if fields:
            modified.append({"id": key, "fields": fields})
        if old[key]["status"] != "done" and new[key]["status"] == "done":
            completed.append(key)
        if old[key]["status"] == "done" and new[key]["status"] != "done":
            reopened.append(key)
    old_order = [t["id"] for t in before if t["id"] in shared]
    new_order = [t["id"] for t in after if t["id"] in shared]
    return {"added": [copy.deepcopy(t) for t in after if t["id"] not in old],
            "removed": [copy.deepcopy(t) for t in before if t["id"] not in new],
            "modified": modified, "completed": completed, "reopened": reopened,
            "order": {"before": old_order, "after": new_order} if old_order != new_order else None}
