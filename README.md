# RIPPLE — isolated Claude P0 review snapshot

**This is a temporary review snapshot, not a production branch.** It has one
independent root commit and contains no inherited RIPPLE history. No merge,
prediction improvement, model evaluation or BullsAI call is part of publication.

The frozen V2.1.1 performance baseline is **f381d1c**
(`f381d1c7c6f55e504cee3f9e6beb425d2387ec0d`), with saved held-out file F1
0.19814814814814813 across 30 tasks and 90 V2.1.1 runs. The delivery implementation
is a **separate development line**, `baf2fc6fa5f34e092b773a2c87d5f3b76bfeb965`,
derived from `358dbff3f63b1ed3b1048d5c032f66702cee0f6f`. Its `full-report-v2`
label is distinct from the frozen `full-report-v2.1.1` checkpoint protocol.
This snapshot does not integrate those implementations.

## Review order

1. [P0 evidence report](docs/RIPPLE_P0_EVIDENCE_REPORT.md) and
   [regression map](docs/REGRESSION_MAP.md).
2. [Architecture review](docs/ARCHITECTURE_REVIEW.md),
   [delivery record](docs/RIPPLE_DELIVERY.md), and
   [prediction-quality review](docs/PREDICTION_QUALITY_REVIEW.md).
3. The actual controller, ledger, validator, source reads, provider boundary and
   verification code under [src/ripple/](src/ripple/), and the tests under
   [tests/](tests/).
4. [Source mapping and provenance](docs/evidence/p0/SOURCE_TO_SNAPSHOT.json),
   [snapshot verification](docs/evidence/p0/SNAPSHOT_VERIFICATION.json),
   [exact published inventory](PUBLISHED_FILES.txt), and [checksums](SHA256SUMS).

The architecture/delivery/quality documents are unchanged historical records.
Their original branch names, no-commit/no-push statements, broader inventories,
older timings and links describe earlier work, not the contents or publication
status of this isolated branch. This README and PUBLISHED_FILES.txt define the
current snapshot. Historical links to omitted artifacts may not resolve here.

## Included source and evidence

All Python modules under `src/ripple/`, all source-package tests and synthetic
fixtures under `tests/`, `pyproject.toml`, and `.gitignore` retain their original
paths and bytes. The three delivery evaluation utility scripts are included as
source under `evaluation/`; no benchmark data, archived reports or evaluation
outputs are present there. The original CI workflow is retained unchanged at
`docs/reference/delivery-ci.yml`, outside the active workflow directory, because
this review snapshot omits the full-suite dataset dependencies.

`docs/reference/delivery-vs-358dbff.patch` is the verified source/test/config diff.
`docs/evidence/p0/` holds sanitized test outcomes, comparisons, census and held-out
slot metadata. It includes all 90 saved V2.1.1 slots, the 25 zero-confirmation
and 12 confirmed-but-report-lost subsets, and the partial 89-attempt inventory.
Benchmark identifiers, commits, counts and diagnostic relationships are retained;
free-text provider errors, requests, private paths and raw responses are omitted.

## Verification results and limits

The original offline P0 suite recorded **219 passes**, including 27 security and
22 resilience cases. The adapted baseline comparison recorded 28 failures and
20 passes, with one new demo deselected; its test-only import adaptations are
documented in the regression map. Snapshot-specific checks are recorded in
`docs/evidence/p0/SNAPSHOT_VERIFICATION.json`. No live-provider accuracy or prompt
injection evaluation has been run for this delivery implementation.

Original October 5 V2.1.1 agent reports, JSONL traces and agent timestamp/UUID run
IDs were not recovered. Saved results cover all 180 final slots across both
systems; available individual checkpoints cover only 89 of 197 attempted runs,
corresponding to 78 final slots. Sanitized result metadata supports outcomes and
relationships, not complete causal replay. The old October 1–3 trace archive is
also excluded. The full FEA-Bench table, prepared benchmark manifests/labels,
repository checkouts and raw datasets are omitted. Global eligibility and
capacity for 80 fresh repository-disjoint tasks remain unknown.

This is not a complete benchmark reproduction environment. Tests depending on
the omitted `evaluation/data/` manifests cannot all run from this snapshot alone.
There is no virtual environment, wheelhouse, raw trace, source ZIP/tar, local
machine record, credential file, original Git history or inherited author identity.
Source/test files retain synthetic fixture identities and credential placeholders.

## Source-to-snapshot verification

The input ZIP SHA-256 is
`387ce0b397848009f8475f55ca3363b74466b93fe0d000f05636feed722aebd0`.
Its integrity, 85-member inventory and all 84 member checksums were verified.
The source tar's 62 files were checked against the readable ZIP copies, its
per-source hash manifest and the verified delivery Git objects. The three added
evaluation scripts were reconstructed from the verified patch and compared with
their delivery Git objects. Historical documentation was read from that commit.
SOURCE_TO_SNAPSHOT.json records every imported file, source and SHA-256, including
the CI reference relocation. Application and test code are byte-identical.

After cloning this branch, verify all published file contents:

```sh
shasum -a 256 -c SHA256SUMS
git rev-list --parents -n 1 HEAD
```

The first command checks every published file except the checksum file itself.
The second should show one SHA and no parent SHA. PUBLISHED_FILES.txt includes
itself and SHA256SUMS. The source ZIP and original implementation histories are
not uploaded with this branch.

With dependencies already available, the two delivery regression modules can be
run offline with the supplied network guard:

```sh
ripple_guard_dir=$(mktemp -d)
cp docs/reference/offline-network-guard.txt "$ripple_guard_dir/sitecustomize.py"
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PYTHONPATH="$ripple_guard_dir:$PWD/src" GIT_SSH_COMMAND=/usr/bin/false \
  python -m pytest -q -p no:cacheprovider \
  tests/test_delivery_security.py tests/test_delivery_resilience.py
```

The guard blocks socket connections and removes API credential environment
variables. Review the evaluation scripts as source; executing their runners can
write artifacts or contact providers. No dependency installation or inference
was performed while preparing this publication.
