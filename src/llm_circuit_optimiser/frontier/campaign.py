"""Resume a campaign from frozen jobs and checked stage receipts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import time

from ..common import sha256
from .registry import Registry, RegistryConflict, RegistryTampered, binomial_upper, canonical, digest, checked_hash

STAGES = ('materialize', 'build', 'emit', 'static_gate', 'develop', 'screen', 'audit', 'export', 'send', 'record')
SIDE_EFFECT_STAGES = {'screen', 'audit', 'send'}


class JobBusy(RuntimeError):
    """Another process holds this job's execution lock."""


class InterruptedStage(RuntimeError):
    """The prior attempt has an unknown outcome and cannot repeat."""


def atomic_json(path, value, *, replace=False):
    """Commit a full JSON file, then sync its containing directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value) + b'\n'
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def envelope(value):
    return {**value, 'receipt_sha256': digest(value)}


def read_envelope(path):
    value = json.loads(Path(path).read_text())
    expected = value.pop('receipt_sha256', None)
    if expected != digest(value):
        raise RegistryTampered(f'Receipt changed: {path}')
    return {**value, 'receipt_sha256': expected}


def artifact_path(job_dir, path):
    path = Path(path)
    if not path.is_absolute():
        if '..' in path.parts:
            raise ValueError('Artifact path leaves the job directory')
        path = Path(job_dir) / path
    return path


def verify_artifacts(job_dir, result):
    if not isinstance(result.get('artifacts'), list):
        raise ValueError('Every stage result needs an artifacts list')
    for artifact in result['artifacts']:
        if not isinstance(artifact, dict) or set(artifact) != {'path', 'sha256'}:
            raise ValueError('Each artifact needs a path and SHA-256')
        checked_hash(artifact['sha256'])
        path = artifact_path(job_dir, artifact['path'])
        if path.is_symlink() or not path.is_file() or sha256(path) != artifact['sha256']:
            raise RegistryTampered(f'Artifact changed or missing: {path}')


def validate_result(stage, result, receipts, context, job_dir):
    if not isinstance(result, dict):
        raise ValueError('A stage must return a JSON object')
    canonical(result)
    verify_artifacts(job_dir, result)
    ops = receipts.get('emit', {}).get('operation_sha256')
    if stage == 'emit':
        checked_hash(result.get('operation_sha256'))
    if stage in ('static_gate', 'develop', 'screen'):
        if type(result.get('passed')) is not bool:
            raise ValueError(stage + ' needs a Boolean passed field')
    if stage in ('screen', 'audit'):
        if result.get('operation_sha256') != ops:
            raise ValueError(stage + ' checked different operation bytes')
    if stage == 'screen':
        evaluator = receipts.get('build', {}).get('evaluator_sha256')
        if evaluator is not None and result.get('evaluator_sha256') != evaluator:
            raise ValueError('The screen uses a different evaluator binary')
        if result.get('shots') != 9024:
            raise ValueError('A public screen must contain 9,024 shots')
        counts = result.get('counts', {})
        keys = ('classical_mismatches', 'phase_garbage_batches', 'ancilla_garbage_batches')
        if any(type(counts.get(k)) is not int or counts[k] < 0 for k in keys):
            raise ValueError('A public screen needs all mismatch and garbage counts')
        if result['passed'] != all(counts[k] == 0 for k in keys):
            raise ValueError('The screen verdict differs from its counts')
    if stage == 'audit':
        slot = context['audit_slot']
        for key in ('pairs', 'streams', 'label', 'alpha'):
            if result.get(key) != slot[key]:
                raise ValueError('The audit differs from its frozen ' + key)
        upper = binomial_upper(result.get('failed_inputs'), slot['pairs'], slot['alpha'])
        reported = result.get('upper_bound')
        if isinstance(reported, bool) or not isinstance(reported, (float, int)) or not math.isfinite(reported):
            raise ValueError('The audit needs a finite upper bound')
        if abs(upper-reported) > 1e-11:
            raise ValueError('The audit upper bound does not match its counts')
        if type(result.get('qualified')) is not bool or result['qualified'] != (upper <= .001):
            raise ValueError('The audit qualification differs from its bound')
    if stage == 'send':
        if type(result.get('submitted')) is not bool:
            raise ValueError('Send must distinguish a submitted result from a refusal')
        if result['submitted']:
            if not isinstance(result.get('submission_id'), str) or not result['submission_id']:
                raise ValueError('A submitted result needs the submission ID')
        else:
            refusal = result.get('refusal')
            if not isinstance(refusal, dict) or type(refusal.get('retryable')) is not bool or not refusal.get('reason'):
                raise ValueError('A known refusal needs a reason and retry class')
    if stage == 'record' and type(result.get('terminal')) is not bool:
        raise ValueError('A service record needs its terminal status')


class Campaign:
    """Call an injected adapter only after the prior receipts pass checks."""

    def __init__(self, root, adapter, *, owner, family_id='frontier-v1', registry_path=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not isinstance(owner, str) or not owner:
            raise ValueError('The campaign needs an owner name')
        self.adapter, self.owner, self.family_id = adapter, owner, family_id
        self.registry = Registry(registry_path or self.root/'registry.sqlite3')
        self.contract = {'schema': 'lco-campaign-v1', 'family_id': family_id,
                         'audit_pairs': 20480, 'streams': 2, 'epsilon': .001,
                         'audit_family_size': 32, 'alpha_per_slot': .05/32,
                         'public_shots': 9024, 'registry_path': str(self.registry.path)}
        path = self.root/'contract.json'
        try:
            atomic_json(path, envelope(self.contract))
        except FileExistsError:
            saved = read_envelope(path)
            if {k: v for k, v in saved.items() if k != 'receipt_sha256'} != self.contract:
                raise RegistryConflict('The campaign contract changed')

    @contextmanager
    def _job_lock(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory/'job.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise JobBusy('Another worker holds this job') from error
            lease = {'owner': self.owner, 'pid': os.getpid(), 'host': socket.gethostname(),
                     'token': secrets.token_hex(24), 'started_unix': time.time(), 'status': 'running'}
            atomic_json(directory/'lease.json', lease, replace=True)
            try:
                yield
            finally:
                lease.update(status='released', finished_unix=time.time())
                atomic_json(directory/'lease.json', lease, replace=True)
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def _send_lock(self):
        # All roots that share a registry also share the account send lock.
        path = self.registry.path.with_suffix('.send.lock')
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _job_directory(self, job):
        identifier = job.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', identifier):
            raise ValueError('The job ID is not a safe path component')
        if identifier in ('.', '..'):
            raise ValueError('Invalid job ID')
        return self.root/'jobs'/identifier

    def _status(self, directory, job, state, receipts, **extra):
        result = {'job_id': job['id'], 'owner': self.owner, 'state': state,
                  'completed_stages': list(receipts), **extra}
        atomic_json(directory/'status.json', result, replace=True)
        return result

    def _read_chain(self, directory, job_hash):
        receipts, previous = {}, job_hash
        missing = False
        for index, stage in enumerate(STAGES):
            path = directory/f'{index:02d}-{stage}.json'
            if not path.exists():
                missing = True
                continue
            if missing:
                raise RegistryTampered('A stage receipt has no predecessor')
            saved = read_envelope(path)
            if (saved.get('stage'), saved.get('job_sha256'), saved.get('previous_sha256')) != (stage, job_hash, previous):
                raise RegistryTampered('The stage receipt chain changed')
            validate_result(stage, saved['result'], receipts, saved['context'], directory)
            receipts[stage] = saved['result']
            previous = saved['receipt_sha256']
        return receipts, previous

    @staticmethod
    def _failed(stage, result):
        if stage in ('static_gate', 'develop', 'screen'):
            return not result['passed']
        if stage == 'audit':
            return not result['qualified']
        if stage == 'send':
            return not result['submitted']
        return result.get('status') in ('failed', 'rejected', 'invalid', 'blocked')

    def run_job(self, job, *, through='export', allow_send=False):
        if through not in STAGES:
            raise ValueError('Unknown final stage')
        if STAGES.index(through) >= STAGES.index('send') and not allow_send:
            raise ValueError('Sending needs allow_send=True')
        job = json.loads(canonical(job))
        directory = self._job_directory(job)
        with self._job_lock(directory):
            contract = read_envelope(self.root/'contract.json')
            if {k: v for k, v in contract.items() if k != 'receipt_sha256'} != self.contract:
                raise RegistryConflict('The campaign contract changed')
            job_hash = digest(job)
            frozen = directory/'job.json'
            try:
                atomic_json(frozen, envelope({'job': job, 'job_sha256': job_hash}))
            except FileExistsError:
                saved = read_envelope(frozen)
                if saved.get('job_sha256') != job_hash or saved.get('job') != job:
                    raise RegistryConflict('The frozen job differs from this request')
            preflight = getattr(self.adapter, 'preflight', None)
            if preflight:
                preflight(json.loads(canonical(job)), directory)
            if job.get('blocked_reasons') or job.get('setting_requests'):
                return self._status(directory, job, 'blocked', {}, reasons=job.get('blocked_reasons') or ['Unresolved setting requests'])
            receipts, previous = self._read_chain(directory, job_hash)
            for stage, result in receipts.items():
                if self._failed(stage, result):
                    return self._status(directory, job, 'rejected', receipts, stopped_at=stage)
            for index, stage in enumerate(STAGES[:STAGES.index(through)+1]):
                if stage in receipts:
                    continue
                context = {'campaign': self.contract, 'job_sha256': job_hash, 'owner': self.owner,
                           'region': job.get('region') or job.get('metadata', {}).get('region') or job['id']}
                attempts = directory/'attempts'/stage
                attempts.mkdir(parents=True, exist_ok=True)
                starts = sorted(attempts.glob('*.started.json'))
                last = read_envelope(starts[-1]) if starts else None
                if last:
                    if (last.get('stage'), last.get('job_sha256'), last.get('previous_sha256')) != (stage, job_hash, previous):
                        raise RegistryTampered('The interrupted attempt belongs to a different job chain')
                    finish = starts[-1].with_name(starts[-1].name.replace('.started.json', '.finished.json'))
                    if finish.exists():
                        saved = read_envelope(finish)
                        if (saved.get('stage'), saved.get('job_sha256'), saved.get('previous_sha256')) != (stage, job_hash, previous):
                            raise RegistryTampered('The completed attempt belongs to a different job chain')
                        validate_result(stage, saved['result'], receipts, saved['context'], directory)
                        retry = (stage == 'send' and not saved['result']['submitted'] and saved['result']['refusal']['retryable'])
                        retry = retry or (stage == 'record' and not saved['result']['terminal'])
                        if not retry:
                            atomic_json(directory/f'{index:02d}-{stage}.json', saved)
                            receipts[stage], previous = saved['result'], saved['receipt_sha256']
                            if self._failed(stage, saved['result']):
                                return self._status(directory, job, 'rejected', receipts, stopped_at=stage)
                            continue
                    if stage in SIDE_EFFECT_STAGES and not finish.exists():
                        # A completed registry result can close a crash after its durable commit.
                        if stage == 'screen':
                            cached = self.registry.get_screen(receipts['emit']['operation_sha256'])
                            if not cached or cached['status'] != 'complete':
                                raise InterruptedStage('Screen outcome is unknown; keep the prior claim')
                        elif stage == 'audit':
                            try:
                                self.registry.reserve_audit(family=self.family_id, job_id=job['id'],
                                    operation_sha256=receipts['emit']['operation_sha256'], job_sha256=job_hash)
                            except RegistryConflict as error:
                                raise InterruptedStage('Audit outcome is unknown; keep its reserved slot') from error
                        else:
                            raise InterruptedStage('Submission outcome is unknown; reconcile the service before continuing')
                if stage == 'screen':
                    context['screen_claim'] = self.registry.claim_screen(receipts['emit']['operation_sha256'],
                        job_id=job['id'], region=context['region'], job_sha256=job_hash)
                elif stage == 'audit':
                    context['audit_slot'] = self.registry.reserve_audit(family=self.family_id, job_id=job['id'],
                        operation_sha256=receipts['emit']['operation_sha256'], job_sha256=job_hash)
                context['disclosure'] = self.registry.disclosure(context['region'])
                attempt = len(starts) + 1
                started = attempts/f'{attempt:04d}.started.json'
                atomic_json(started, envelope({'stage': stage, 'job_sha256': job_hash, 'previous_sha256': previous,
                            'context': context, 'started_unix': time.time()}))
                self._status(directory, job, 'running', receipts, active_stage=stage)
                try:
                    claim = context.get('screen_claim') or context.get('audit_slot')
                    if claim and claim.get('reused'):
                        result = claim['result']
                    else:
                        def execute():
                            return self.adapter.run_stage(stage, json.loads(canonical(job)), directory,
                                json.loads(canonical(receipts)), json.loads(canonical(context)))
                        if stage == 'send':
                            with self._send_lock():
                                result = execute()
                        else:
                            result = execute()
                    result = json.loads(canonical(result))
                    for artifact in result.get('artifacts', []):
                        artifact['path'] = str(artifact_path(directory, artifact['path']).absolute())
                    validate_result(stage, result, receipts, context, directory)
                    if stage == 'screen':
                        self.registry.complete_screen(context['screen_claim']['token'], result)
                    elif stage == 'audit':
                        self.registry.complete_audit(context['audit_slot']['token'], result)
                    completed = envelope({'stage': stage, 'job_sha256': job_hash, 'previous_sha256': previous,
                                          'context': context, 'result': result, 'finished_unix': time.time()})
                    atomic_json(attempts/f'{attempt:04d}.finished.json', completed)
                    if stage == 'send' and not result['submitted'] and result['refusal']['retryable']:
                        return self._status(directory, job, 'waiting', receipts, stopped_at=stage, refusal=result['refusal'])
                    if stage == 'record' and not result['terminal']:
                        return self._status(directory, job, 'waiting', receipts, stopped_at=stage)
                    atomic_json(directory/f'{index:02d}-{stage}.json', completed)
                    receipts[stage], previous = result, completed['receipt_sha256']
                    if self._failed(stage, result):
                        return self._status(directory, job, 'rejected', receipts, stopped_at=stage)
                except BaseException as error:
                    atomic_json(attempts/f'{attempt:04d}.error.json', envelope({
                        'stage': stage, 'job_sha256': job_hash, 'error_type': type(error).__name__,
                        'error': str(error), 'finished_unix': time.time()}))
                    if stage == 'screen':
                        self.registry.interrupt_screen(context['screen_claim']['token'], error)
                    elif stage == 'audit':
                        self.registry.interrupt_audit(context['audit_slot']['token'], error)
                    self._status(directory, job, 'interrupted', receipts, stopped_at=stage, error=str(error))
                    raise
            return self._status(directory, job, 'complete' if through == 'record' else 'ready', receipts, through=through)

    def run_jobs(self, jobs, *, through='export', allow_send=False, parallel=1):
        if type(parallel) is not int or parallel < 1:
            raise ValueError('parallel must be a positive integer')
        selected = [job for job in jobs if job.get('owner') in (None, '', 'any', self.owner)]
        def execute(job):
            try:
                return self.run_job(job, through=through, allow_send=allow_send)
            except (JobBusy, InterruptedStage, RegistryConflict) as error:
                return {'job_id': job['id'], 'state': 'blocked', 'reason': str(error)}
            except Exception as error:
                return {'job_id': job['id'], 'state': 'interrupted', 'reason': str(error), 'error_type': type(error).__name__}
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            return list(pool.map(execute, selected))
