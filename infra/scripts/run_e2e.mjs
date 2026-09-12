// Browser localhost origins mirror published developer URLs. TCP relays preserve
// Host/Origin/headers and always send API traffic through Kong, never the backend.
import net from 'node:net'
import { spawn } from 'node:child_process'

const servers = []
for (const [port, host, upstreamPort] of [[8080, 'frontend', 3000], [8000, 'api_gateway', 8000]]) {
  const server = net.createServer((socket) => {
    const upstream = net.connect({ host, port: upstreamPort })
    socket.on('error', () => upstream.destroy())
    upstream.on('error', () => socket.destroy())
    socket.on('close', () => upstream.destroy())
    upstream.on('close', () => socket.destroy())
    socket.pipe(upstream).pipe(socket)
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(port, '127.0.0.1', resolve)
  })
  servers.push(server)
}
const args = ['/tests/node_modules/@playwright/test/cli.js', 'test']
const headed = process.env.E2E_HEADED === '1'
const child = headed
  ? spawn('xvfb-run', ['-a', process.execPath, ...args, '--headed'], { stdio: 'inherit' })
  : spawn(process.execPath, args, { stdio: 'inherit' })
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal))
child.on('error', () => process.exit(1))
child.on('exit', (code) => {
  for (const server of servers) server.close()
  process.exit(code ?? 1)
})
