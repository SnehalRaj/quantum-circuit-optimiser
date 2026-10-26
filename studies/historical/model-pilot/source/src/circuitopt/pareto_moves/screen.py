"""Screen one emitted variant once with the trusted public evaluator (from code/screen.py, amendment 1).

`setup` builds eval_circuit once from the fixed harness archive in the screen folder.
`run` links the variant's ops.bin into that folder and runs eval_circuit with no arguments.
The evaluator draws its 9,024 shots from the operation hash, so the outcome equals the public
check for those bytes. Each variant gets one screen. A second screen is refused.
"""
from pathlib import Path
import json
import re
import subprocess
import tarfile
import time

from .layout import load, require, sha, utc

COUNTS = {'shots': 'tested shots', 'classical_mismatches': 'classical mismatches',
          'phase_garbage_batches': 'phase-garbage batches', 'ancilla_garbage_batches': 'ancilla-garbage batches'}
PUBLIC_SHOTS = 9024


def evaluator_binary(campaign):
    return campaign.screen_dir / 'target/release/eval_circuit'


def setup(campaign, execute=subprocess.run):
    """Build eval_circuit from the unchanged harness and record its hashes."""
    folder = campaign.screen_dir
    require(not folder.exists(), f'The screen folder already exists: {folder}')
    folder.mkdir(parents=True)
    with tarfile.open(campaign.harness) as harness:
        harness.extractall(folder, filter='data')
    chain = campaign.chain
    with campaign.lock('native-compiler.lock'):
        done = execute([str(chain / 'cargo'), 'build', '--release', '--locked', '--offline', '--jobs',
                        str(campaign.screen_jobs), '--bin', 'eval_circuit'],
                       cwd=folder, env=campaign.cargo_env(folder / 'target'), capture_output=True, text=True)
    require(done.returncode == 0, done.stderr[-2000:])
    record = {'built_utc': utc(), 'harness_archive_sha256': sha(campaign.harness),
              'eval_source_sha256': sha(folder / 'src/bin/eval_circuit.rs'),
              'eval_binary_sha256': sha(evaluator_binary(campaign)),
              'cargo_sha256': sha(chain / 'cargo'), 'rustc_sha256': sha(chain / 'rustc')}
    out = campaign.root / 'screening/evaluator.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + '\n')
    return record


def parse_counts(text):
    """Read the last value of each evaluator count; None when it is absent."""
    def number(label):
        found = re.findall(re.escape(label) + r'\s*:\s*(\d+)', text)
        return int(found[-1]) if found else None
    return {key: number(label) for key, label in COUNTS.items()}


def verdict(returncode, counts, score):
    return (returncode == 0 and counts['shots'] == PUBLIC_SHOTS and score is not None
            and all(counts[k] == 0 for k in counts if k != 'shots'))


def run(campaign, cid, track=None, execute=subprocess.run):
    """Screen the variant once. screen.json records the outcome, pass or fail."""
    home = campaign.candidate(cid, track)
    target = home / 'screen.json'
    require(not target.exists(), 'Each variant is screened once.')
    evaluator = load(campaign.root / 'screening/evaluator.json')
    binary = evaluator_binary(campaign)
    require(sha(binary) == evaluator['eval_binary_sha256'], 'The evaluator differs from its build record.')
    emission = load(home / 'emission.json')
    ops = Path(emission['ops_location'])
    require(sha(ops) == emission['ops_sha256'], 'The operation file differs from its emission receipt.')
    folder = campaign.screen_dir
    with campaign.lock('screening/screen.lock'):
        # A second process can pass the first check while this one runs; check again under the lock.
        require(not target.exists(), 'Each variant is screened once.')
        for name in ('ops.bin', 'score.json'):
            (folder / name).unlink(missing_ok=True)
        (folder / 'ops.bin').symlink_to(ops)
        started = time.monotonic()
        done = execute([str(binary)], cwd=folder, capture_output=True, text=True,
                       env={'PATH': campaign.child_path, 'RAYON_NUM_THREADS': str(campaign.threads),
                            'LANG': 'C', 'LC_ALL': 'C'})
        wall = time.monotonic() - started
        score = json.loads((folder / 'score.json').read_text()) if (folder / 'score.json').exists() else None
        (folder / 'ops.bin').unlink()
        counts = parse_counts(done.stdout + done.stderr)
        passed = verdict(done.returncode, counts, score)
        (home / 'screen.stdout.txt').write_text(done.stdout)
        (home / 'screen.stderr.txt').write_text(done.stderr)
        record = {'candidate': cid, 'screened_utc': utc(), 'ops_sha256': emission['ops_sha256'],
                  'eval_binary_sha256': evaluator['eval_binary_sha256'], 'returncode': done.returncode,
                  'wall_seconds': wall, 'counts': counts, 'score': score, 'passed': passed,
                  'scope': 'One trusted-evaluator screen of these exact bytes under amendment 1. It is not repeated.'}
        with target.open('x') as stream:
            stream.write(json.dumps(record, indent=2) + '\n')
    return {'candidate': cid, 'passed': passed, 'counts': counts,
            'score': score and {'Q': score['metrics']['qubits'], 'T': score['metrics']['toffoli'], 'S': score['score']}}
