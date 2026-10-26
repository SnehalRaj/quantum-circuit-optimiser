"""Durable campaign claims. A claim precedes every public screen and audit."""
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import secrets
import sqlite3
import time


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def checked_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Expected a lowercase SHA-256 hash')
    return value


class RegistryConflict(RuntimeError):
    """A prior claim prevents another execution."""


class RegistryTampered(RuntimeError):
    """A saved record no longer matches its digest."""


def binomial_upper(failures, pairs, alpha):
    """Return the one-sided exact upper confidence limit."""
    if type(failures) is not int or type(pairs) is not int or not 0 <= failures <= pairs or pairs <= 0:
        raise ValueError('Invalid binomial counts')
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError('Invalid confidence allowance')
    if failures == pairs:
        return 1.0
    if failures == 0:
        return -math.expm1(math.log(alpha) / pairs)
    coefficients = [0.0]
    for k in range(1, failures + 1):
        coefficients.append(coefficients[-1] + math.log(pairs-k+1) - math.log(k))
    log_alpha = math.log(alpha)
    low, high = 0.0, 1.0
    for _ in range(80):
        p = (low + high) / 2
        log_p, log_q = math.log(p), math.log1p(-p)
        terms = [c + k*log_p + (pairs-k)*log_q for k, c in enumerate(coefficients)]
        maximum = max(terms)
        log_cdf = maximum + math.log(sum(math.exp(t-maximum) for t in terms))
        if log_cdf > log_alpha:
            low = p
        else:
            high = p
    return (low + high) / 2


