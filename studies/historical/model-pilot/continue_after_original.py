"""Keep the corrected arm and common audit sequential with the first matrix."""
from pathlib import Path
import subprocess
import sys
import time

here = Path(__file__).resolve().parent
expected = [here / 'runs' / condition / arm / 'final/manifest.json'
            for condition in ['guarded-seeds-v1', 'guarded-cleanup-only-v1']
            for arm in ['offline', 'claude']]
deadline = time.monotonic() + 7200
while not all(path.exists() for path in expected):
    if time.monotonic() > deadline:
        raise TimeoutError('The first matrix has not frozen. Preserve its partial records.')
    time.sleep(10)
for stage in ['run', 'finish']:
    result = subprocess.run([sys.executable, str(here / 'identifier_amendment.py'), stage], check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
