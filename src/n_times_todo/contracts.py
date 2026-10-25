"""Pure, closed input contracts. No I/O or global mutable state."""
import json
import re
from .errors import TodoError

MAX_TASKS = 1000
MAX_CHANGES = 200
STATUSES = frozenset({"todo", "doing", "blocked", "done"})
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def obj(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise TodoError("invalid_arguments", "unexpected or missing object fields")
    return value

def string(value, name, maximum, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise TodoError("invalid_arguments", name)
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        raise TodoError("invalid_arguments", name) from None
    if "\x00" in value:
        raise TodoError("invalid_arguments", name)
    return value

def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise TodoError("invalid_arguments", "id")
    return value

def version(value):
    if type(value) is not int or value < 0:
        raise TodoError("invalid_arguments", "version must be a nonnegative integer")
    return value

def task(value):
    obj(value, ("id", "title", "status", "notes"), ("id", "title"))
    result = {"id": identifier(value["id"]), "title": string(value["title"], "title", 2000),
              "status": value.get("status", "todo"), "notes": string(value.get("notes", ""), "notes", 20000, True)}
    if not isinstance(result["status"], str) or result["status"] not in STATUSES:
        raise TodoError("invalid_arguments", "status")
    return result

def tasks(value):
    if not isinstance(value, list) or len(value) > MAX_TASKS:
        raise TodoError("invalid_arguments", "tasks")
    result = [task(item) for item in value]
    ids = [item["id"] for item in result]
    if len(set(ids)) != len(ids):
        raise TodoError("duplicate_task_id")
    return result

def decode(text):
    if not isinstance(text, str):
        raise TodoError("invalid_arguments", "JSON text required")
    try:
        size = len(text.encode("utf-8", "strict"))
    except UnicodeError:
        raise TodoError("invalid_arguments", "invalid Unicode") from None
    if size > 2_000_000:
        raise TodoError("invalid_arguments", "request too large")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise TodoError("invalid_arguments", "duplicate JSON key")
            result[key] = value
        return result
    def bad_constant(_):
        raise TodoError("invalid_arguments", "nonfinite JSON number")
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise TodoError("invalid_arguments", "invalid JSON") from None
