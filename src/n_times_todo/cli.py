import argparse
import json
import sys
from . import __version__
from .api import dispatch, TOOLS
from .contracts import decode
from .errors import TodoError
from .render import markdown
from .store import TodoStore
from .audit import export_audit


def main(argv=None):
    parser = argparse.ArgumentParser(description='N次贴: append-only todo snapshots and automatic diffs')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--store', default='.n-times-todo', help='private local history directory (default: .n-times-todo)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='initialize an empty ledger and materialize version 0')
    sub.add_parser('call', help='read one {tool,arguments} JSON request from stdin')
    sub.add_parser('tools', help='print tool names and closed argument lists')
    show = sub.add_parser('show', help='show a complete current or historical snapshot')
    show.add_argument('--at', type=int)
    show.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    sub.add_parser('history')
    comparison = sub.add_parser('diff')
    comparison.add_argument('before', type=int)
    comparison.add_argument('after', type=int)
    audit = sub.add_parser('audit', help='export read-only audit context; read explicit goal/evidence JSON from stdin')
    audit.add_argument('--since', type=int, default=0, help='baseline version (default: 0)')
    audit.add_argument('--expected-version', type=int, help='reject a different committed head')
    sub.add_parser('verify')
    sub.add_parser('sync', help='recreate missing version pages without changing history')
    args = parser.parse_args(argv)
    try:
        if args.command == 'tools':
            output = {'schema_version': 1, 'tools': TOOLS, 'store': 'host-configured; not a model argument'}
        elif args.command == 'audit':
            try:
                context = decode(sys.stdin.read(2_000_001))
            except UnicodeError:
                raise TodoError('invalid_arguments', 'invalid Unicode') from None
            output = export_audit(args.store, context=context, since=args.since,
                                  expected_version=args.expected_version)
        else:
            store = TodoStore(args.store)
            if args.command == 'call':
                text = sys.stdin.read(2_000_001)
                output = dispatch(store, decode(text))
            elif args.command == 'show':
                snapshot = store.get(args.at)
                if args.format == 'markdown':
                    print(markdown(snapshot), end='')
                    return 0
                output = snapshot
            elif args.command == 'init':
                output = {'snapshot': store.get(), 'views': store.sync()}
            elif args.command == 'history':
                output = store.history()
            elif args.command == 'diff':
                output = store.compare(args.before, args.after)
            else:
                output = getattr(store, args.command)()
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 1 if isinstance(output, dict) and output.get('ok') is False else 0
    except TodoError as exc:
        print(json.dumps({'ok': False, 'error': {'code': exc.code, 'detail': exc.detail}}, ensure_ascii=False))
        return 1
    except (OSError, __import__('sqlite3').Error):
        print(json.dumps({'ok': False, 'error': {'code': 'store_unavailable', 'detail': 'Check local store access; retry writes with the same request_id.'}}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
