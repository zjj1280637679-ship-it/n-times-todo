"""Create a temporary synthetic ledger and print its read-only audit context."""
import json
from pathlib import Path
import tempfile

from n_times_todo import TodoStore, export_audit


def main():
    examples = Path(__file__).resolve().parent
    # Setup deliberately uses the write API. export_audit itself only reads.
    with tempfile.TemporaryDirectory(prefix='n-times-audit-demo-') as directory:
        store = TodoStore(Path(directory) / 'synthetic-store')
        for name in ('add.json', 'finish.json'):
            request = json.loads((examples / name).read_text(encoding='utf-8'))
            store.update(**request['arguments'])
        context = json.loads((examples / 'audit-context.json').read_text(encoding='utf-8'))
        bundle = export_audit(store.root, context=context, since=0, expected_version=2)
        print(json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
