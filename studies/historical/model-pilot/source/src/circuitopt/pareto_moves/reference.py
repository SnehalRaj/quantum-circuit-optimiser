"""Derive the single sealed public-reference folder for one audited candidate (from code/reference.py).

The export holds only the candidate's overlay folder (src/point_add) and the parent's NOTICE.
The runner, checkout, sealing and finalize scripts derive from reviewed scripts by counted
substitutions. Each `old` text must occur exactly the recorded number of times. derivation.json
records every substitution. The memory amendment changes only the abort guard of our reference
runner; the public harness has no memory guard. The step runs the preflight, the checkout and
the sealing. It never starts the reference run.
"""
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from .layout import MoveError, load, relative, require, save, sha

# Byte copy of the PREFLIGHT text in runs/frontier-levers-20261025/code/reference.py.
PREFLIGHT = '''"""Verify the export, the audit result and the operation bytes before one reference run."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
CAMPAIGN = ROOT.parent.parent
REPO = CAMPAIGN.parent.parent
CONFIG = json.loads((ROOT / 'preflight-config.json').read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def protected_git(path):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    def call(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-C', str(path), *args], env=env)
    return {'path': str(path), 'head': call('rev-parse', 'HEAD').decode().strip(),
            'status_sha256': hashlib.sha256(call('status', '--porcelain=v1', '--untracked-files=no')).hexdigest(),
            'tracked_diff_sha256': hashlib.sha256(call('diff', '--binary', 'HEAD')).hexdigest(),
            'scope': 'Read-only Git protection snapshot. Ignored generated artifacts are excluded.'}


def main():
    assert not (ROOT / 'preflight.json').exists()
    export = CAMPAIGN / CONFIG['export']
    manifest = json.loads((export / 'manifest.json').read_text())
    assert sha(export / 'manifest.json') == CONFIG['manifest_sha256']
    for name, expected in manifest['source_sha256'].items():
        assert sha(export / 'source' / name) == expected, name
    files = {str(p.relative_to(export / 'source')) for p in (export / 'source').rglob('*') if p.is_file()}
    assert files == set(manifest['source_sha256'])
    assert manifest['ops_sha256'] == CONFIG['expected_ops_sha256']
    slot = CAMPAIGN / CONFIG['audit_slot']
    protocol = json.loads((slot / 'protocol.json').read_text())
    assert sha(slot / 'protocol.json') == (slot / 'protocol.json.sha256').read_text().split()[0]
    assert (protocol['pairs'], protocol['streams'], protocol['epsilon'], protocol['family_size']) == (20480, 2, 0.001, 32)
    assert sha(slot / protocol['checker']['path']) == protocol['checker']['sha256']
    folder = slot / 'evaluations' / CONFIG['candidate'] / 'calibration'
    result = json.loads((folder / 'result.json').read_text())
    assert result['status'] == 'complete' and result['binding']['protocol_sha256'] == sha(slot / 'protocol.json')
    assert result['binding']['ops_sha256'] == CONFIG['expected_ops_sha256']
    assert result['bounds']['decision'] == 'meets_0.001_bound' and result['bounds']['family_upper95'] < 0.001
    for name, expected in result['file_sha256'].items():
        assert sha(folder / name) == expected, name
    member = next(m for m in protocol['members'] if m['id'] == CONFIG['candidate'])
    assert sha(slot / member['ops']) == CONFIG['expected_ops_sha256']
    notices = {p.name: sha(p) for p in (export / 'notices').iterdir() if p.is_file()}
    assert notices
    protection = {'original_challenge': protected_git(REPO.parent / 'ecdsa-fail/vendor/ecdsafail-challenge'),
                  'dirty_primary': protected_git(REPO.parent / 'reversible-circuit-optimizer')}
    with (ROOT / 'source-protection-before.json').open('x') as stream:
        stream.write(json.dumps(protection, indent=2) + '\\n')
    receipt = {'status': 'verified; no reference invocation started', 'verified_utc': datetime.now(timezone.utc).isoformat(),
               'candidate': CONFIG['candidate'], 'expected_ops_sha256': CONFIG['expected_ops_sha256'],
               'source_commit': manifest['source_commit'], 'source_archive_sha256': manifest['source_archive_sha256'],
               'source_manifest_sha256': CONFIG['manifest_sha256'], 'source_files_sha256': manifest['source_sha256'],
               'protocol_sha256': sha(slot / 'protocol.json'), 'checker_sha256': protocol['checker']['sha256'],
               'qualification_result_sha256': sha(folder / 'result.json'), 'qualification_files': result['file_sha256'],
               'qualification_Q': result['Q'], 'qualification_T': result['T'], 'qualification_counts': result['counts'],
               'qualification_bounds': result['bounds'], 'retained_notices_sha256': notices}
    with (ROOT / 'preflight.json').open('x') as stream:
        stream.write(json.dumps(receipt, indent=2) + '\\n')
    print(json.dumps({k: receipt[k] for k in ('status', 'candidate', 'expected_ops_sha256', 'qualification_Q', 'qualification_T')}))


if __name__ == '__main__':
    main()
'''

