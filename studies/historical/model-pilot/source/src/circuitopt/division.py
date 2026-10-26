"""Stream and check reversible modular division. No full point-addition claim."""
import hashlib
import json
import random
from .arithmetic import designs as D
from .arithmetic.revsim import Circuit

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()

def seed(label):
    return int.from_bytes(hashlib.sha256(label.encode()).digest(), 'big')

FAMILIES = {
    'inverse': ('Inverse and multiply', D.d0_inverse_then_multiply),
    'replay': ('Record and replay', D.d1_record_and_replay),
    'width': ('Width reuse', D.d2_trimmed_walk),
    'jump2': ('Jump-2', D.d3_jump2),
    'pingpong': ('Ping-pong', D.d4_pingpong),
    'leapfrog': ('Leapfrog reconstruction', D.d6_leapfrog),
}

def build_recipe(circuit, x, y, recipe):
    """Delegate candidate emission while preserving the fixed checker."""
    from .candidate import build_recipe as emit
    return emit(circuit,x,y,recipe)


class GateSink:
    """Simulate emitted gates immediately. This avoids storing millions of Python tuples."""
    def __init__(self, shots, measurement_seed):
        self.shots, self.full = shots, (1 << shots) - 1
        self.state, self.bits = {}, {}
        self.phase = self.dirty = self.toff = self.count = 0
        self.qmax = -1
        self.by_label = {}
        self.rng = random.Random(measurement_seed)
        self.hash = hashlib.sha256()

    def load(self, reg, values):
        for s, value in enumerate(values):
            for i, q in enumerate(reg):
                if (value >> i) & 1:
                    self.state[q] = self.state.get(q, 0) | (1 << s)

    def read(self, reg):
        return [sum(((self.state.get(q, 0) >> s) & 1) << i for i, q in enumerate(reg))
                for s in range(self.shots)]

    def append(self, op):
        kind, qs, cond, label = op
        self.hash.update(canonical((kind, qs, cond)) + b'\n')
        self.count += 1
        self.qmax = max(self.qmax, *qs[:1] if kind == 'HMR' else qs)
        state = self.state
        m = self.full if cond is None else self.bits.get(cond, 0)
        if kind in ('CCX', 'CCZ'):
            a, b, t = qs
            v = m & state.get(a, 0) & state.get(b, 0)
            if kind == 'CCX':
                state[t] = state.get(t, 0) ^ v
            else:
                self.phase ^= v & state.get(t, 0)
            work = m.bit_count()
            self.toff += work
            self.by_label[label] = self.by_label.get(label, 0) + work
        elif kind == 'CX':
            a, t = qs
            state[t] = state.get(t, 0) ^ (m & state.get(a, 0))
        elif kind == 'X':
            state[qs[0]] = state.get(qs[0], 0) ^ m
        elif kind == 'SWAP':
            a, b = qs
            va, vb = state.get(a, 0), state.get(b, 0)
            d = (va ^ vb) & m
            state[a], state[b] = va ^ d, vb ^ d
        elif kind == 'Z':
            self.phase ^= m & state.get(qs[0], 0)
        elif kind == 'CZ':
            self.phase ^= m & state.get(qs[0], 0) & state.get(qs[1], 0)
        elif kind == 'HMR':
            t, bit = qs
            r = self.rng.getrandbits(self.shots) & m
            self.phase ^= state.get(t, 0) & r
            self.bits[bit] = (self.bits.get(bit, 0) & ~m) | r
            state[t] = state.get(t, 0) & ~m
        elif kind == 'FREE':
            self.dirty |= state.get(qs[0], 0)
            state[qs[0]] = 0
        else:
            raise ValueError(kind)


def inspect_outputs(sink, xr, qr, xs, ys, p, streams=2):
    got_x, got_q = sink.read(xr), sink.read(qr)
    allowed = set(xr + qr)
    scratch = sink.dirty
    for q, values in sink.state.items():
        if q not in allowed:
            scratch |= values
    channels = {'output': set(), 'phase': set(), 'scratch': set()}
    bad_shots = set()
    examples = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        expected = y * pow(x, -1, p) % p
        for s in range(streams):
            lane = i * streams + s
            bad = []
            for ch, failed in [('output', got_x[lane] != x or got_q[lane] != expected),
                               ('phase', (sink.phase >> lane) & 1),
                               ('scratch', (scratch >> lane) & 1)]:
                if failed:
                    channels[ch].add(i)
                    bad.append(ch)
            if bad:
                bad_shots.add(lane)
                if len(examples) < 8:
                    examples.append(dict(index=i, stream=s, x=str(x), y=str(y),
                                         expected=str(expected), got_x=str(got_x[lane]),
                                         got_quotient=str(got_q[lane]), channels=bad))
    return channels, bad_shots, examples


