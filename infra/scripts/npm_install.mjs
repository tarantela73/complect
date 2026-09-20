import fs from 'node:fs'
import crypto from 'node:crypto'
import { spawnSync } from 'node:child_process'

const npm = spawnSync('npm', ['--version'], { encoding: 'utf8' })
if (npm.status !== 0) process.exit(1)
const npmVersion = npm.stdout.trim()
if (process.env.EXPECTED_NODE && process.version !== `v${process.env.EXPECTED_NODE}`) {
  throw new Error('Container Node version differs from the reviewed toolchain')
}
if (process.env.EXPECTED_NPM && npmVersion !== process.env.EXPECTED_NPM) {
  throw new Error('Container npm version differs from the reviewed toolchain')
}
const digest = crypto.createHash('sha256').update(fs.readFileSync('package-lock.json')).update(fs.readFileSync('package.json')).update(process.version).update(npmVersion).digest('hex')
const marker = 'node_modules/.complect-install'
if (fs.existsSync(marker) && fs.readFileSync(marker, 'utf8').trim() === digest) {
  console.log('PASS: npm dependencies match the checkout lock and container toolchain')
} else {
  const result = spawnSync('npm', ['ci', '--no-audit', '--no-fund'], { stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status || 1)
  fs.writeFileSync(marker, digest + '\n')
  console.log('PASS: npm ci completed without modifying the lockfile')
}
