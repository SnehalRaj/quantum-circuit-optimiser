"""Campaign paths, receipts and locks shared by the Pareto-move steps.

A campaign root holds code/, tools/, sources/, tracks/, audits/, exports/, official/ and publication/.
The defaults reproduce runs/frontier-levers-20261025/code/*.py. Every path is a parameter.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import fcntl
import hashlib
import json
import os
import tempfile

HARNESS_COMMIT = '6b0327d60e40068fcbed5fedcb7f66d926f12320'
HARNESS_RELATIVE = f'runs/frontier-reliability-20261024/snapshot/sources/{HARNESS_COMMIT}.tar.gz'
RUST = '1.93.0-aarch64-apple-darwin'
DEV_LABEL = 'pareto-20261024-development-v1'


class MoveError(ValueError):
    """A step refused to run because a recorded condition failed."""


def require(condition, message):
    if not condition:
        raise MoveError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    """Write JSON once. An existing receipt is never replaced."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')


def load(path):
    return json.loads(Path(path).read_text())


def relative(path, base):
    """Return `path` relative to `base` when possible, else the absolute path."""
    path = Path(path)
    try:
        return str(path.resolve().relative_to(Path(base).resolve()))
    except ValueError:
        return str(path)


class Lock:
    """An exclusive advisory file lock (fcntl.flock) held for one `with` block."""

    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a')
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def default_scratch():
    base = os.environ.get('PARETO_MOVES_SCRATCH')
    return Path(base) if base else Path(tempfile.gettempdir()) / 'pareto-moves'


@dataclass(frozen=True)
class Campaign:
    """Paths and fixed settings for one campaign. `Campaign.at(root)` fills the recorded defaults."""
    root: Path
    repo: Path
    harness: Path
    toolchain: Path
    build_scratch: Path
    screen_dir: Path
    checker: Path
    large_checker: Path
    rust: str = RUST
    track: str = 'froghop-levers'
    overlay: str = 'src/point_add'
    large_checker_from_ops: int = 50_000_000
    dev_label: str = DEV_LABEL
    dev_pairs: int = 8192
    streams: int = 2
    threads: int = 4
    build_jobs: int = 1
    screen_jobs: int = 2
    epsilon: float = 0.001
    time_wrapper: tuple = ('/usr/bin/time', '-l')
    child_path: str = '/usr/bin:/bin'

    @classmethod
    def at(cls, root, *, repo=None, harness=None, toolchain=None, scratch=None, build_scratch=None,
           screen_dir=None, checker=None, large_checker=None, **settings):
        def absolute(path):  # child processes run in other folders, so every path is absolute
            return Path(path).expanduser().resolve()
        root = absolute(root)
        repo = absolute(repo) if repo else root.parents[1]
        scratch = absolute(scratch) if scratch else absolute(default_scratch())
        if build_scratch is None:
            build_scratch = os.environ.get('LEVERS_SCRATCH') or scratch / 'levers-build'
        settings = {k: v for k, v in settings.items() if v is not None}
        if 'time_wrapper' in settings:
            settings['time_wrapper'] = tuple(settings['time_wrapper'])
        return cls(root=root, repo=repo,
                   harness=absolute(harness) if harness else repo / HARNESS_RELATIVE,
                   toolchain=absolute(toolchain) if toolchain else repo.parent / 'ecdsa-fail/.tools',
                   build_scratch=absolute(build_scratch),
                   screen_dir=absolute(screen_dir) if screen_dir else scratch / 'screen-harness',
                   checker=absolute(checker) if checker else root / 'tools/study-check.bin',
                   large_checker=absolute(large_checker) if large_checker else root / 'tools/study-check-400m.bin',
                   **settings)

    @property
    def name(self):
        return self.root.name

    @property
    def chain(self):
        return self.toolchain / 'rustup/toolchains' / self.rust / 'bin'

    @property
    def rust_version(self):
        return self.rust.split('-')[0]

    def candidate(self, cid, track=None):
        return self.root / 'tracks' / (track or self.track) / cid

    def work(self, cid):
        return self.build_scratch / cid

    def lock(self, name):
        return Lock(self.root / name)

    def cargo_env(self, target):
        return {'PATH': f'{self.chain}:{self.child_path}', 'CARGO_HOME': str(self.toolchain / 'cargo'),
                'RUSTUP_HOME': str(self.toolchain / 'rustup'), 'RUSTC': str(self.chain / 'rustc'),
                'CARGO_TARGET_DIR': str(target), 'CARGO_NET_OFFLINE': 'true'}

    def source_archive(self, commit):
        """Return the one archive in sources/ whose name starts with `commit`."""
        found = sorted(self.root.glob(f'sources/{commit}*.tar.gz'))
        require(found, f'No source archive starts with {commit} in {self.root / "sources"}')
        require(len(found) == 1, f'The prefix {commit} matches {len(found)} archives; give more digits')
        return found[0]
