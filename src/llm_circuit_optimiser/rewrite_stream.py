"""Mine and apply proved local rules on standard operation artifacts."""
from collections import Counter, deque
from pathlib import Path
import shutil
import struct
import subprocess

from .common import digest
from .rewrite_rules import ARITY, MAX_GATES, certify, cost

NO = (1 << 64) - 1
RECORD = struct.Struct('<II6Q')
KINDS = {0: 'NEG', 6: 'X', 7: 'Z', 8: 'CX', 9: 'CZ', 10: 'SWAP', 13: 'CCX', 14: 'CCZ'}
NUMBERS = {v: k for k, v in KINDS.items()}


def read_ops(path):
    with Path(path).open('rb', buffering=0) as source:
        header = source.read(16)
        if len(header) != 16 or header[:8] not in (b'QECCOPSZ', b'QECCOPS1'):
            raise ValueError('Invalid operation header')
        count = int.from_bytes(header[8:], 'little')
        if not 0 < count <= 50_000_000:
            raise ValueError('Invalid operation count')
        process = None
        stream = source
        if header[:8] == b'QECCOPSZ':
            exe = shutil.which('zstd')
            if not exe:
                raise ValueError('Install zstd to read compressed operation artifacts')
            process = subprocess.Popen([exe, '-dq', '-c'], stdin=source, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE)
            stream = process.stdout
        seen, pending = 0, b''
        try:
            while block := stream.read(RECORD.size * 16384):
                pending += block
                size = len(pending) // RECORD.size * RECORD.size
                for op in RECORD.iter_unpack(pending[:size]):
                    seen += 1
                    if seen > count or op[1] != 0 or op[0] > 17:
                        raise ValueError('Invalid operation record or count')
                    yield op
                pending = pending[size:]
            if seen != count or pending:
                raise ValueError('Operation count does not match body')
            if process and process.wait() != 0:
                raise ValueError('Operation decompression failed')
        finally:
            if process:
                if process.poll() is None:
                    process.kill()
                process.communicate()
                process.stdout.close()
                process.stderr.close()


def write_ops(path, operations):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which('zstd')
    if not exe:
        raise ValueError('Install zstd to write operation artifacts')
    # Exclusive creation also preserves partial output after an interruption.
    with path.open('xb', buffering=0) as target:
        target.write(b'QECCOPSZ' + bytes(8))
        process = subprocess.Popen([exe, '-q', '-c', '-3', '-T1'], stdin=subprocess.PIPE,
                                   stdout=target, stderr=subprocess.PIPE)
        count = 0
        try:
            block = bytearray()
            for op in operations:
                block.extend(RECORD.pack(*op))
                count += 1
                if len(block) >= RECORD.size * 16384:
                    process.stdin.write(block)
                    block.clear()
            process.stdin.write(block)
            process.stdin.close()
            error = process.stderr.read()
            if process.wait():
                raise ValueError('Operation compression failed: ' + error.decode(errors='replace'))
            target.seek(8)
            target.write(count.to_bytes(8, 'little'))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stderr.close()
            if not process.stdin.closed:
                process.stdin.close()
    return count


def normalize(ops):
    wires, gates = [], []
    for op in ops:
        kind = KINDS.get(op[0])
        if kind is None or any(x != NO for x in op[5:]) or op[1] != 0:
            return None
        arity = ARITY[kind]
        qs = [op[4], op[3], op[2]][:arity]
        if any(x != NO for x in [op[4], op[3], op[2]][arity:]) or NO in qs or len(set(qs)) != arity:
            return None
        ids = []
        for q in qs:
            if q not in wires:
                wires.append(q)
            ids.append(wires.index(q))
        if len(wires) > 6:
            return None
        gates.append([kind, *ids])
    return gates, wires


def lift(gate, wires):
    kind, *ids = gate
    qs = [wires[q] for q in ids] + [NO] * (3 - len(ids))
    return (NUMBERS[kind], 0, qs[2], qs[1], qs[0], NO, NO, NO)


def key(gates):
    return tuple(tuple(g) for g in gates)


def transform(operations, rules, stats):
    lookup = {}
    for rule in rules:
        before, after, wires = rule['before'], rule['after'], rule['wires']
        if not certify(before, after, wires)['accepted']:
            raise ValueError('Rule lacks an exact improving certificate')
        canonical = normalize([lift(g, list(range(wires))) for g in before])
        if canonical is None or canonical[0] != before or len(canonical[1]) != wires:
            raise ValueError('Rule must use canonical first-occurrence wire names')
        k = key(before)
        if k in lookup and lookup[k] != after:
            raise ValueError('Conflicting replacements for the same pattern')
        lookup[k] = after
    lengths = sorted({len(k) for k in lookup}, reverse=True)
    buffer = []
    stats.update(rewrites=0, static_toffoli_saved=0, input_ops=0, output_ops=0, rule_hits={})
    for op in operations:
        stats['input_ops'] += 1
        buffer.append(op)
        while True:
            changed = False
            for length in lengths:
                if length > len(buffer):
                    continue
                normal = normalize(buffer[-length:])
                if normal is None:
                    continue
                gates, wires = normal
                replacement = lookup.get(key(gates))
                if replacement is None:
                    continue
                buffer[-length:] = [lift(g, wires) for g in replacement]
                stats['rewrites'] += 1
                stats['static_toffoli_saved'] += cost(gates) - cost(replacement)
                rule_id = digest(gates)
                stats['rule_hits'][rule_id] = stats['rule_hits'].get(rule_id, 0) + 1
                changed = True
                break
            if not changed:
                break
        while len(buffer) > MAX_GATES:
            stats['output_ops'] += 1
            yield buffer.pop(0)
    for op in buffer:
        stats['output_ops'] += 1
        yield op


def mine(operations, limit=10):
    counts, windows = Counter(), deque(maxlen=MAX_GATES)
    for op in operations:
        windows.append(op)
        for length in range(3, len(windows) + 1):
            normal = normalize(list(windows)[-length:])
            if normal is None:
                continue
            gates, wires = normal
            if len(wires) >= 3 and cost(gates) >= 2:
                counts[key(gates)] += 1
    ordered = sorted(counts, key=lambda k: (-counts[k] * cost(k), -counts[k], len(k), k))[:limit]
    return [{'id': digest(k)[:16], 'wires': 1 + max(q for g in k for q in g[1:]),
             'before': [list(g) for g in k], 'occurrences': counts[k],
             'rank_weight': counts[k] * cost(k)} for k in ordered]