REVIEWED_ROOT = 'runs/final-frontier-20261024'
DEFAULT_MEMORY_GIB = 8
COPIED = ('reference-run.sh', 'reference-proposal.json', 'reference-source.diff')


@dataclass(frozen=True)
class Reviewed:
    """The reviewed final-frontier reference folders that the derivation reads."""
    joint: Path
    q1112: Path
    cof: Path
    harness_comparison: Path

    @classmethod
    def at(cls, repo, root=REVIEWED_ROOT):
        final = Path(repo) / root
        return cls(joint=final / 'official/joint-cap1113-catalog', q1112=final / 'official/q1112-catalog',
                   cof=final / 'official/cof-cap1096-catalog',
                   harness_comparison=final / 'submission-drafts/research/harness-comparison.json')


def substitutions(cid, ops, qubits, slot, manifest_sha, memory_gib=DEFAULT_MEMORY_GIB):
    """Return (reviewed folder field, file, target, [(old, new, count)]) for each derived script."""
    clean_line = "        runner=runner.replace(\"'clean_skycof_environment': True\",\"'clean_constructor_environment': True\")\n"
    memory_line = (f"        runner=runner.replace(\"'memory_limit_group_bytes': 8 * 1024**3\","
                   f"\"'memory_limit_group_bytes': {memory_gib} * 1024**3\")\n")
    return [
        ('joint', 'prepare_checkout.py', 'prepare_checkout.py',
         [("'exports/joint-d690-cap1113-catalog'", f"'exports/{cid}'", 1)]),
        ('q1112', 'prepare_sealed_requests.py', 'prepare_sealed_requests.py', [
            ('"""Seal two prepared reference requests without launching either reference."""',
             '"""Seal one prepared reference request without launching the reference."""', 1),
            (clean_line, clean_line + (memory_line if memory_gib != DEFAULT_MEMORY_GIB else ''), 1),
            ("CASES=[('q1112-catalog','q1112-catalog','b322ddc32187a916175c8e3c80dbe110f3108e7bf63d8f62da76589a4ad64a98',2),\n"
             "       ('joint-cap1113-catalog','joint-d690-cap1113-catalog','d9f7c568ceb906d08d676d1ce7bc7a743ab63f925b3f051af2c95c4ebd59be91',1)]",
             f"CASES=[('{cid}','{cid}','{ops}',1)]", 1),
            ("template=(CAMPAIGN/'official/q1097-catalog/attempt03/run_once.py').read_text()",
             "template=(CAMPAIGN.parent/'final-frontier-20261024/official/q1097-catalog/attempt03/run_once.py').read_text()", 1),
            ("'memory_limit_group_bytes':8*1024**3", f"'memory_limit_group_bytes':{memory_gib}*1024**3", 1)]),
        ('cof', 'finalize.py', 'finalize.py', [
            ("EXPECTED='fb6ef7c687cc719ca50302635638fe21c967d5222b276183aa4d99033777e3a3'", f"EXPECTED='{ops}'", 1),
            ("score['metrics']['qubits']==1096", f"score['metrics']['qubits']=={qubits}", 1),
            ("'wave-03/evaluations/cof-cap1096-catalog/calibration/result.json'",
             f"'{slot}/evaluations/{cid}/calibration/result.json'", 1),
            ("'e127bf6bd4b694ef2b9bea240a47b14a9298dadf8682842bce3e5879e1167036'", f"'{manifest_sha}'", 1)])]


def substitute(text, pairs, name='text'):
    """Apply counted substitutions in order. Refuse when any count differs."""
    for old, new, count in pairs:
        found = text.count(old)
        if found != count:
            raise MoveError(f'{name}: expected {count} occurrence(s) of {old!r}, found {found}')
        text = text.replace(old, new)
    return text


def derive(source, target, pairs, repo):
    """Write the derived script and return its receipt."""
    target.write_text(substitute(Path(source).read_text(), pairs, Path(source).name))
    return {'source': relative(source, repo), 'source_sha256': sha(source), 'target': target.name,
            'target_sha256': sha(target), 'substitutions': [{'old': o, 'new': n, 'count': c} for o, n, c in pairs]}


def derive_scripts(official, reviewed, repo, *, cid, ops, qubits, slot, manifest_sha, memory_gib=DEFAULT_MEMORY_GIB):
    """Derive the checkout, sealing and finalize scripts into `official`; return the receipts."""
    official = Path(official)
    return [derive(getattr(reviewed, field) / name, official / target, pairs, repo)
            for field, name, target, pairs in substitutions(cid, ops, qubits, slot, manifest_sha, memory_gib)]


