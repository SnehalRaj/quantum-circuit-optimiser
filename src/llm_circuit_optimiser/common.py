"""Small shared helpers. Candidate code never enters the evaluator."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)


def load(path):
    return json.loads(Path(path).read_text())


def clean_env():
    return {'PATH': os.defpath}


def run(command, directory, receipt, *, env=None, timeout=None, accepted_returncodes=(0,)):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    receipt = Path(receipt)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    record = {'command': [str(x) for x in command], 'started_unix': time.time()}
    save(receipt, record)
    try:
        result = subprocess.run(record['command'], cwd=directory,
                                env=clean_env() if env is None else env,
                                capture_output=True, timeout=timeout, text=True)
        receipt.with_suffix('.stdout.txt').write_text(result.stdout)
        receipt.with_suffix('.stderr.txt').write_text(result.stderr)
        record.update(returncode=result.returncode)
        if result.returncode not in accepted_returncodes:
            raise RuntimeError(f'Command failed ({result.returncode}); see {receipt}')
        return result.stdout
    except (subprocess.TimeoutExpired, OSError, RuntimeError) as error:
        record['error'] = str(error)
        raise
    finally:
        record['wall_seconds'] = time.time() - record['started_unix']
        save(receipt, record)


def pareto(rows):
    """Retain all distinct Q/T trade-offs among admitted records."""
    admitted = [r for r in rows if r.get('qualified')]
    result = []
    for row in admitted:
        q, t = row['Q'], row['T']
        if any(other['Q'] <= q and other['T'] <= t and
               (other['Q'] < q or other['T'] < t) for other in admitted):
            continue
        if not any(r['Q'] == q and r['T'] == t for r in result):
            result.append(row)
    return sorted(result, key=lambda r: (r['Q'], r['T']))
