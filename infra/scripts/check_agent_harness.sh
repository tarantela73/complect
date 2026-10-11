#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
snapshot_manifest=${HARNESS_SNAPSHOT_MANIFEST:-.dev/harness/working-tree.snapshot.json}
python_image=$(docker image inspect --format '{{.Id}}' python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f)
node_image=$(docker image inspect --format '{{.Id}}' node@sha256:e21fc383b50d5347dc7a9f1cae45b8f4e2f0d39f7ade28e4eef7d2934522b752)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
paths="$tmp/paths"
manifest_file=""

if GIT_OPTIONAL_LOCKS=0 git -C "$root" rev-parse --show-toplevel >/dev/null 2>&1; then
  manifest_file=$snapshot_manifest
  case "$manifest_file" in /*) ;; *) manifest_file="$root/$manifest_file" ;; esac
  python3 -B "$root/infra/scripts/agent_harness.py" --root "$root" --verify-snapshot "$manifest_file"
  python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); sys.stdout.buffer.write(b"".join(p.encode()+b"\0" for p in m["files"]))' "$manifest_file" >"$paths"
else
  printf '%s\0' AGENTS.md Makefile feature_list.json progress.md session-handoff.md init.sh \
    docs/agents/harness.md docs/agents/harness-assessment.md docs/agents/capabilities.md \
    docs/agents/harness-skill-provenance.json docs/agents/harness-evidence \
    .agents/skills/harness-creator infra/scripts/tests/harness_skill.test.mjs \
    infra/scripts/agent_harness.py infra/scripts/tests/test_agent_harness.py \
    infra/scripts/check_agent_harness.sh >"$paths"
fi

archive="$tmp/source.tar"
tar -C "$root" --null --files-from="$paths" -cf "$archive"
mkdir -p "$tmp/manifest-root/.harness-snapshot"
cp -- "$manifest_file" "$tmp/manifest-root/.harness-snapshot/working-tree.snapshot.json" 2>/dev/null || true
if [ -f "$tmp/manifest-root/.harness-snapshot/working-tree.snapshot.json" ]; then
  tar -C "$tmp/manifest-root" -rf "$archive" .harness-snapshot/working-tree.snapshot.json
fi
# Re-read the source manifest after archive creation to reject a concurrent edit.
if [ -n "$manifest_file" ]; then
  python3 -B "$root/infra/scripts/agent_harness.py" --root "$root" --verify-snapshot "$manifest_file"
fi
docker run --rm -i --pull never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /work:rw,nosuid,nodev \
  --tmpfs /tmp:rw,exec,nosuid,nodev --workdir /work --entrypoint /bin/sh "$python_image" -ec \
  'tar --no-same-owner -xf -
python3 -B -c '\''import unittest
suite = unittest.defaultTestLoader.discover("infra/scripts/tests", pattern="test_agent_harness.py")
assert suite.countTestCases() > 0, "No mandatory Python tests"
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful() or bool(result.skipped))'\''
python3 -B infra/scripts/agent_harness.py' <"$archive"

node_archive="$tmp/node.tar"
tar -C "$root" -cf "$node_archive" .agents/skills/harness-creator infra/scripts/tests/harness_skill.test.mjs
docker run --rm -i --pull never --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /work:rw,nosuid,nodev \
  --tmpfs /tmp:rw,exec,nosuid,nodev --workdir /work --entrypoint /bin/sh "$node_image" -ec \
  'tar --no-same-owner -xf -
node --test --test-reporter=tap infra/scripts/tests/harness_skill.test.mjs >/tmp/node-tests.log 2>&1 || status=$?
cat /tmp/node-tests.log
status=${status:-0}
[ "$status" -eq 0 ] || exit "$status"
node -e '\''const t=require("fs").readFileSync("/tmp/node-tests.log","utf8"); if (!/^# Subtest: (?!.*\.mjs$).+$/m.test(t) || /^# tests 0$/m.test(t) || /^# (skipped|cancelled|fail) [1-9]/m.test(t)) process.exit(1);'\''' <"$node_archive"

printf '%s\n' 'HARNESS_GATE: PASS; ERP_ACCEPTANCE: NOT_RUN'
