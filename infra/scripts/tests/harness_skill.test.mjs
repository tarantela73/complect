import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm, mkdir } from 'node:fs/promises';
import { execFileSync, spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verificationCommands, initScriptFromCommands, validateFeatureState, scoreHarness } from '../../../.agents/skills/harness-creator/scripts/lib/harness-utils.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const scripts = path.join(root, '.agents/skills/harness-creator/scripts');
async function temporary(fn) {
  const dir = await mkdtemp(path.join(os.tmpdir(), 'harness-test-'));
  try { return await fn(dir); } finally { await rm(dir, { recursive: true, force: true }); }
}
const feature = (id = 'feat-001', status = 'not-started', dependencies = []) => ({id, name: 'Feature', description: 'Concrete behavior', status, dependencies, evidence: status === 'done' ? 'test command, exit 0, log path' : ''});
const validate = features => validateFeatureState(JSON.stringify({features}));
test('empty repository has zero structural score', () => assert.equal(scoreHarness([]).overall, 0));
test('generic scaffold fails without verification', () => temporary(async dir => {
  execFileSync('node', [path.join(scripts, 'create-harness.mjs'), '--target', dir]);
  assert.notEqual(spawnSync('bash', [path.join(dir, 'init.sh')]).status, 0);
}));
test('Python gate propagates pytest exit 5', () => temporary(async dir => {
  const command = verificationCommands({stack: 'python'})[0];
  assert.equal(spawnSync('bash', ['-c', `python3() { return 5; }; ${command}`]).status, 5);
}));
test('no implicit dependency installation or install-only success', () => {
  const commands = verificationCommands({stack:'node', packageJson:{scripts:{test:'node --test'}}, packageManager:'npm'});
  assert.deepEqual(commands, ['npm test']);
  assert.match(verificationCommands({stack:'node', packageJson:{scripts:{}}, packageManager:'npm'})[0], /exit 1/);
});
test('generated script quotes command labels without executing substitution twice', () => temporary(async dir => {
  await writeFile(path.join(dir, 'init.sh'), initScriptFromCommands(["printf x >> 'marker'; echo $(printf y >> marker)"]));
  assert.equal(spawnSync('bash', [path.join(dir, 'init.sh')], {cwd:os.tmpdir()}).status, 0);
  assert.equal(await readFile(path.join(dir, 'marker'), 'utf8'), 'xy');
}));
test('failed pipeline stops verification', () => temporary(async dir => {
  await writeFile(path.join(dir, 'init.sh'), initScriptFromCommands(['false | cat', 'touch after']));
  assert.notEqual(spawnSync('bash', [path.join(dir, 'init.sh')]).status, 0);
  await assert.rejects(readFile(path.join(dir, 'after')));
}));
test('generator preserves existing files', () => temporary(async dir => {
  await writeFile(path.join(dir, 'AGENTS.md'), 'keep');
  execFileSync('node', [path.join(scripts, 'create-harness.mjs'), '--target', dir]);
  assert.equal(await readFile(path.join(dir, 'AGENTS.md'), 'utf8'), 'keep');
}));
test('generator rejects traversal in agent-file', () => temporary(async dir => {
  const result = spawnSync('node', [path.join(scripts, 'create-harness.mjs'), '--target', path.join(dir, 'target'), '--agent-file', '../outside.md']);
  assert.notEqual(result.status, 0);
  await assert.rejects(readFile(path.join(dir, 'outside.md')));
}));
test('valid dependency chain passes', () => assert.deepEqual(validate([feature('feat-001', 'done'), feature('feat-002', 'in-progress', ['feat-001'])]), []));
test('invalid and duplicate IDs fail', () => {
  assert.ok(validate([feature('invalid')]).length);
  assert.ok(validate([feature(), feature()]).length);
});
test('done requires evidence', () => assert.ok(validate([{...feature('feat-001','done'), evidence:''}]).length));
test('invalid status and empty state fail', () => {
  assert.ok(validate([feature('feat-001','passing')]).length);
  assert.ok(validate([]).length);
});
test('unknown and incomplete dependencies fail', () => {
  assert.ok(validate([feature('feat-001','in-progress',['feat-002'])]).length);
  assert.ok(validate([feature(),feature('feat-002','done',['feat-001'])]).length);
});
test('cycles and multiple active features fail', () => {
  assert.ok(validate([feature('feat-001','not-started',['feat-002']),feature('feat-002','not-started',['feat-001'])]).length);
  assert.ok(validate([feature('feat-001','in-progress'),feature('feat-002','in-progress')]).length);
});
test('CLI rejects done without evidence even at low score threshold', () => temporary(async dir => {
  execFileSync('node',[path.join(scripts,'create-harness.mjs'),'--target',dir]);
  await writeFile(path.join(dir,'feature_list.json'),JSON.stringify({features:[{...feature('feat-001','done'),evidence:''}]}));
  assert.notEqual(spawnSync('node',[path.join(scripts,'validate-harness.mjs'),'--target',dir,'--min-score','0']).status,0);
}));
test('explicit verification runs from generated project directory', () => temporary(async dir => {
  execFileSync('node',[path.join(scripts,'create-harness.mjs'),'--target',dir,'--commands','test -f feature_list.json']);
  assert.equal(spawnSync('bash',[path.join(dir,'init.sh')],{cwd:os.tmpdir()}).status,0);
}));

test('generator rejects explicitly empty commands before writing instructions', () => temporary(async dir => {
  assert.notEqual(spawnSync('node', [path.join(scripts, 'create-harness.mjs'), '--target', dir, '--commands=']).status, 0);
  await assert.rejects(readFile(path.join(dir, 'AGENTS.md')));
}));
test('malformed dependency fields return errors rather than crash', () => {
  assert.ok(validate([{...feature(), dependencies: null}]).length);
  assert.ok(validate([{...feature(), dependencies: 7}]).length);
  assert.ok(validate([null]).length);
});

test('forced generator rejects symlink output without modifying its target', () => temporary(async dir => {
  const outside = path.join(dir, 'outside.md');
  const target = path.join(dir, 'project');
  await mkdir(target);
  await writeFile(outside, 'keep');
  const { symlink } = await import('node:fs/promises');
  await symlink(outside, path.join(target, 'AGENTS.md'));
  assert.notEqual(spawnSync('node', [path.join(scripts, 'create-harness.mjs'), '--target', target, '--force']).status, 0);
  assert.equal(await readFile(outside, 'utf8'), 'keep');
}));
test('benchmark self-check actually executes generated verification', () => temporary(async dir => {
  execFileSync('node', [path.join(scripts, 'create-harness.mjs'), '--target', dir, '--commands', 'true']);
  execFileSync('node', [path.join(scripts, 'run-benchmark.mjs'), '--target', dir]);
  const result = JSON.parse(await readFile(path.join(dir, 'harness-benchmark.json'), 'utf8'));
  assert.equal(result.selfCheck.verificationExecuted, true);
  assert.equal(result.evals.caseExecution, 'NOT_RUN');
}));

test('invalid benchmark thresholds cannot turn a failure into success', () => temporary(async dir => {
  for (const command of ['validate-harness.mjs','run-benchmark.mjs']) {
    assert.notEqual(spawnSync('node',[path.join(scripts,command),'--target',dir,'--min-score','NaN']).status,0);
  }
}));