def division_batch(c, xs, ys, measurement_seed):
    circ = Circuit(measured_uncompute=c['cleanup'] == 'measured')
    sink = GateSink(2 * len(xs), measurement_seed)
    circ.ops = sink
    x, y = circ.alloc(c['bits']), circ.alloc(c['bits'])
    sink.load(x, [v for v in xs for _ in range(2)])
    sink.load(y, [v for v in ys for _ in range(2)])
    xr, qr = build_recipe(circ, x, y, c)
    if circ.live != 2 * c['bits']:
        raise ValueError('Unexpected live output width')
    channels, bad_shots, examples = inspect_outputs(sink, xr, qr, xs, ys, int(c['prime']))
    return dict(channels=channels, bad_shots=bad_shots, examples=examples,
                T=sink.toff / sink.shots, Q=sink.qmax + 1, live_peak_Q=circ.peak,
                operation_count=sink.count, operation_sha256=sink.hash.hexdigest(),
                toffolis_by_block={k: v / sink.shots for k, v in sink.by_label.items()})


def input_pairs(p, label, start, count, stress=False):
    xs, ys = [], []
    for i in range(start, start + count):
        rng = random.Random(seed(f'{label}/input/{i}'))
        if stress:
            special = [1, p - 1, 2, p - 2] + [1 << k for k in range(p.bit_length()) if 1 << k < p]
            x = special[i % len(special)]
            y = [0, 1, p - 1, x][i % 4]
        else:
            x, y = rng.randrange(1, p), rng.randrange(p)
        xs.append(x)
        ys.append(y)
    return xs, ys


def evaluate_division(c, pairs, label, stress=False, batch_size=1024):
    failures = {k: 0 for k in ('output', 'phase', 'scratch')}
    failed_inputs = failed_shots = 0
    total_t = 0
    examples, hashes = [], set()
    inp_hash = hashlib.sha256()
    blocks = {}
    for start in range(0, pairs, batch_size):
        n = min(batch_size, pairs - start)
        xs, ys = input_pairs(int(c['prime']), label, start, n, stress)
        inp_hash.update(canonical(list(zip(xs, ys))))
        b = division_batch(c, xs, ys, seed(f'{label}/measurement/{start}'))
        hashes.add(b['operation_sha256'])
        total_t += n * b['T']
        for k, v in b['toffolis_by_block'].items():
            blocks[k] = blocks.get(k, 0) + n * v
        failed_inputs += len(set().union(*b['channels'].values()))
        failed_shots += len(b['bad_shots'])
        for k in failures:
            failures[k] += len(b['channels'][k])
        for example in b['examples']:
            if len(examples) < 8:
                examples.append({**example, 'index': example['index'] + start})
    if len(hashes) != 1:
        raise ValueError('Circuit emission depends on evaluation input')
    return dict(T=total_t / pairs, Q=b['Q'], live_peak_Q=b['live_peak_Q'],
                operation_sha256=hashes.pop(), operation_count=b['operation_count'],
                input_sha256=inp_hash.hexdigest(), failed_inputs=failed_inputs,
                failed_shots=failed_shots, channel_failed_inputs=failures,
                first_failures=examples, toffolis_by_block={k: v / pairs for k, v in blocks.items()},
                time_scope='construction, stream hashing, simulation and checking combined')


