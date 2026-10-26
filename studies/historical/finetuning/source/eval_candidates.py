"""Score candidate edits for one parent commit: parse -> apply -> compile -> benchmark (valid, score).

Runs on a CPU allocation. Uses a private git worktree and cargo target dir. Results are cached by the hash of the
edited source tree, so identical candidates (across methods) are benchmarked once.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, time
from pathlib import Path

REPO = os.environ.get('LCO_STUDY_REPO', 'ecdsafail-challenge')
BLOCK = re.compile(r'<<<<<<< SEARCH (\S+)\n(.*?)=======\n(.*?)>>>>>>> REPLACE', re.S)
ENV = {**os.environ, 'CARGO_NET_OFFLINE': 'true', 'PATH': f"{os.environ['HOME']}/.cargo/bin:" + os.environ['PATH']}


def parse(text):
    blocks = BLOCK.findall(text)
    ok = bool(blocks) and all(p.startswith('src/point_add/') and '..' not in p and p.endswith('.rs') for p, _, _ in blocks)
    return blocks if ok else None


def apply(wt, blocks):
    files = {}
    for path, s, r in blocks:
        f = wt / path
        if path not in files: files[path] = f.read_text() if f.exists() else ''
        text = files[path]
        if s == '':
            if text: return None
            files[path] = r; continue
        if text.count(s) != 1: return None
        files[path] = text.replace(s, r, 1)
    for path, text in files.items():
        (wt / path).parent.mkdir(parents=True, exist_ok=True); (wt / path).write_text(text)
    return files


def run(cmd, wt, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=wt, env=ENV, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, time.time() - t0
    except subprocess.TimeoutExpired:
        return 'timeout', '', '', time.time() - t0


def bench(wt, target):
    env_target = {'CARGO_TARGET_DIR': str(target)}
    ENV.update(env_target)
    rc, out, err, tb = run(['cargo', 'build', '--release', '--locked', '--offline', '--bin', 'build_circuit', '--bin', 'eval_circuit'], wt, 900)
    if rc != 0: return {'compiles': False, 'build_s': tb, 'log': err[-1500:]}
    # benchmark.sh expects ./target/release; point it at our private target dir.
    link = wt / 'target'
    if not link.exists(): link.symlink_to(target)
    (wt / 'score.json').unlink(missing_ok=True)
    rc, out, err, tr = run(['bash', 'benchmark.sh', '--note', 'candidate'], wt, 1500)
    res = {'compiles': True, 'build_s': tb, 'bench_s': tr, 'valid': False, 'rc': rc}
    sj = wt / 'score.json'   # eval_circuit writes it only on success (all failures exit 1)
    if rc == 0 and sj.exists() and 'experiment OK' in out:
        s = json.loads(sj.read_text()); res.update(valid=True, score=s['score'], **s['metrics'])
    else:
        res['log'] = (out[-1000:] + '\n--stderr--\n' + err[-1000:])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--parent', required=True)
    ap.add_argument('--cands', nargs='+', required=True, help='gen jsonl files')
    ap.add_argument('--out', required=True)
    ap.add_argument('--work', required=True)
    a = ap.parse_args()
    wt = Path(a.work) / a.parent[:12]; target = Path(a.work) / (a.parent[:12] + '-target')
    if wt.exists(): subprocess.run(['git', '-C', REPO, 'worktree', 'remove', '--force', str(wt)], capture_output=True)
    subprocess.run(['git', '-C', REPO, 'worktree', 'add', '--detach', str(wt), a.parent], check=True, capture_output=True)
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if out.exists():
        for l in open(out):
            r = json.loads(l)
            if 'tree' in r and 'result' in r: cache[r['tree']] = r['result']
    done = {(json.loads(l)['label'], json.loads(l)['idx']) for l in open(out)} if out.exists() else set()
    with open(out, 'a') as fh:
        if ('parent', 0) not in done:
            r = bench(wt, target)
            fh.write(json.dumps({'parent': a.parent, 'label': 'parent', 'idx': 0, 'result': r}) + '\n'); fh.flush()
        for cf in a.cands:
            if not os.path.exists(cf): print('missing', cf); continue
            for line in open(cf):
                g = json.loads(line)
                if g['parent'] != a.parent: continue
                for i, text in enumerate(g['candidates']):
                    if (g['label'], i) in done: continue
                    rec = {'parent': a.parent, 'id': g['id'], 'label': g['label'], 'idx': i}
                    subprocess.run(['git', 'checkout', '-q', '--', '.'], cwd=wt); subprocess.run(['git', 'clean', '-qfd', 'src'], cwd=wt)
                    blocks = parse(text)
                    rec['parses'] = blocks is not None
                    files = apply(wt, blocks) if blocks else None
                    rec['applies'] = files is not None
                    if files is not None:
                        tree = hashlib.sha256(json.dumps(sorted(files.items())).encode()).hexdigest()
                        rec['tree'] = tree
                        if tree not in cache: cache[tree] = bench(wt, target)
                        rec['result'] = cache[tree]
                    fh.write(json.dumps(rec) + '\n'); fh.flush()
    subprocess.run(['git', 'checkout', '-q', '--', '.'], cwd=wt)
    subprocess.run(['git', '-C', REPO, 'worktree', 'remove', '--force', str(wt)], capture_output=True)
    shutil.rmtree(target, ignore_errors=True)


if __name__ == '__main__':
    main()
