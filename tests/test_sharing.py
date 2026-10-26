"""Exercise archive, history, redaction and export boundaries."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import shutil
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zlib

SPEC = importlib.util.spec_from_file_location('share', Path(__file__).resolve().parents[1]/'tools/share.py')
share = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(share)
PRIVATE_PATH = '/' + 'Users' + '/fixture/private-project/result.json'


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'repo'; self.root.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args])

    def tracked(self, name, content):
        path = self.root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        self.git('add', name)

    def test_nested_archive_and_gzip_contents_are_inspected(self):
        payload = gzip.compress(json.dumps({'path': PRIVATE_PATH}).encode())
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as archive:
            member = tarfile.TarInfo('record.json.gz'); member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        findings = share.inspect_blob('records.tar.gz', raw.getvalue())
        self.assertTrue(any(f['path'].endswith('record.json.gz!gunzip') and f['rule'] == 'private_path' for f in findings))

    def test_symlinks_and_archive_traversal_fail(self):
        outside = self.root.parent/'outside'; outside.write_text('fixture')
        (self.root/'linked').symlink_to(outside); self.git('add', 'linked')
        self.assertFalse(share.audit(self.root)['passed'])
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz') as archive:
            member = tarfile.TarInfo('../escape'); member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        self.assertEqual(share.inspect_blob('bad.tar.gz', raw.getvalue())[0]['rule'], 'unsafe_archive_entry')

    def test_redaction_preserves_numbers_hashes_and_valid_nested_json(self):
        record = {'path': PRIVATE_PATH, 'Q': 818, 'T': 9728780, 'ops_sha256': 'a'*64,
                  'response': json.dumps({'cwd': PRIVATE_PATH})}
        result = json.loads(share.redact_bytes(json.dumps(record).encode()))
        for key in ['Q', 'T', 'ops_sha256']:
            self.assertEqual(result[key], record[key])
        self.assertIn('redacted-path', json.loads(result['response'])['cwd'])
        self.assertFalse(share.inspect_blob('record.json', json.dumps(result).encode()))

    def test_secret_findings_never_echo_the_secret(self):
        token = 'gh' + 'p_' + 'x'*40
        findings = share.inspect_blob('record', token.encode())
        self.assertEqual(findings[0]['rule'], 'credential')
        self.assertNotIn(token, json.dumps(findings))

    def test_clean_export_omits_history_and_ignored_work(self):
        self.tracked('README.md', b'Fixture source\n')
        self.tracked('.gitignore', b'work/\n')
        (self.root/'work').mkdir(); (self.root/'work/private').write_text(PRIVATE_PATH)
        target = self.root.parent/'source.tar.gz'
        share.export_archive(self.root, target)
        with tarfile.open(target) as archive:
            names = archive.getnames()
            self.assertEqual(set(names), {'README.md', '.gitignore', 'SHARE-MANIFEST.json'})
            self.assertTrue(all(m.uid == 0 and m.gid == 0 and not m.uname and not m.gname for m in archive))

    def test_export_refuses_an_unclean_tree(self):
        self.tracked('receipt.json', json.dumps({'path': PRIVATE_PATH}).encode())
        target = self.root.parent/'source.tar.gz'
        with self.assertRaisesRegex(ValueError, 'failed its sharing audit'):
            share.export_archive(self.root, target)
        self.assertFalse(target.exists())

    def test_export_rejects_a_symlink_in_a_tracked_file_parent(self):
        self.tracked('data/record.json', b'{}\n')
        outside = self.root.parent/'outside'; outside.mkdir()
        (outside/'record.json').write_bytes(b'{}\n')
        shutil.rmtree(self.root/'data')
        (self.root/'data').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            share.export_archive(self.root, self.root.parent/'source.tar.gz')

    def test_failed_zstd_command_cannot_pass(self):
        class FailedDecoder:
            stdout = io.BytesIO(b'')
            def wait(self): return 1
        with patch.dict('sys.modules', {'zstandard': None}), patch.object(share.shutil, 'which', return_value='/tool'), \
             patch.object(share.subprocess, 'Popen', return_value=FailedDecoder()):
            findings = share.inspect_blob('bad.zst', b'invalid framing')
        self.assertTrue(any(f['rule'] == 'inspection_incomplete' for f in findings))

    def test_compressed_png_international_text_is_inspected(self):
        data = b'label\0\1\0\0\0' + zlib.compress(PRIVATE_PATH.encode())
        kind = b'iTXt'
        chunk = struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
        findings = share.inspect_blob('metadata.png', b'\x89PNG\r\n\x1a\n'+chunk)
        self.assertTrue(any(f['rule'] == 'private_path' and f['path'].endswith('!pngtext') for f in findings))

    def test_pdf_metadata_is_inspected_after_text(self):
        def output(argv, **kwargs):
            raw = PRIVATE_PATH.encode() if '-meta' in argv else b'ordinary document'
            return subprocess.CompletedProcess(argv, 0, stdout=raw, stderr=b'')
        with patch.object(share.shutil, 'which', return_value='/tool'), patch.object(share.subprocess, 'run', side_effect=output):
            findings = share.inspect_blob('document.pdf', b'%PDF-fixture')
        self.assertTrue(any(f['rule'] == 'private_path' and f['path'].endswith('!pdfmetadata') for f in findings))

    def test_clean_current_tree_does_not_hide_history(self):
        self.tracked('receipt.json', json.dumps({'path': PRIVATE_PATH}).encode())
        self.git('commit', '-qm', 'Fixture original')
        self.tracked('receipt.json', b'{}\n'); self.git('commit', '-qm', 'Fixture cleaned')
        self.assertTrue(share.audit(self.root)['passed'])
        result = share.audit_history(self.root)
        self.assertFalse(result['passed'])
        self.assertEqual(result['findings'][0]['rule'], 'private_path')


if __name__ == '__main__':
    unittest.main()
