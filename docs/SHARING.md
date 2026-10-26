# Shareable source and evidence

This public snapshot has a fresh Git history. Audit new source and evidence before publication.
The source archive command makes no network request. It changes no remote repository.

Run the current-tree audit from the repository root:

```sh
python3 tools/share.py audit --output work/sharing-audit.json
```

The audit reads tracked files, compressed archive members, PDF text, and readable binary metadata.
It rejects symbolic links, unsafe archive paths, machine files, private path text, and recognized credential patterns.
PDF text and metadata inspection requires `pdftotext` and `pdfinfo`.
Zstandard inspection requires the `zstandard` Python package or the `zstd` command.
An unavailable reader fails the audit. Pattern checks cannot prove that every possible secret is absent.

Create a source archive outside the checkout:

```sh
python3 tools/share.py export --output ../quantum-circuit-optimiser-source.tar.gz
```

The exporter copies tracked working-tree bytes into a temporary directory, then audits those bytes.
It stops if any finding remains. It includes a `SHARE-MANIFEST.json` with file hashes.
It excludes Git history, ignored work, credentials, local environments, and build outputs.
Archive entries omit local owner names and timestamps.
Use `--include-untracked` only after reviewing every added file.

Inspect an extracted archive with `python3 tools/share.py audit --root PATH --tree`.
The archive contains the pinned source and data needed by the current library.
Optional live model calls still require the provider tools described in the main documentation.

## Redacted evidence

Some historical receipts and logs recorded private paths.
The shareable copies replace those paths with explicit placeholders.
`{repository}` refers to this source root. `{redacted-path-HASH}` retains a stable path identity without its text.
These placeholders describe historical locations. They are not executable paths.

[sharing-redactions.json](sharing-redactions.json) records each changed artifact and archive member.
It retains the original SHA-256 and the shareable copy's SHA-256.
Numeric results, operation hashes, and source hashes inside the evidence keep their original values.
The original files remain in private local storage outside the exported tree.
Executable vendor source and upstream notices retain their bytes.

The historical retention manifest hashes the shareable copies.
Changed entries also retain `original_sha256`.
Frozen experiment contracts and their hash sidecars continue to identify the original experiment bytes.
Use the redaction manifest when comparing those contracts with a shareable copy.
Redaction does not create a new circuit result or repeat a validation run.

## Git history

Cleaning a working tree does not remove older Git objects.
Check them separately:

```sh
python3 tools/share.py history --output work/sharing-history-audit.json
```

The source development repository has older objects with private path text.
This public snapshot starts from a fresh root commit. The source exporter omits all Git objects and history.
Do not share a Git bundle or clone until its separate history audit is clean.
This work performs no history rewrite.