def export_circuit(recipe, directory):
    """Save an immutable gate stream and its register map before evaluation."""
    import gzip
    from pathlib import Path
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    meta_path=directory/'circuit.json'
    ops_path=directory/'gates.jsonl.gz'
    if meta_path.exists():
        meta=json.loads(meta_path.read_text())
        if meta['recipe']!=recipe or hashlib.sha256(ops_path.read_bytes()).hexdigest()!=meta['file_sha256']:
            raise ValueError('Saved circuit hash mismatch')
        return meta
    class Writer:
        def __init__(self, target):
            self.target=target
            self.hash=hashlib.sha256()
            self.count=0
        def append(self,op):
            self.target.write(canonical(op)+b'\n')
            self.hash.update(canonical(op[:3])+b'\n')
            self.count+=1
    circ=Circuit(measured_uncompute=recipe['cleanup']=='measured')
    x,y=circ.alloc(recipe['bits']),circ.alloc(recipe['bits'])
    with open(ops_path,'xb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as out:
            writer=Writer(out)
            circ.ops=writer
            xr,qr=build_recipe(circ,x,y,recipe)
    meta={'recipe':recipe,'inputs':[x,y],'outputs':[xr,qr],'operation_sha256':writer.hash.hexdigest(),
          'operation_count':writer.count,'live_peak_Q':circ.peak,
          'file_sha256':hashlib.sha256(ops_path.read_bytes()).hexdigest()}
    meta_path.write_text(json.dumps(meta,indent=2)+'\n')
    return meta


def evaluate_saved(directory, pairs, label, input_path, batch_size=1024):
    """Evaluate the saved gate file. Save every input and measurement batch seed."""
    import gzip
    from pathlib import Path
    directory=Path(directory)
    meta=json.loads((directory/'circuit.json').read_text())
    if hashlib.sha256((directory/'gates.jsonl.gz').read_bytes()).hexdigest()!=meta['file_sha256']:
        raise ValueError('Saved circuit hash mismatch')
    recipe=meta['recipe']
    failures={k:0 for k in ('output','phase','scratch')}
    failed_inputs=failed_shots=0
    total_t=0
    examples=[]
    input_hash=hashlib.sha256()
    input_path=Path(input_path)
    input_path.parent.mkdir(parents=True,exist_ok=True)
    with open(input_path,'xb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as inputs:
            for start in range(0,pairs,batch_size):
                n=min(batch_size,pairs-start)
                xs,ys=input_pairs(int(recipe['prime']),label,start,n)
                measurement_seed=seed(f'{label}/measurement/{start}')
                row=canonical({'start':start,'x':xs,'y':ys,'streams':2,'measurement_seed':str(measurement_seed)})+b'\n'
                inputs.write(row)
                input_hash.update(row)
                sink=GateSink(2*n,measurement_seed)
                sink.load(meta['inputs'][0],[x for x in xs for _ in range(2)])
                sink.load(meta['inputs'][1],[y for y in ys for _ in range(2)])
                with gzip.open(directory/'gates.jsonl.gz','rt') as stream:
                    for line in stream:
                        sink.append(json.loads(line))
                if sink.hash.hexdigest()!=meta['operation_sha256']:
                    raise ValueError('Saved operation hash mismatch')
                ch,bad,first=inspect_outputs(sink,*meta['outputs'],xs,ys,int(recipe['prime']))
                failed_inputs+=len(set().union(*ch.values()))
                failed_shots+=len(bad)
                total_t+=sink.toff/2
                for k in failures: failures[k]+=len(ch[k])
                examples.extend({**v,'index':start+v['index']} for v in first[:max(0,8-len(examples))])
    return {'Q':sink.qmax+1,'T':total_t/pairs,'failed_inputs':failed_inputs,
            'failed_shots':failed_shots,'channel_failed_inputs':failures,'first_failures':examples,
            'pairs':pairs,'streams':2,'input_sha256':input_hash.hexdigest(),
            'operation_sha256':meta['operation_sha256'],'circuit_file_sha256':meta['file_sha256'],
            'inputs_file':str(input_path),'circuit_directory':str(directory)}

def replay_evidence(directory, input_path):
    """Replay the exact saved inputs and measurement seeds against the saved gates."""
    import gzip
    from pathlib import Path
    directory=Path(directory)
    meta=json.loads((directory/'circuit.json').read_text())
    if hashlib.sha256((directory/'gates.jsonl.gz').read_bytes()).hexdigest()!=meta['file_sha256']:
        raise ValueError('Saved circuit hash mismatch')
    pairs=failed_inputs=failed_shots=total_t=0
    input_hash=hashlib.sha256()
    channels={k:0 for k in ('output','phase','scratch')}
    with gzip.open(input_path,'rb') as inputs:
        for raw in inputs:
            input_hash.update(raw)
            batch=json.loads(raw)
            xs,ys=batch['x'],batch['y']
            if batch['streams']!=2 or len(xs)!=len(ys): raise ValueError('Invalid saved input batch')
            sink=GateSink(2*len(xs),int(batch['measurement_seed']))
            sink.load(meta['inputs'][0],[x for x in xs for _ in range(2)])
            sink.load(meta['inputs'][1],[y for y in ys for _ in range(2)])
            with gzip.open(directory/'gates.jsonl.gz','rt') as gates:
                for line in gates: sink.append(json.loads(line))
            if sink.hash.hexdigest()!=meta['operation_sha256']: raise ValueError('Operation hash changed')
            ch,bad,_=inspect_outputs(sink,*meta['outputs'],xs,ys,int(meta['recipe']['prime']))
            pairs+=len(xs)
            total_t+=sink.toff/2
            failed_inputs+=len(set().union(*ch.values()))
            failed_shots+=len(bad)
            for k in channels: channels[k]+=len(ch[k])
    if pairs<1: raise ValueError('No saved inputs')
    return {'Q':sink.qmax+1,'T':total_t/pairs,'pairs':pairs,'failed_inputs':failed_inputs,
            'failed_shots':failed_shots,'channel_failed_inputs':channels,'input_sha256':input_hash.hexdigest(),
            'operation_sha256':meta['operation_sha256']}
