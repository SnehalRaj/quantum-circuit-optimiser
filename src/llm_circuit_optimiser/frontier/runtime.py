"""Execute counted frontier jobs with fixed tools and recorded outputs."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import subprocess
import time

from .. import large, runner
from ..common import ROOT, sha256
from .campaign import atomic_json, read_envelope, envelope, verify_artifacts
from .materialize import materialize
from .notes import dependencies_for_submission, render_note, validate_note_size
from .registry import binomial_upper

TERMINAL = {'accepted', 'promoted', 'rejected', 'failed', 'invalid', 'error', 'cancelled'}


def artifacts(*paths):
    return [{'path': str(Path(p).resolve()), 'sha256': sha256(p)} for p in paths]


def source_unchanged(receipt):
    source = Path(receipt['source_dir'])
    if large._hashes(source) != receipt['source_hashes']:
        raise ValueError('The fixed source tree changed')


def resolve_dependencies(dependencies, rows):
    """Resolve each recorded dependency against one saved public list."""
    identifiers = {row.get('id') for row in rows if isinstance(row.get('id'), str)}
    resolved, mapping = [], []
    for dependency in dependencies:
        original = dependency['submissionId']
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?', original):
            raise ValueError('A dependency needs a public UUID or eight-character prefix')
        matches = [value for value in identifiers if value == original or (len(original) == 8 and value.startswith(original+'-'))]
        if len(matches) != 1 or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', matches[0]):
            raise ValueError('The public list cannot uniquely resolve dependency ' + original)
        resolved.append({**dependency, 'submissionId': matches[0]})
        mapping.append({'recorded_id': original, 'submission_id': matches[0]})
    return resolved, mapping


def parse_emission(stderr):
    q = re.findall(r'peak qubits\s*[:=]?\s*(\d+)', stderr, re.I)
    counts = re.findall(r'POSTPASS[^\n]*final=\(\s*([\d,]+)\s*,\s*([\d,]+)\s*,\s*([\d,.]+)\s*\)', stderr)
    catalog = re.findall(r'research_catalog\s+rewrites=(\d+)(?:\s+native_toffolis_saved=(\d+))?', stderr)
    value = {'Q': int(q[-1]) if q else None, 'catalog_rewrites': int(catalog[-1][0]) if catalog else 0,
             'rewrite_counts': large._rewrite_counts(stderr)}
    value['catalog_native_toffolis_saved'] = int(catalog[-1][1]) if catalog and catalog[-1][1] else None
    if counts:
        values = [x.replace(',', '') for x in counts[-1]]
        value.update(operation_count=int(values[0]), static_toffoli=int(values[1]), static_expected_T=float(values[2]))
    else:
        value['static_expected_T'] = None
    value['pre_catalog_expected_T'] = value['static_expected_T'] if catalog else None
    value['post_catalog_expected_T'] = None
    value['catalog_unit_saving_heuristic_T'] = None
    if catalog and value['static_expected_T'] is not None and value['catalog_native_toffolis_saved'] is not None:
        value['catalog_unit_saving_heuristic_T'] = value['static_expected_T'] - value['catalog_native_toffolis_saved']
    value['count_scope'] = ('POSTPASS precedes the catalog. Native savings count gate records. '
                            'The unit-saving heuristic assumes each removed gate executes once; conditional gates can violate this assumption.'
                            if catalog else 'POSTPASS expected count; the public evaluator measures the executed mean.')
    return value


class RuntimeAdapter:
    """Runtime paths come from explicit configuration or standard tool discovery."""

    def __init__(self, config):
        self.config = json.loads(json.dumps(config, allow_nan=False))

    def _attempt(self, directory, stage):
        base = Path(directory)/'runtime'/stage
        base.mkdir(parents=True, exist_ok=True)
        for number in range(1, 1000000):
            path = base/f'{number:04d}'
            try:
                path.mkdir()
                return path
            except FileExistsError:
                continue
        raise RuntimeError('Too many preserved attempts')

    def _command(self, argv, cwd, directory, *, env=None, accepted=(0,)):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        record = {'argv': [str(x) for x in argv], 'cwd': str(cwd), 'started_unix': time.time()}
        atomic_json(directory/'started.json', record)
        with (directory/'stdout.txt').open('wb') as out, (directory/'stderr.txt').open('wb') as err:
            try:
                done = subprocess.run(record['argv'], cwd=cwd, env=env, stdout=out, stderr=err,
                                      timeout=self.config.get('command_timeout'))
                record.update(returncode=done.returncode, wall_seconds=time.monotonic()-start)
            except BaseException as error:
                record.update(error=str(error), error_type=type(error).__name__, wall_seconds=time.monotonic()-start)
                atomic_json(directory/'process.json', record)
                raise
        record['artifacts'] = artifacts(directory/'stdout.txt', directory/'stderr.txt')
        atomic_json(directory/'process.json', record)
        if done.returncode not in accepted:
            raise RuntimeError(f'Command failed with status {done.returncode}; see {directory}')
        return record

    def _freeze(self, job_dir):
        inputs = {}
        for key in ('cargo', 'inherited_contract_file', 'public_snapshot'):
            if self.config.get(key):
                inputs[key] = sha256(self.config[key])
        if self.config.get('checker'):
            checker = self.config['checker']
            inputs['checker'] = sha256(checker['path'])
            if inputs['checker'] != checker['sha256']:
                raise ValueError('The configured checker binary changed')
        paths = self.config.get('grammar_files', [])
        if not isinstance(paths, list):
            raise ValueError('grammar_files must be a list of paths')
        inputs['grammars'] = {str(Path(path).resolve()): sha256(path) for path in paths}
        inputs['benchmark_manifest'] = sha256(Path(self.config.get('benchmark_dir', ROOT/'vendor/benchmark'))/'manifest.json')
        frozen = {'config': self.config, 'implementation_sha256': self._implementation_hashes(), 'input_sha256': inputs}
        path = Path(job_dir)/'runtime-config.json'
        try:
            atomic_json(path, envelope(frozen))
        except FileExistsError:
            old = read_envelope(path)
            if {k: v for k, v in old.items() if k != 'receipt_sha256'} != frozen:
                raise ValueError('The runtime configuration changed after the job froze')

    def _implementation_hashes(self):
        package = Path(__file__).parent
        paths = list(package.glob('*.py'))
        paths += [package.parent/name for name in ('grammar.py', 'families.py', 'common.py', 'large.py', 'runner.py')]
        if not self.config.get('checker'):
            paths += [ROOT/'native/build.sh', ROOT/'native/Cargo.toml', ROOT/'native/Cargo.lock']
            for directory in (ROOT/'native/src', ROOT/'vendor/native_reference',
                              ROOT/'vendor/frontier_rewrite', ROOT/'vendor/grammar_passes'):
                paths += [path for path in directory.rglob('*') if path.is_file()
                          and (path.suffix == '.rs' or path.name in ('Cargo.toml', 'Cargo.lock', 'manifest.json'))]
        return {str(path.resolve()): sha256(path) for path in sorted(set(paths))}

    def preflight(self, job, job_dir):
        """Check configuration before the campaign returns cached stage results."""
        self._freeze(job_dir)

    def run_stage(self, stage, job, job_dir, receipts, context):
        self._freeze(job_dir)
        return getattr(self, '_' + stage)(job, Path(job_dir), receipts, context)

    def _materialize(self, job, directory, receipts, context):
        from ..grammar import compose
        from .bindings import bind_actions
        from .materialize import _directory_files, _git_files, _resolve, _root
        if bool(self.config.get('base_dir')) == bool(self.config.get('git_repo')):
            raise ValueError('Choose one base source: base_dir or git_repo')
        if self.config.get('base_dir'):
            base, _ = _directory_files(self.config['base_dir'], job.get('base_files_sha256'))
        else:
            repo = _root(self.config['git_repo'])
            base = _git_files(repo, _resolve(repo, job['base_commit']))
        grammar_paths = self.config.get('grammar_files', [])
        grammar = compose(grammar_paths) if grammar_paths else None
        binding = bind_actions(job, grammar, base_files=base)
        binding_path = directory/'grammar-binding.json'
        value = envelope(binding['receipt'])
        try:
            atomic_json(binding_path, value)
        except FileExistsError:
            if read_envelope(binding_path) != value:
                raise ValueError('The frozen grammar binding changed')
        keys = ('base_dir', 'git_repo', 'files_root', 'benchmark_dir')
        result = materialize(binding['job'], directory/'candidate', **{k: self.config[k] for k in keys if self.config.get(k)})
        result['grammar_binding'] = binding['receipt']
        manifest = Path(self.config.get('benchmark_dir', ROOT/'vendor/benchmark'))/'manifest.json'
        result['benchmark_commit'] = json.loads(manifest.read_text()).get('commit')
        result['artifacts'] = artifacts(binding_path, directory/'candidate/materialization.json',
                                       *[Path(result['source_dir'])/name for name in result['source_hashes']])
        return result

    def _build(self, job, directory, receipts, context):
        source = receipts['materialize']
        source_unchanged(source)
        attempt = self._attempt(directory, 'build')
        if self.config.get('cargo'):
            # Rustup selects the tool from the invoked executable name.
            cargo = str(Path(self.config['cargo']).absolute())
            env = {'PATH': str(Path(cargo).parent) + ':/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
            if os.environ.get('HOME'): env['HOME'] = os.environ['HOME']
            target = attempt/'target'
            env['CARGO_TARGET_DIR'] = str(target)
            rustc = Path(cargo).with_name('rustc')
            if rustc.is_file(): env['RUSTC'] = str(rustc)
        else:
            cargo, env, target, _ = large._build_environment(attempt)
        for key, name in [('cargo_home', 'CARGO_HOME'), ('rustup_home', 'RUSTUP_HOME')]:
            if self.config.get(key): env[name] = str(Path(self.config[key]).resolve())
        if self.config.get('target_dir'):
            target = Path(self.config['target_dir']).resolve()
            env['CARGO_TARGET_DIR'] = str(target)
        target.mkdir(parents=True, exist_ok=True)
        argv = [cargo, 'build', '--release', '--locked', '--jobs', str(self.config.get('build_jobs', 1)),
                '--bin', 'build_circuit', '--bin', 'eval_circuit']
        if self.config.get('offline', False):
            argv.append('--offline'); env['CARGO_NET_OFFLINE'] = 'true'
        rustc = env.get('RUSTC', shutil.which('rustc', path=env['PATH']))
        if not rustc:
            raise RuntimeError('The pinned Rust compiler is unavailable')
        version = self._command([rustc, '--version'], source['source_dir'], attempt/'toolchain', env=env)
        version_text = (attempt/'toolchain/stdout.txt').read_text().strip()
        expected = self.config.get('rust_version', '1.93.0')
        if not version_text.startswith('rustc ' + expected + ' '):
            raise ValueError('The Rust compiler differs from the pinned version ' + expected)
        with large._exclusive(target/'.lco-build.lock'):
            process = self._command(argv, source['source_dir'], attempt/'compile', env=env)
            binaries = attempt/'bin'; binaries.mkdir()
            for name in ['build_circuit', 'eval_circuit']:
                shutil.copy2(target/'release'/name, binaries/name)
        source_unchanged(source)
        emitter, evaluator = binaries/'build_circuit', binaries/'eval_circuit'
        return {'build_binary': str(emitter), 'build_binary_sha256': sha256(emitter),
                'eval_binary': str(evaluator), 'evaluator_sha256': sha256(evaluator),
                'eval_binary_sha256': sha256(evaluator), 'rust_version': version_text,
                'cargo_sha256': sha256(cargo), 'rustc_sha256': sha256(rustc),
                'artifacts': artifacts(emitter, evaluator, *[attempt/stage/name
                    for stage in ('compile', 'toolchain') for name in ('process.json', 'stdout.txt', 'stderr.txt')])}

    @staticmethod
    def _bound(receipts):
        source_unchanged(receipts['materialize'])
        built = receipts['build']
        for name, expected in [('build_binary', 'build_binary_sha256'), ('eval_binary', 'evaluator_sha256')]:
            if sha256(built[name]) != built[expected]:
                raise ValueError('A bound emitter or evaluator binary changed')
        if 'emit' in receipts and sha256(receipts['emit']['ops_path']) != receipts['emit']['operation_sha256']:
            raise ValueError('The operation bytes changed')

    def _emit(self, job, directory, receipts, context):
        self._bound(receipts)
        attempt = self._attempt(directory, 'emit')
        lock = nullcontext() if os.environ.get('LCO_PARALLEL_EMISSIONS') == '1' else large.emission_lock()
        with lock:
            process = self._command([receipts['build']['build_binary']], attempt, attempt/'process', env={'PATH': '/usr/bin:/bin'})
        ops = attempt/'ops.bin'
        if not ops.is_file() or not ops.stat().st_size:
            raise ValueError('The emitter did not write its operation stream')
        self._bound(receipts)
        result = parse_emission((attempt/'process/stderr.txt').read_text())
        if result['Q'] is not None and job.get('Q') is not None and result['Q'] != job['Q']:
            raise ValueError('The emitted qubit count differs from the job')
        operation = sha256(ops)
        expected = job.get('expected_ops_sha256') or job.get('metadata', {}).get('expected_ops_sha256')
        if expected and expected != operation:
            raise ValueError('The emitted operations differ from the frozen replay hash')
        return {**result, 'ops_path': str(ops), 'operation_sha256': operation, 'ops_sha256': operation,
                'artifacts': artifacts(ops, attempt/'process/process.json', attempt/'process/stdout.txt', attempt/'process/stderr.txt')}

    def _static_gate(self, job, directory, receipts, context):
        self._bound(receipts)
        expected = receipts['emit'].get('static_expected_T')
        must_beat = job.get('must_beat')
        if isinstance(must_beat, dict): must_beat = must_beat.get('T', must_beat.get('toffoli'))
        if expected is None:
            raise ValueError('The emitter has no static expected-T count for this job')
        emission = receipts['emit']
        deferred = emission.get('catalog_rewrites', 0) > 0 and emission.get('post_catalog_expected_T') is None
        return {'passed': deferred or must_beat is None or expected < must_beat, 'static_expected_T': expected,
                'must_beat': must_beat, 'deferred_to_measurement': deferred,
                'pre_catalog_expected_T': emission.get('pre_catalog_expected_T'),
                'catalog_native_toffolis_saved': emission.get('catalog_native_toffolis_saved'),
                'catalog_unit_saving_heuristic_T': emission.get('catalog_unit_saving_heuristic_T'),
                'scope': ('The catalog changed the stream after POSTPASS. Continue to measurement because its expected count is unknown.'
                          if deferred else 'Cost screen using the emitter estimate; this is not a certified lower bound.'),
                'artifacts': []}

    def _checker(self, directory):
        config = self.config.get('checker')
        if config:
            path = Path(config['path']).resolve()
            if sha256(path) != config['sha256']:
                raise ValueError('The configured checker binary changed')
            return {**config, 'path': str(path)}
        tools = runner.native_tools(directory/'native', offline=self.config.get('offline', False))
        return {'path': tools['circuit_check'], 'sha256': tools['binary_sha256']['circuit_check'], 'kind': 'native', 'threads': 1}

    def _check(self, ops, directory, *, pairs, label, alpha):
        checker = self._checker(directory)
        directory.mkdir(parents=True, exist_ok=True)
        out = directory/'audit.json'; per_input = directory/'outcomes.jsonl'
        before = sha256(ops)
        if checker.get('kind', 'native') == 'native':
            argv = [checker['path'], '--width', '256', '--ops', str(ops), '--pairs', str(pairs), '--label', label,
                    '--out', str(out), '--streams', '2', '--alpha', str(alpha), '--threads', '1']
        elif checker['kind'] == 'study':
            argv = [checker['path'], '--ops', str(ops), '--pairs', str(pairs), '--streams', '2', '--label', label,
                    '--threads', str(checker.get('threads', 4)), '--out', str(out), '--per-input', str(per_input),
                    '--progress', str(directory/'progress.json')]
        else:
            raise ValueError('Unknown checker protocol')
        process = self._command(argv, directory, directory/'process', env={'PATH': '/usr/bin:/bin'}, accepted=(0, 1))
        if sha256(checker['path']) != checker['sha256'] or sha256(ops) != before:
            raise ValueError('The fixed checker or operation bytes changed during evaluation')
        raw = json.loads(out.read_text()); counts = raw.get('counts', raw)
        if raw.get('pairs', pairs) != pairs or raw.get('streams', 2) != 2 or raw.get('label', label) != label:
            raise ValueError('The checker returned a different input protocol')
        if checker.get('kind', 'native') == 'native':
            if raw.get('field_width') != 256 or raw.get('alpha') != alpha:
                raise ValueError('The native checker returned a different width or confidence allowance')
        for key in ('ops_sha256', 'operation_sha256'):
            if key in raw and raw[key] != before:
                raise ValueError('The checker receipt names different operation bytes')
        failed = counts.get('failed_inputs')
        upper = binomial_upper(failed, pairs, alpha)
        rows = Path(raw.get('per_input', per_input))
        if not rows.is_absolute(): rows = directory/rows
        if not rows.is_file(): rows = out.with_suffix('.jsonl')
        if not rows.is_file(): raise ValueError('The checker did not retain per-input evidence')
        rows_hash = sha256(rows)
        if raw.get('per_input_sha256', rows_hash) != rows_hash:
            raise ValueError('The checker row hash differs from its saved output')
        result = {**raw, 'operation_sha256': before, 'ops_sha256': before, 'pairs': pairs, 'streams': 2,
                  'label': label, 'alpha': alpha, 'failed_inputs': failed, 'upper_bound': upper, 'qualified': upper <= .001,
                  'checker_sha256': checker['sha256'], 'per_input': str(rows), 'per_input_sha256': rows_hash,
                  'artifacts': artifacts(out, rows, directory/'process/process.json',
                                         directory/'process/stdout.txt', directory/'process/stderr.txt')}
        if 'packet_sha256' not in result and 'packet_sha256' in counts: result['packet_sha256'] = counts['packet_sha256']
        return result

    def _develop(self, job, directory, receipts, context):
        from .paired import compare_pair
        self._bound(receipts)
        attempt = self._attempt(directory, 'develop')
        parent = job.get('metadata', {}).get('parent_ops') or self.config.get('parent_ops')
        if parent:
            parent_path = Path(parent['path']).resolve()
            if sha256(parent_path) != parent['sha256']: raise ValueError('The paired parent operations changed')
        else:
            parent_job = {**job, 'id': job['id']+'-parent', 'edits': [], 'file_copies': [], 'Q': None}
            parent_job.pop('expected_ops_sha256', None)
            parent_job['metadata'] = {k: v for k, v in job.get('metadata', {}).items()
                                      if k not in ('expected_ops_sha256', 'actions', 'grammar_actions', 'frontier_actions', 'grammar_refs')}
            for key in ('actions', 'grammar_actions', 'frontier_actions'):
                parent_job.pop(key, None)
            parent_dir = attempt/'parent'; parent_dir.mkdir()
            material = self._materialize(parent_job, parent_dir, {}, context)
            built = self._build(parent_job, parent_dir, {'materialize': material}, context)
            emitted = self._emit(parent_job, parent_dir, {'materialize': material, 'build': built}, context)
            parent_path = Path(emitted['ops_path'])
        label = 'development/' + secrets.token_hex(32)
        atomic_json(attempt/'protocol.json', {'pairs': 8192, 'streams': 2, 'label': label, 'alpha': .05})
        parent_result = self._check(parent_path, attempt/'parent-check', pairs=8192, label=label, alpha=.05)
        child_result = self._check(receipts['emit']['ops_path'], attempt/'child-check', pairs=8192, label=label, alpha=.05)
        result = compare_pair(parent_result, child_result, parent_result['per_input'], child_result['per_input'],
                              pairs=8192, streams=2, output=attempt/'comparison.json')
        self._bound(receipts)
        return {**result, 'operation_sha256': receipts['emit']['operation_sha256'],
                'parent_operation_sha256': parent_result['operation_sha256'],
                'artifacts': parent_result['artifacts'] + child_result['artifacts'] + artifacts(attempt/'comparison.json', attempt/'protocol.json')}

    def _screen(self, job, directory, receipts, context):
        if not context.get('screen_claim'):
            raise ValueError('A public screen requires its campaign claim')
        self._bound(receipts)
        attempt = self._attempt(directory, 'screen')
        source = Path(receipts['materialize']['source_dir'])
        for name in ['score.json', 'results.tsv']:
            if (source/name).exists(): raise ValueError('The fixed source contains an unclaimed evaluator output')
        (attempt/'ops.bin').symlink_to(receipts['emit']['ops_path'])
        process = self._command([receipts['build']['eval_binary']], attempt, attempt/'process',
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C', 'RAYON_NUM_THREADS': '4'},
                               accepted=(0, 1))
        for name in ['score.json', 'results.tsv']:
            if (source/name).exists(): shutil.move(str(source/name), attempt/name)
        self._bound(receipts)
        output = (attempt/'process/stdout.txt').read_text() + (attempt/'process/stderr.txt').read_text()
        def count(label):
            matches = re.findall(re.escape(label) + r'\s*:\s*(\d+)', output)
            if not matches: raise ValueError('The fixed evaluator omitted ' + label)
            return int(matches[-1])
        shots = count('tested shots')
        counts = {'classical_mismatches': count('classical mismatches'),
                  'phase_garbage_batches': count('phase-garbage batches'),
                  'ancilla_garbage_batches': count('ancilla-garbage batches')}
        score_file = attempt/'score.json'
        score = json.loads(score_file.read_text()) if score_file.is_file() else None
        passed = process['returncode'] == 0 and shots == 9024 and not any(counts.values()) and score is not None
        if not any(counts.values()) and not passed:
            raise ValueError('The evaluator did not return a complete scored screen')
        metrics = score['metrics'] if score else {}
        if passed and metrics.get('qubits') != job['Q']:
            raise ValueError('The public qubit count differs from the job')
        return {'passed': passed, 'shots': shots, 'counts': counts, 'Q': metrics.get('qubits'), 'T': metrics.get('toffoli'),
                'score': score.get('score') if score else None, 'operation_sha256': receipts['emit']['operation_sha256'],
                'evaluator_sha256': receipts['build']['evaluator_sha256'],
                'artifacts': artifacts(*[p for p in [score_file, attempt/'results.tsv', attempt/'process/process.json',
                                                  attempt/'process/stdout.txt', attempt/'process/stderr.txt'] if p.is_file()])}

    def _audit(self, job, directory, receipts, context):
        from .paired import validate_verdicts
        self._bound(receipts)
        slot = context['audit_slot']
        attempt = self._attempt(directory, 'audit')
        result = self._check(receipts['emit']['ops_path'], attempt, pairs=slot['pairs'], label=slot['label'], alpha=slot['alpha'])
        rows = validate_verdicts(result, result['per_input'], pairs=slot['pairs'], streams=slot['streams'])
        atomic_json(attempt/'row-validation.json', rows)
        result['row_validation'] = rows
        result['artifacts'] += artifacts(attempt/'row-validation.json')
        self._bound(receipts)
        return result

    def _export(self, job, directory, receipts, context):
        self._bound(receipts)
        attempt = self._attempt(directory, 'export')
        source = Path(receipts['materialize']['source_dir'])
        target = attempt/'source'; shutil.copytree(source, target)
        metadata = job.get('metadata', {})
        inherited = metadata.get('inherited_contract', self.config.get('inherited_contract', ''))
        contract_file = self.config.get('inherited_contract_file')
        if contract_file: inherited = Path(contract_file).read_text()
        credits = metadata.get('credits') or self.config.get('credits')
        if not credits:
            raise ValueError('Export needs source-author credits')
        note = render_note(job, receipts, context['disclosure'], model=self.config.get('model', 'Model-free'),
                           harness=self.config.get('harness', 'llm-circuit-optimiser'), credits=credits, inherited_contract=inherited)
        validate_note_size(note)
        (attempt/'note.md').write_text(note)
        dependencies = dependencies_for_submission(job)
        resolution = []
        if self.config.get('public_snapshot'):
            public = json.loads(Path(self.config['public_snapshot']).read_text())
            rows = public.get('submissions') if isinstance(public, dict) else public
            if not isinstance(rows, list):
                raise ValueError('public_snapshot must contain the complete public submission list')
            dependencies, resolution = resolve_dependencies(dependencies, rows)
        atomic_json(attempt/'dependencies.json', dependencies)
        export = {'source_dir': str(target), 'source_hashes': large._hashes(target),
                  'note_path': str(attempt/'note.md'), 'dependencies_path': str(attempt/'dependencies.json'),
                  'operation_sha256': receipts['emit']['operation_sha256'], 'Q': receipts['screen']['Q'],
                  'T': receipts['screen']['T'], 'score': receipts['screen']['score'], 'model': self.config.get('model', 'Model-free'),
                  'harness': self.config.get('harness', 'llm-circuit-optimiser'), 'dependency_resolution': resolution}
        atomic_json(attempt/'export.json', export)
        return {**export, 'artifacts': artifacts(attempt/'note.md', attempt/'dependencies.json', attempt/'export.json',
                                               *[target/name for name in export['source_hashes']])}

    def _public_rows(self, directory):
        attempt = self._attempt(directory, 'public-sync')
        self._command([self.config.get('ecdsafail', 'ecdsafail'), 'submissions', '--all', '--json'], directory, attempt)
        value = json.loads((attempt/'stdout.txt').read_text())
        rows = value.get('submissions') if isinstance(value, dict) else value
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('The public list response has no submission rows')
        return rows, attempt

    def _send(self, job, directory, receipts, context):
        from .public import select_frontier, best_at_or_below
        self._bound(receipts)
        exported = receipts['export']; source_unchanged(exported)
        verify_artifacts(directory, exported)
        username = self.config.get('username')
        if not username: raise ValueError('Sending needs the account username for in-flight checks')
        timeout, poll = self.config.get('poll_timeout', 3600), self.config.get('poll_seconds', 30)
        deadline = time.monotonic() + timeout
        while True:
            rows, snapshot = self._public_rows(directory)
            mine = [r for r in rows if r.get('solverUsername') == username and r.get('status') not in TERMINAL]
            if not mine: break
            if time.monotonic() >= deadline:
                return {'submitted': False, 'refusal': {'reason': 'Earlier account submission remains in flight', 'retryable': True},
                        'artifacts': artifacts(snapshot/'stdout.txt')}
            time.sleep(min(poll, max(0, deadline-time.monotonic())))
        eligible, _, _ = select_frontier(rows)
        if any(r['officialMetrics']['qubits'] <= exported['Q'] and r['officialMetrics']['toffoli'] <= exported['T'] for r in eligible):
            return {'submitted': False, 'refusal': {'reason': 'The live curve dominates this candidate', 'retryable': False},
                    'artifacts': artifacts(snapshot/'stdout.txt')}
        benchmark = Path(self.config['benchmark_repo']).resolve()
        attempt = self._attempt(directory, 'send')
        checkout = attempt/'checkout'
        checkout_commit = self.config.get('benchmark_commit') or receipts['materialize'].get('benchmark_commit') or job['base_commit']
        if not isinstance(checkout_commit, str) or not re.fullmatch(r'[0-9a-f]{7,40}', checkout_commit):
            raise ValueError('The submission checkout needs a pinned benchmark commit')
        self._command(['git', '-C', str(benchmark), 'worktree', 'add', '--detach', str(checkout), checkout_commit],
                      directory, attempt/'worktree')
        shutil.rmtree(checkout/'src/point_add')
        shutil.copytree(Path(exported['source_dir'])/'src/point_add', checkout/'src/point_add')
        changed = subprocess.check_output(['git', '-C', str(checkout), 'diff', '--name-only'], text=True).splitlines()
        if any(not path.startswith('src/point_add/') for path in changed):
            raise ValueError('The sealed checkout changes files outside point_add')
        expected_tree = {name.removeprefix('src/point_add/'): value for name, value in exported['source_hashes'].items() if name.startswith('src/point_add/')}
        if large._hashes(checkout/'src/point_add') != expected_tree:
            raise ValueError('The sealed checkout differs from the exported circuit')
        for name, expected in exported['source_hashes'].items():
            if not name.startswith('src/point_add/'):
                path = checkout/name
                if not path.is_file() or path.is_symlink() or sha256(path) != expected:
                    raise ValueError('The sealed checkout differs from the fixed benchmark: ' + name)
        if sha256(receipts['emit']['ops_path']) != exported['operation_sha256']:
            raise ValueError('The screened operations changed before submission')
        rows, snapshot = self._public_rows(directory)
        if any(r.get('solverUsername') == username and r.get('status') not in TERMINAL for r in rows):
            return {'submitted': False, 'refusal': {'reason': 'An account submission started while preparing the checkout', 'retryable': True},
                    'artifacts': artifacts(snapshot/'stdout.txt')}
        eligible, _, _ = select_frontier(rows)
        if any(r['officialMetrics']['qubits'] <= exported['Q'] and r['officialMetrics']['toffoli'] <= exported['T'] for r in eligible):
            return {'submitted': False, 'refusal': {'reason': 'The live curve dominates this candidate', 'retryable': False},
                    'artifacts': artifacts(snapshot/'stdout.txt')}
        verify_artifacts(directory, exported)
        dependencies = json.loads(Path(exported['dependencies_path']).read_text())
        dependencies, resolution = resolve_dependencies(dependencies, rows)
        dependencies_path = attempt/'resolved-dependencies.json'
        atomic_json(dependencies_path, dependencies)
        atomic_json(attempt/'dependency-resolution.json', {'mapping': resolution, 'public_snapshot_sha256': sha256(snapshot/'stdout.txt')})
        argv = [self.config.get('ecdsafail', 'ecdsafail'), 'submit', '--dependencies-file', str(dependencies_path),
                '--note-file', exported['note_path'], '--model', exported['model'], '--harness', exported['harness'],
                '--claimed-score', str(exported['score']), '--json']
        process = self._command(argv, checkout, attempt/'submit', accepted=tuple(range(256)))
        stdout, stderr = (attempt/'submit/stdout.txt').read_text(), (attempt/'submit/stderr.txt').read_text()
        try: response = json.loads(stdout)
        except json.JSONDecodeError: response = None
        evidence = artifacts(attempt/'submit/process.json', attempt/'submit/stdout.txt', attempt/'submit/stderr.txt',
                             snapshot/'stdout.txt', dependencies_path, attempt/'dependency-resolution.json')
        if process['returncode']:
            try:
                refusal = json.loads(stderr).get('error', {})
            except (json.JSONDecodeError, AttributeError):
                refusal = {}
            if (not stdout.strip() and isinstance(refusal, dict) and refusal.get('code') == 'conflict'
                    and 'in flight' in str(refusal.get('message', '')).lower()):
                return {'submitted': False, 'refusal': {'reason': 'Service conflict: submission in flight', 'retryable': True}, 'artifacts': evidence}
            raise RuntimeError('Submission response is not a recognized refusal; reconcile it before another send')
        identifier = response.get('submissionId') or response.get('id') if isinstance(response, dict) else None
        if not identifier and isinstance(response, dict): identifier = response.get('submission', {}).get('id')
        if not identifier: raise RuntimeError('Submission completed without a recognized ID; reconcile the service result')
        return {'submitted': True, 'submission_id': identifier, 'response': response, 'artifacts': evidence}

    def _record(self, job, directory, receipts, context):
        from .public import select_frontier
        rows, snapshot = self._public_rows(directory)
        identifier = receipts['send']['submission_id']
        matches = [r for r in rows if r.get('id') == identifier]
        if len(matches) != 1:
            return {'terminal': False, 'submission_id': identifier, 'reason': 'The public list does not contain this submission yet',
                    'artifacts': artifacts(snapshot/'stdout.txt')}
        row = matches[0]
        _, _, frontier = select_frontier(rows)
        return {'terminal': row.get('status') in TERMINAL, 'submission_id': identifier, 'service_result': row,
                'on_frontier': any(r.get('id') == identifier for r in frontier), 'artifacts': artifacts(snapshot/'stdout.txt')}
