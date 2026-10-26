#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$repo_dir"
checker=work/native-validation/frozen/bin/circuit-check
for policy in mfs mfsu mfsr mfsur; do
    "$checker" --width 16 --ops "work/native-validation/qualified-$policy.qops.zst" \
        --pairs 100000 --label lco-native-qualification-20261026-v1 \
        --out "work/native-validation/audit-$policy.json" --streams 2 --alpha 0.0125 --threads 1 \
        > "work/native-validation/audit-$policy.stdout" \
        2> "evidence/native-validation/audit-$policy.log"
    cp "work/native-validation/audit-$policy.json" "evidence/native-validation/audits/$policy.json"
done