def expected_runner(reviewed, cid, ops, memory_gib=DEFAULT_MEMORY_GIB):
    """Return the reviewed runner text with this candidate's hash, export and memory guard."""
    text = (reviewed.joint / 'run_once.py').read_text()
    text = text.replace("'d9f7c568ceb906d08d676d1ce7bc7a743ab63f925b3f051af2c95c4ebd59be91'", repr(ops))
    text = text.replace("'exports/joint-d690-cap1113-catalog'", f"'exports/{cid}'")
    # A recorded instrument amendment: the public harness has no memory guard; ours only aborts runs above it.
    return substitute(text, [("'memory_limit_group_bytes': 8 * 1024**3",
                              f"'memory_limit_group_bytes': {memory_gib} * 1024**3", 1)], 'run_once.py')


def default_python(campaign):
    venv = campaign.repo / '.venv/bin/python'
    return venv if venv.is_file() else Path(sys.executable)


def prepare(campaign, cid, source_dir, parent_commit, audit_slot, *, memory_gib=DEFAULT_MEMORY_GIB,
            reviewed=None, python=None, execute=subprocess.run):
    """Create exports/<cid> and official/<cid>, run the preflight, checkout and sealing, and check the runner."""
    root = campaign.root
    reviewed = reviewed or Reviewed.at(campaign.repo)
    python = python or default_python(campaign)
    slot = f'audits/slot-{int(audit_slot):02d}'
    result = load(root / slot / 'evaluations' / cid / 'calibration/result.json')
    ops = result['binding']['ops_sha256']
    export = root / 'exports' / cid
    official = root / 'official' / cid
    require(not export.exists() and not official.exists(), f'Preserve the existing reference folders of {cid}.')
    archive = campaign.source_archive(parent_commit)
    point = Path(source_dir) / campaign.overlay
    require(point.is_dir(), f'The candidate tree has no {campaign.overlay}: {source_dir}')
    shutil.copytree(point, export / 'source' / campaign.overlay)
    (export / 'notices').mkdir()
    with tarfile.open(archive) as tree:  # the parent's NOTICE; the harness snapshot omits it
        try:
            notice = tree.extractfile('NOTICE').read()
        except (KeyError, AttributeError):
            raise MoveError(f'The parent archive {archive.name} has no NOTICE') from None
    (export / 'notices/original-NOTICE').write_bytes(notice)
    source = {str(f.relative_to(export / 'source')): sha(f) for f in sorted((export / 'source').rglob('*')) if f.is_file()}
    save(export / 'manifest.json', {'id': cid, 'ops_sha256': ops, 'source_commit': archive.name.split('.')[0],
         'source_archive_sha256': sha(archive), 'source_sha256': source,
         'scope': f'Candidate {campaign.overlay} for the fixed harness {campaign.harness.name[:8]}.'})
    research = root / 'submission-drafts/research/harness-comparison.json'
    if not research.exists():
        research.parent.mkdir(parents=True)
        shutil.copy2(reviewed.harness_comparison, research)
    official.mkdir(parents=True)
    manifest_sha = sha(export / 'manifest.json')
    save(official / 'preflight-config.json', {'candidate': cid, 'export': f'exports/{cid}',
         'manifest_sha256': manifest_sha, 'expected_ops_sha256': ops, 'audit_slot': slot})
    (official / 'preflight.py').write_text(PREFLIGHT)
    receipts = derive_scripts(official, reviewed, campaign.repo, cid=cid, ops=ops, qubits=result['Q'], slot=slot,
                              manifest_sha=manifest_sha, memory_gib=memory_gib)
    for item in COPIED:
        require(sha(reviewed.joint / item) == sha(reviewed.q1112 / item), f'The reviewed copies of {item} differ.')
        shutil.copy2(reviewed.joint / item, official / item)
    save(official / 'derivation.json', {'candidate': cid, 'expected_ops_sha256': ops,
         'derivation_tool_sha256': sha(Path(__file__)), 'memory_limit_gib': memory_gib, 'files': receipts})
    for script, log in (('preflight.py', 'preflight'), ('prepare_checkout.py', 'checkout'),
                        ('prepare_sealed_requests.py', 'sealing')):
        with (official / f'{log}.stdout.txt').open('x') as out, (official / f'{log}.stderr.txt').open('x') as err:
            done = execute([str(python), str(official / script)], stdout=out, stderr=err)
        require(done.returncode == 0, f'{script} failed; see {log}.stderr.txt')
    require((official / 'run_once.py').read_text() == expected_runner(reviewed, cid, ops, memory_gib),
            'The sealed runner differs from the reviewed runner.')
    return {'candidate': cid, 'official': relative(official, campaign.repo),
            'sealed_request_sha256': (official / 'sealed-request.json.sha256').read_text().strip()}
