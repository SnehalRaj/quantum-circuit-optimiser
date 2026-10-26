#!/usr/bin/env python3
"""Audit shareable bytes and create a source archive without repository history."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
import zlib

LIMIT = 256 * 1024 * 1024
PRIVATE = re.compile(rb'/(?:Us' rb'ers|home)/[^\s"\x27<>\x00\\]+|/private/'
                     rb'tmp/[^\s"\x27<>\x00\\]*|/var/' rb'folders/[^\s"\x27<>\x00\\]*|'
                     rb'/tmp/(?:claude|codex)[^\s"\x27<>\x00\\]*')
CONTEXT = re.compile(('nvi' + 'dia|inter' + 'views?(?: preparation)?').encode(), re.I)
SECRETS = re.compile(rb'(?<![A-Za-z0-9])(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|'
                     rb'gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|'
                     rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
ASSIGNMENT = re.compile(rb'(?i)["\x27](?:api[_-]?key|access[_-]?token|password|client[_-]?secret)'
                        rb'["\x27]\s*:\s*["\x27]([^"\x27\\\s]{8,})["\x27]')
BLOCKED = {'.git', '.env', '.venv', '__pycache__', '.DS_Store', '.pytest_cache',
           '.cache', 'target', 'work', 'build', 'dist'}


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and '..' not in path.parts and '\\' not in name


def checked_source(root, name):
    if not safe_name(name):
        raise ValueError('The source path is unsafe')
    cursor = Path(root)
    for part in PurePosixPath(name).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('The source path contains a symbolic link')
    if not cursor.is_file():
        raise ValueError('The source file is missing')
    return cursor


def selected_paths(root, *, include_untracked=False, tree=False):
    root = Path(root)
    if tree:
        return sorted(p.relative_to(root).as_posix() for p in root.rglob('*')
                      if p.is_file() or p.is_symlink())
    command = ['git', '-C', str(root), 'ls-files', '-z']
    if include_untracked:
        command += ['--cached', '--others', '--exclude-standard']
    return sorted(set(subprocess.check_output(command).decode().split('\0')) - {''})


def _bounded(stream):
    value = stream.read(LIMIT + 1)
    if len(value) > LIMIT:
        raise ValueError('Expanded member exceeds the inspection limit')
    return value


def _inflate(data):
    inflater = zlib.decompressobj()
    expanded = inflater.decompress(data, LIMIT + 1)
    if len(expanded) > LIMIT or not inflater.eof:
        raise ValueError('Compressed metadata is incomplete or exceeds the inspection limit')
    return expanded


def inspect_blob(name, data, *, depth=0):
    """Return findings without printing matched private text or secret values."""
    found = []
    for rule, pattern in [('private_path', PRIVATE), ('career_context', CONTEXT), ('credential', SECRETS)]:
        count = sum(1 for _ in pattern.finditer(data))
        if count:
            found.append({'path': name, 'rule': rule, 'count': count})
    assignments = [m for m in ASSIGNMENT.finditer(data)
                   if not re.search(rb'(?i)example|fixture|placeholder|redacted|dummy|your[_-]|\$\{|<', m[1])]
    if assignments:
        found.append({'path': name, 'rule': 'credential_assignment', 'count': len(assignments)})
    if depth > 5:
        return found + [{'path': name, 'rule': 'archive_depth', 'count': 1}]
    try:
        if name.endswith(('.tar.gz', '.tgz', '.tar')):
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
                for member in archive:
                    member_name = name + '!' + member.name
                    if not safe_name(member.name) or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                        found.append({'path': member_name, 'rule': 'unsafe_archive_entry', 'count': 1})
                    elif member.isfile():
                        if member.size > LIMIT:
                            raise ValueError('Expanded member exceeds the inspection limit')
                        found += inspect_blob(member_name, _bounded(archive.extractfile(member)), depth=depth+1)
        elif name.endswith('.gz'):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                found += inspect_blob(name + '!gunzip', _bounded(stream), depth=depth+1)
        elif name.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    member_name = name + '!' + member.filename
                    if not safe_name(member.filename) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                        found.append({'path': member_name, 'rule': 'unsafe_archive_entry', 'count': 1})
                    elif not member.is_dir():
                        with archive.open(member) as stream:
                            found += inspect_blob(member_name, _bounded(stream), depth=depth+1)
        elif name.endswith('.zst'):
            encoded, expected_size = data, None
            if data.startswith(b'QECCOPSZ'):
                if len(data) < 16:
                    raise ValueError('The operation header is incomplete')
                expected_size = int.from_bytes(data[8:16], 'little') * 56
                if expected_size > LIMIT:
                    raise ValueError('Expanded operations exceed the inspection limit')
                encoded = data[16:]
            try:
                import zstandard
                with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(encoded)) as stream:
                    expanded = _bounded(stream)
                validator = zstandard.ZstdDecompressor().decompressobj()
                validator.decompress(encoded)
                if not validator.eof or validator.unused_data:
                    raise ValueError('The Zstandard frame is incomplete or has trailing data')
            except ImportError:
                if not shutil.which('zstd'):
                    raise ValueError('Install zstandard or zstd to inspect this compressed file')
                with tempfile.TemporaryFile() as input_file:
                    input_file.write(encoded); input_file.seek(0)
                    process = subprocess.Popen(['zstd', '-dc'], stdin=input_file, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    try:
                        expanded = _bounded(process.stdout)
                    except BaseException:
                        process.stdout.close()
                        if process.poll() is None:
                            process.terminate()
                        process.wait()
                        raise
                    process.stdout.close()
                    if process.wait() != 0:
                        raise ValueError('Zstandard decoding failed')
            if expected_size is not None and len(expanded) != expected_size:
                raise ValueError('The operation count differs from its compressed payload')
            found += inspect_blob(name + '!unzstd', expanded, depth=depth+1)
        elif data.startswith(b'\x89PNG\r\n\x1a\n'):
            offset = 8
            while offset + 12 <= len(data):
                size = int.from_bytes(data[offset:offset+4], 'big')
                kind, chunk = data[offset+4:offset+8], data[offset+8:offset+8+size]
                if len(chunk) != size:
                    raise ValueError('PNG metadata is incomplete')
                if kind == b'zTXt':
                    _, encoded = chunk.split(b'\0', 1)
                    if not encoded or encoded[0] != 0:
                        raise ValueError('Unknown PNG text compression')
                    decoded = _inflate(encoded[1:])
                    found += inspect_blob(name+'!pngtext', decoded, depth=depth+1)
                elif kind == b'iTXt':
                    _, encoded = chunk.split(b'\0', 1)
                    if len(encoded) < 2 or encoded[0] not in (0, 1) or encoded[1] != 0:
                        raise ValueError('Unknown PNG international text compression')
                    fields = encoded[2:].split(b'\0', 2)
                    if len(fields) != 3:
                        raise ValueError('PNG international text is incomplete')
                    decoded = _inflate(fields[2]) if encoded[0] else fields[2]
                    found += inspect_blob(name+'!pngtext', decoded, depth=depth+1)
                offset += size + 12
        elif data.startswith(b'%PDF-'):
            if not all(shutil.which(name) for name in ('pdftotext', 'pdfinfo')):
                raise ValueError('Install pdftotext and pdfinfo to inspect this PDF')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'document.pdf'
                path.write_bytes(data)
                result = subprocess.run(['pdftotext', str(path), '-'], capture_output=True, timeout=60)
                if result.returncode:
                    raise ValueError('PDF text extraction failed')
                found += inspect_blob(name + '!pdftext', result.stdout, depth=depth+1)
                for flag, label in [([], '!pdfinfo'), (['-meta'], '!pdfmetadata')]:
                    result = subprocess.run(['pdfinfo', *flag, str(path)], capture_output=True, timeout=60)
                    if result.returncode:
                        raise ValueError('PDF metadata extraction failed')
                    found += inspect_blob(name + label, result.stdout, depth=depth+1)
        else:
            # UTF-16 metadata can carry paths that the raw byte pass cannot see.
            if b'\x00' in data[:4096]:
                for codec in ('utf-16-le', 'utf-16-be'):
                    decoded = data.decode(codec, errors='ignore').encode()
                    for rule, pattern in [('private_path', PRIVATE), ('career_context', CONTEXT), ('credential', SECRETS)]:
                        if pattern.search(decoded):
                            found.append({'path': name, 'rule': rule + '_utf16', 'count': 1})
    except (OSError, ValueError, zlib.error, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        found.append({'path': name, 'rule': 'inspection_incomplete', 'reason': str(error), 'count': 1})
    return found


def audit(root, *, include_untracked=False, tree=False):
    root = Path(root).resolve()
    paths = selected_paths(root, include_untracked=include_untracked, tree=tree)
    findings = []
    for name in paths:
        path = root/name
        if any(part in BLOCKED or (part.startswith('.env.') and part != '.env.example') for part in Path(name).parts):
            findings.append({'path': name, 'rule': 'machine_artifact', 'count': 1})
        try:
            path = checked_source(root, name)
        except ValueError:
            findings.append({'path': name, 'rule': 'symlink_or_unsafe_path', 'count': 1})
            continue
        findings += inspect_blob(name, path.read_bytes())
    return {'schema': 'sharing-audit-v1', 'files': len(paths), 'passed': not findings, 'findings': findings,
            'scope': 'Tracked working-tree bytes, archive members, PDF text, and readable binary metadata. Pattern checks do not prove that every possible secret is absent.'}


def redact_bytes(data):
    """Replace private path text; preserve numeric results and existing hash strings."""
    data.decode('utf-8')  # Binary artifacts need a separate format-aware decision.
    def replacement(match):
        path = match.group()
        marker = b'/llm-circuit-optimiser'
        if marker in path:
            return b'{repository}' + path.split(marker, 1)[1]
        return b'{redacted-path-' + checksum(path)[:12].encode() + b'}'
    result = PRIVATE.sub(replacement, data)
    return CONTEXT.sub(b'external-project', result)


def audit_history(root):
    """Inspect every reachable Git blob without changing any historical object."""
    root = Path(root).resolve()
    objects = subprocess.check_output(['git', '-C', str(root), 'rev-list', '--objects', '--all']).decode().splitlines()
    names = dict(line.split(' ', 1) if ' ' in line else (line, '') for line in objects)
    metadata = subprocess.check_output(['git', '-C', str(root), 'cat-file', '--batch-check'],
                                       input=('\n'.join(names)+'\n').encode()).decode().splitlines()
    selected = [(line.split()[0], int(line.split()[2])) for line in metadata if line.split()[1] == 'blob']
    findings = []
    with tempfile.TemporaryFile() as requests:
        requests.write(('\n'.join(sha for sha, _ in selected)+'\n').encode()); requests.seek(0)
        process = subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'], stdin=requests,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for expected, size in selected:
            header = process.stdout.readline().decode().strip().split()
            if header != [expected, 'blob', str(size)]:
                raise ValueError('Git returned an unexpected historical object')
            data = process.stdout.read(size)
            if len(data) != size or process.stdout.read(1) != b'\n':
                raise ValueError('Git returned an incomplete historical object')
            findings += inspect_blob(names[expected], data)
            for record in findings:
                record.setdefault('object', expected)
        process.stdout.close()
        if process.wait():
            raise ValueError('Git history inspection failed')
        process.stderr.close()
    messages = subprocess.check_output(['git', '-C', str(root), 'log', '--all', '--format=%H%x00%B%x00'])
    fields = messages.split(b'\0')
    for index in range(0, len(fields)-1, 2):
        findings += inspect_blob('commit-message:' + fields[index].strip().decode(), fields[index+1])
    return {'schema': 'sharing-history-audit-v1', 'blobs': len(selected), 'passed': not findings, 'findings': findings,
            'scope': 'Reachable Git objects only. No history rewrite was performed.'}


def export_archive(root, destination, *, include_untracked=False):
    """Copy only audited source files. Never copy Git history or ignored work."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.exists():
        raise ValueError('The export destination already exists')
    paths = selected_paths(root, include_untracked=include_untracked)
    if destination.is_relative_to(root):
        raise ValueError('Write the shareable archive outside the source checkout')
    with tempfile.TemporaryDirectory(prefix='source-export-') as directory:
        staged = Path(directory)/'source'; staged.mkdir()
        for name in paths:
            source = checked_source(root, name)
            target = staged/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
        report = audit(staged, tree=True)
        if not report['passed']:
            raise ValueError('The staged export failed its sharing audit: ' + json.dumps(report['findings']))
        hashes = {p: checksum((staged/p).read_bytes()) for p in paths}
        (staged/'SHARE-MANIFEST.json').write_text(json.dumps({'schema': 'share-manifest-v1', 'files_sha256': hashes,
            'scope': 'Current source bytes only. Git history and local work are excluded.'}, indent=2, sort_keys=True)+'\n')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for path in sorted(p for p in staged.rglob('*') if p.is_file()):
                    entry = tarfile.TarInfo(path.relative_to(staged).as_posix())
                    entry.size = path.stat().st_size; entry.mode = path.stat().st_mode & 0o777
                    with path.open('rb') as content:
                        archive.addfile(entry, content)
    return {'path': str(destination), 'sha256': checksum(destination.read_bytes()), 'files': len(paths) + 1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'history', 'export'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--include-untracked', action='store_true')
    parser.add_argument('--tree', action='store_true', help='Inspect an extracted tree without Git')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'export':
        if args.output is None:
            parser.error('export requires --output')
        result = export_archive(args.root, args.output, include_untracked=args.include_untracked)
    else:
        result = (audit_history(args.root) if args.command == 'history'
                  else audit(args.root, include_untracked=args.include_untracked, tree=args.tree))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get('passed', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
