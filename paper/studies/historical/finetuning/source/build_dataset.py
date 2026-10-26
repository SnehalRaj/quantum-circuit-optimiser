"""Build the ecdsa-edits dataset from ecdsafail-challenge history.

Each example: pre-edit excerpts of the touched src/point_add .rs files -> hypothesis + SEARCH/REPLACE blocks.
Split is temporal (oldest -> train, newest -> test). Every target is checked to reproduce the commit exactly.
"""
import argparse, difflib, hashlib, json, re, subprocess
from pathlib import Path
from transformers import AutoTokenizer

SYSTEM = ("You are an expert in reversible quantum circuit construction for the secp256k1 point-addition challenge. "
          "The Rust code in src/point_add builds a reversible circuit; the score is mean Toffoli count x peak qubits (lower is better). "
          "The circuit must stay classically correct, uncompute every ancilla, and leave no phase garbage. "
          "Propose one candidate edit: a one-line hypothesis, then SEARCH/REPLACE blocks that apply to the shown code.")
CTX = 40          # lines of context around each hunk in the prompt
HUNK_CTX = 2      # unchanged lines kept inside each SEARCH block
SUBMISSION = re.compile(r'^(Accept|Validate) submission [0-9a-f-]{36}$')


def git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, check=True).stdout


def blocks_for(old, new):
    """SEARCH/REPLACE blocks (old_text, new_text) whose SEARCH text is unique in old."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    out = []
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(HUNK_CTX):
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        while ''.join(a).count(''.join(a[i1:i2])) != 1 or not ''.join(a[i1:i2]).strip():
            if i1 == 0 and i2 == len(a): break
            if i1 > 0: i1 -= 1; j1 -= 1
            if i2 < len(a): i2 += 1; j2 += 1
        out.append((''.join(a[i1:i2]), ''.join(b[j1:j2]), i1, i2))
    return out


def apply_blocks(text, blocks):
    for s, r in blocks:
        if s == '':
            if text: raise ValueError('create on existing file')
            text = r; continue
        if text.count(s) != 1: raise ValueError('SEARCH not unique')
        text = text.replace(s, r, 1)
    return text


def render(path, s, r):
    return f'<<<<<<< SEARCH {path}\n{s}=======\n{r}>>>>>>> REPLACE\n'


def excerpt(path, old, ranges):
    lines = old.splitlines()
    spans = sorted((max(0, i1 - CTX), min(len(lines), i2 + CTX)) for i1, i2 in ranges)
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]: merged[-1][1] = max(merged[-1][1], e)
        else: merged.append([s, e])
    parts = [f'// FILE: {path} (lines {s + 1}-{e} of {len(lines)})\n' + '\n'.join(lines[s:e]) for s, e in merged]
    return '\n// ...\n'.join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='ecdsafail-challenge')
    ap.add_argument('--out', default='data')
    ap.add_argument('--max-changed', type=int, default=150)
    ap.add_argument('--max-files', type=int, default=3)
    ap.add_argument('--max-tokens', type=int, default=6144)
    ap.add_argument('--drop-tests', action='store_true')
    a = ap.parse_args()
    tok = AutoTokenizer.from_pretrained('Qwen/Qwen2.5-Coder-7B-Instruct')
    log = git(a.repo, 'log', '--no-merges', '--reverse', '--format=%H|%P|%at|%an|%s', '--', 'src/point_add')
    rows, seen, drop = [], set(), {}
    def skip(k): drop[k] = drop.get(k, 0) + 1
    for line in log.splitlines():
        h, parents, t, author, subj = line.split('|', 4)
        if len(parents.split()) != 1: skip('root'); continue
        p = parents
        stat = git(a.repo, 'diff', '--numstat', p, h, '--', 'src/point_add')
        files, changed, bad = [], 0, False
        for st in stat.splitlines():
            x, y, f = st.split('\t')
            if f.startswith('src/point_add/memory/'): continue
            if not f.endswith('.rs') or x == '-': bad = True; break
            files.append(f); changed += int(x) + int(y)
        if bad: skip('non_rs_or_binary'); continue
        if not files: skip('no_rs'); continue
        if changed > a.max_changed or len(files) > a.max_files: skip('too_large'); continue
        target, excerpts, ok = [], [], True
        for f in files:
            old = subprocess.run(['git', '-C', a.repo, 'show', f'{p}:{f}'], capture_output=True, text=True).stdout
            new = subprocess.run(['git', '-C', a.repo, 'show', f'{h}:{f}'], capture_output=True, text=True).stdout
            if not new: ok = False; break          # deletions are out of scope
            if not old:
                bl = [('', new)]; excerpts.append(f'// FILE: {f} (new file)')
            else:
                raw = blocks_for(old, new)
                bl = [(s, r) for s, r, _, _ in raw]
                excerpts.append(excerpt(f, old, [(i1, i2) for _, _, i1, i2 in raw]))
            try:
                if apply_blocks(old, bl) != new: ok = False; break
            except ValueError: ok = False; break
            target += [render(f, s, r) for s, r in bl]
        if not ok: skip('roundtrip_fail'); continue
        if a.drop_tests and any(re.search(r'#\[(test|cfg\(test\))\]', r) and not re.search(r'#\[(test|cfg\(test\))\]', s)
                                for s, r in [(t.split('=======\n')[0], t.split('=======\n')[1]) for t in target]):
            skip('adds_tests'); continue
        hyp = 'Improve the circuit.' if SUBMISSION.match(subj) else subj.strip()
        user = ('Current code excerpts (pre-edit):\n\n```rust\n' + '\n// ===\n'.join(excerpts) + '\n```\n\n'
                'Propose one edit that lowers Toffoli x peak qubits while preserving correctness.')
        assistant = f'Hypothesis: {hyp}\n\n' + ''.join(target)
        key = hashlib.sha256((user + assistant).encode()).hexdigest()
        if key in seen: skip('duplicate'); continue
        seen.add(key)
        msgs = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user},
                {'role': 'assistant', 'content': assistant}]
        n = len(tok.apply_chat_template(msgs, tokenize=True))
        if n > a.max_tokens: skip('too_many_tokens'); continue
        rows.append({'id': h, 'parent': p, 'time': int(t), 'author': author, 'subject': subj,
                     'files': files, 'changed_lines': changed, 'n_tokens': n,
                     'is_submission': bool(SUBMISSION.match(subj)), 'messages': msgs})
    rows.sort(key=lambda r: r['time'])
    n = len(rows); n_test = round(0.12 * n); n_val = round(0.06 * n)
    splits = {'train': rows[:n - n_test - n_val], 'val': rows[n - n_test - n_val:n - n_test], 'test': rows[n - n_test:]}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for k, v in splits.items():
        with open(out / f'{k}.jsonl', 'w') as fh:
            for r in v: fh.write(json.dumps(r) + '\n')
    summary = {'kept': n, 'dropped': drop, 'splits': {k: len(v) for k, v in splits.items()},
               'boundaries': {k: [v[0]['time'], v[-1]['time']] for k, v in splits.items() if v},
               'tokens': {k: sum(r['n_tokens'] for r in v) for k, v in splits.items()}}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
