import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'

const helper = new URL('../npm_install.mjs', import.meta.url).pathname

test('a changed package input cannot reuse a lock-only install marker', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'complect-npm-check-'))
  try {
    const bin = path.join(root, 'bin')
    fs.mkdirSync(bin)
    fs.writeFileSync(path.join(bin, 'npm'), '#!/bin/sh\nif [ "$1" = "--version" ]; then echo 10.9.8; exit 0; fi\nif [ -f fail-ci ]; then exit 7; fi\n/bin/mkdir -p node_modules\n', { mode: 0o755 })
    fs.writeFileSync(path.join(root, 'package.json'), '{"name":"fixture"}\n')
    fs.writeFileSync(path.join(root, 'package-lock.json'), '{}\n')
    const options = { cwd: root, env: { ...process.env, PATH: bin }, encoding: 'utf8' }
    assert.equal(spawnSync(process.execPath, [helper], options).status, 0)
    fs.writeFileSync(path.join(root, 'package.json'), '{"name":"changed-input"}\n')
    fs.writeFileSync(path.join(root, 'fail-ci'), '')
    assert.equal(spawnSync(process.execPath, [helper], options).status, 7)
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
