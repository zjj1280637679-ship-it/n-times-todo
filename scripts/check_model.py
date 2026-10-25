"""Finite two-writer/two-request CAS abstraction; not a refinement proof."""
from collections import deque
from dataclasses import dataclass, replace
import json
import time
import tracemalloc

@dataclass(frozen=True)
class State:
    history: tuple = (0,)
    requests: tuple = ()  # (request id, expected version, desired value, committed version)
    pending: tuple = (None, None)


def transitions(state, mutant=None):
    if len(state.history) < 3:
        for writer in range(2):
            if state.pending[writer] is None:
                for request in range(2):
                    for desired in range(2):
                        pending = list(state.pending)
                        pending[writer] = (request, len(state.history)-1, desired)
                        yield 'start', replace(state, pending=tuple(pending))
    for writer, command in enumerate(state.pending):
        if command is None:
            continue
        remaining = list(state.pending)
        remaining[writer] = None
        remaining = tuple(remaining)
        yield 'abort_before_commit', replace(state, pending=remaining)
        request, expected, desired = command
        existing = next((r for r in state.requests if r[0] == request), None)
        if existing is not None and mutant != 'repeat_request':
            yield 'replay_or_conflict', replace(state, pending=remaining)
        elif expected != len(state.history)-1 and mutant != 'ignore_cas':
            yield 'version_conflict', replace(state, pending=remaining)
        elif len(state.history) < 3:
            history = state.history + (desired,)
            if mutant == 'overwrite_history':
                history = (1-state.history[0],) + history[1:]
            record = (request, expected, desired, len(history)-1)
            yield ('commit', command), State(history, state.requests + (record,), remaining)


def edge_error(before, event, after):
    if after.history[:len(before.history)] != before.history:
        return 'immutable_prefix'
    if len(after.history) > len(before.history) + 1:
        return 'single_append'
    ids = [r[0] for r in after.requests]
    if len(set(ids)) != len(ids):
        return 'one_commit_per_request'
    if len(after.requests) != len(after.history)-1:
        return 'atomic_snapshot_and_request'
    if isinstance(event, tuple) and event[0] == 'commit':
        request, expected, desired = event[1]
        if expected != len(before.history)-1:
            return 'compare_and_swap'
        if after.history[-1] != desired:
            return 'snapshot_matches_intent'
    elif after.history != before.history:
        return 'no_commit_no_change'
    return None


def explore(mutant=None):
    start = State()
    seen, queue = {start}, deque([start])
    edges, commits, conflicts, replays, aborts = 0, 0, 0, 0, 0
    started = time.monotonic()
    while queue:
        current = queue.popleft()
        for event, following in transitions(current, mutant):
            edges += 1
            error = edge_error(current, event, following)
            if error:
                return {'ok': False, 'violation': error, 'states': len(seen), 'edges': edges}
            commits += isinstance(event, tuple)
            conflicts += event == 'version_conflict'
            replays += event == 'replay_or_conflict'
            aborts += event == 'abort_before_commit'
            if following not in seen:
                seen.add(following)
                queue.append(following)
            if len(seen) > 10000 or time.monotonic()-started > 5:
                raise RuntimeError('model budget exceeded')
    assert min(commits, conflicts, replays, aborts) > 0, 'non-vacuous witnesses required'
    return {'ok': True, 'states': len(seen), 'edges': edges,
            'witness_edges': {'commits': commits, 'conflicts': conflicts, 'replays_or_conflicts': replays, 'aborts': aborts}}


def run():
    tracemalloc.start()
    started = time.monotonic()
    baseline = explore()
    mutants = {name: explore(name) for name in ('overwrite_history', 'repeat_request', 'ignore_cas')}
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    if peak > 32*1024*1024 or not all(not x['ok'] for x in mutants.values()):
        raise RuntimeError('model acceptance failed')
    return {'baseline': baseline, 'mutants': mutants, 'seconds': time.monotonic()-started,
            'peak_traced_bytes': peak, 'scope': 'two writers, two IDs, two values, at most two committed updates; atomic transaction assumed, not implementation proof'}

if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