class Registry:
    """Use one shared database for every worker in a campaign."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS screens (
                    operation_sha256 TEXT PRIMARY KEY,
                    token TEXT NOT NULL UNIQUE,
                    record TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS families (
                    family TEXT PRIMARY KEY,
                    record TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audits (
                    family TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL,
                    operation_sha256 TEXT NOT NULL,
                    record TEXT NOT NULL,
                    record_sha256 TEXT NOT NULL,
                    PRIMARY KEY (family, slot),
                    UNIQUE (family, job_id, operation_sha256)
                );
            ''')

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA busy_timeout=30000')
        con.execute('PRAGMA synchronous=FULL')
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as con:
            con.execute('BEGIN IMMEDIATE')
            try:
                yield con
                con.execute('COMMIT')
            except BaseException:
                con.execute('ROLLBACK')
                raise

    @staticmethod
    def _read(row):
        if row is None:
            return None
        value = json.loads(row['record'])
        if digest(value) != row['record_sha256']:
            raise RegistryTampered('The campaign registry record changed')
        return value

    @staticmethod
    def _encoded(value):
        return canonical(value).decode(), digest(value)

    def get_screen(self, operation_sha256):
        checked_hash(operation_sha256)
        with self._connection() as con:
            return self._read(con.execute('SELECT * FROM screens WHERE operation_sha256=?', (operation_sha256,)).fetchone())

    def claim_screen(self, operation_sha256, *, job_id, region, job_sha256):
        checked_hash(operation_sha256)
        checked_hash(job_sha256)
        if not all(isinstance(x, str) and x for x in (job_id, region)):
            raise ValueError('A screen needs a job and region')
        with self._transaction() as con:
            row = self._read(con.execute('SELECT * FROM screens WHERE operation_sha256=?', (operation_sha256,)).fetchone())
            if row:
                if row['status'] == 'complete':
                    return {**row, 'reused': True}
                raise RegistryConflict('These operation bytes already have an unfinished or interrupted screen claim')
            row = {'schema': 'lco-screen-claim-v1', 'operation_sha256': operation_sha256,
                   'job_id': job_id, 'region': region, 'job_sha256': job_sha256,
                   'token': secrets.token_hex(24), 'status': 'claimed', 'claimed_unix': time.time()}
            con.execute('INSERT INTO screens VALUES (?,?,?,?)',
                        (operation_sha256, row['token'], *self._encoded(row)))
            return {**row, 'reused': False}

    def complete_screen(self, token, result):
        with self._transaction() as con:
            row = self._read(con.execute('SELECT * FROM screens WHERE token=?', (token,)).fetchone())
            if row is None:
                raise RegistryConflict('Unknown screen claim')
            if result.get('operation_sha256') != row['operation_sha256']:
                raise ValueError('Screen result has different operation bytes')
            if row['status'] == 'complete':
                if digest(row['result']) != digest(result):
                    raise RegistryConflict('The completed screen result differs')
                return row
            if row['status'] != 'claimed':
                raise RegistryConflict('An interrupted screen needs explicit reconciliation')
            row.update(status='complete', result=result, result_sha256=digest(result), finished_unix=time.time())
            con.execute('UPDATE screens SET record=?,record_sha256=? WHERE token=?', (*self._encoded(row), token))
            return row

    def interrupt_screen(self, token, reason):
        with self._transaction() as con:
            row = self._read(con.execute('SELECT * FROM screens WHERE token=?', (token,)).fetchone())
            if row is None:
                raise RegistryConflict('Unknown screen claim')
            if row['status'] == 'claimed':
                row.update(status='interrupted', interruption=str(reason), interrupted_unix=time.time())
                con.execute('UPDATE screens SET record=?,record_sha256=? WHERE token=?', (*self._encoded(row), token))
            return row

    def disclosure(self, region):
        with self._connection() as con:
            rows = [self._read(r) for r in con.execute('SELECT * FROM screens ORDER BY operation_sha256')]
        return [r for r in rows if r['region'] == region]

    def reserve_audit(self, *, family, job_id, operation_sha256, job_sha256):
        checked_hash(operation_sha256)
        checked_hash(job_sha256)
        if not all(isinstance(x, str) and x for x in (family, job_id)):
            raise ValueError('An audit needs a family and job')
        contract = {'schema': 'lco-audit-family-v1', 'family': family, 'slots': 32,
                    'family_alpha': .05, 'alpha': .05/32, 'pairs': 20480, 'streams': 2, 'epsilon': .001}
        with self._transaction() as con:
            old_contract = self._read(con.execute('SELECT * FROM families WHERE family=?', (family,)).fetchone())
            if old_contract is None:
                con.execute('INSERT INTO families VALUES (?,?,?)', (family, *self._encoded(contract)))
            elif old_contract != contract:
                raise RegistryConflict('The frozen audit family differs')
            old = self._read(con.execute('SELECT * FROM audits WHERE family=? AND job_id=? AND operation_sha256=?',
                                        (family, job_id, operation_sha256)).fetchone())
            if old:
                if old['job_sha256'] != job_sha256:
                    raise RegistryConflict('The audit job changed')
                if old['status'] == 'complete':
                    return {**old, 'reused': True}
                raise RegistryConflict('This candidate already has an unfinished or interrupted audit')
            slot = con.execute('SELECT count(*) FROM audits WHERE family=?', (family,)).fetchone()[0]
            if slot >= 32:
                raise RegistryConflict('The 32-slot audit family is full')
            row = {**contract, 'slot': slot, 'job_id': job_id, 'operation_sha256': operation_sha256,
                   'job_sha256': job_sha256, 'token': secrets.token_hex(24), 'status': 'reserved',
                   'label': family + '/' + secrets.token_hex(32), 'reserved_unix': time.time()}
            con.execute('INSERT INTO audits VALUES (?,?,?,?,?,?,?)',
                        (family, slot, row['token'], job_id, operation_sha256, *self._encoded(row)))
            return {**row, 'reused': False}

    def complete_audit(self, token, result):
        with self._transaction() as con:
            row = self._read(con.execute('SELECT * FROM audits WHERE token=?', (token,)).fetchone())
            if row is None:
                raise RegistryConflict('Unknown audit reservation')
            for key in ['operation_sha256', 'pairs', 'streams', 'alpha', 'label']:
                if result.get(key) != row[key]:
                    raise ValueError('Audit result does not match its frozen ' + key)
            if row['status'] == 'complete':
                if digest(row['result']) != digest(result):
                    raise RegistryConflict('The completed audit result differs')
                return row
            if row['status'] != 'reserved':
                raise RegistryConflict('An interrupted audit needs explicit reconciliation')
            row.update(status='complete', result=result, result_sha256=digest(result), finished_unix=time.time())
            con.execute('UPDATE audits SET record=?,record_sha256=? WHERE token=?', (*self._encoded(row), token))
            return row

    def interrupt_audit(self, token, reason):
        with self._transaction() as con:
            row = self._read(con.execute('SELECT * FROM audits WHERE token=?', (token,)).fetchone())
            if row is None:
                raise RegistryConflict('Unknown audit reservation')
            if row['status'] == 'reserved':
                row.update(status='interrupted', interruption=str(reason), interrupted_unix=time.time())
                con.execute('UPDATE audits SET record=?,record_sha256=? WHERE token=?', (*self._encoded(row), token))
            return row
