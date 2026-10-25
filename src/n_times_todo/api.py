"""Tiny tool facade. Store location is trusted host configuration, never tool input."""
from . import contracts as c
from .errors import TodoError

TOOLS = {
    'todo.get': {'required': [], 'optional': ['version']},
    'todo.update': {'required': ['changes', 'expected_version', 'request_id'], 'optional': ['reason', 'actor']},
    'todo.history': {'required': [], 'optional': ['after', 'limit']},
    'todo.diff': {'required': ['before', 'after'], 'optional': []},
    'todo.restore': {'required': ['version', 'expected_version', 'request_id'], 'optional': ['reason', 'actor']},
    'todo.verify': {'required': [], 'optional': []},
    'todo.sync': {'required': [], 'optional': []},
}


def dispatch(store, request):
    try:
        c.obj(request, ('tool', 'arguments'), ('tool', 'arguments'))
        name, arguments = request['tool'], request['arguments']
        if not isinstance(name, str) or name not in TOOLS:
            raise TodoError('unknown_tool')
        shape = TOOLS[name]
        c.obj(arguments, shape['required'] + shape['optional'], shape['required'])
        methods = {'todo.get': store.get, 'todo.update': store.update, 'todo.history': store.history,
                   'todo.diff': store.compare, 'todo.restore': store.restore,
                   'todo.verify': store.verify, 'todo.sync': store.sync}
        data = methods[name](**arguments)
        if name in ('todo.update', 'todo.restore'):
            snapshot = data.pop('snapshot')
            data.update({k: snapshot[k] for k in ('version', 'created_at', 'hash', 'parent_hash', 'kind', 'changes')})
        return {'ok': True, 'data': data}
    except TodoError as exc:
        return {'ok': False, 'error': {'code': exc.code, 'detail': exc.detail}}
