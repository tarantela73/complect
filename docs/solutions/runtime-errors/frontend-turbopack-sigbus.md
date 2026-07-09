# Frontend Turbopack SIGBUS on Docker startup

## Problem

Frontend container fails to start with `SIGBUS` error during `npm install` or `next dev --turbopack`:

```
npm error path /app
npm error command failed
npm error signal SIGBUS
npm error command sh -c next dev --turbopack
```

## Root cause

Docker volume mounts (especially `.next`, `node_modules`) can become corrupted with wrong file ownership/permissions when:
- Container runs as root internally (`USER node` in Dockerfile)
- Host writes to volumes before container starts
- Previous container was killed abruptly leaving stale files

Turbopack uses native Node.js bindings that can fail with `SIGBUS` (bus error) when accessing corrupted file system state.

## Solution

### Immediate fix (docker-compose.dev.yml)

```yaml
environment:
  - TURBOPACK=1
  - WATCHPACK_POLLING=true  # Enable polling for Docker file watching

command: sh -c "npm install --no-audit --no-fund && npm run dev"  # Suppress npm noise
```

- `WATCHPACK_POLLING=true` — forces chokidar/watchpack to use polling instead of inotify, which is more reliable in Docker volumes
- `TURBOPACK=1` — explicitly enables Turbopack mode
- `--no-audit --no-fund` — suppresses npm output noise during container startup

### Recovery from corrupted state

```bash
# Stop and remove frontend container + volumes
docker compose -f docker-compose.dev.yml rm -sf frontend
docker volume prune -f  # Remove stale volumes

# Or targeted: remove specific volumes
docker volume ls --filter name=Complect_frontend_data  # Find volume name
docker volume rm <volume_name>  # Remove stale volume
```

### Prevention

1. Never mount `node_modules` or `.next` as persistent Docker volumes — they should be ephemeral
2. Use `npm ci` instead of `npm install` in production/Docker for deterministic installs
3. If SIGBUS recurs, clean volumes and restart

## Related

- [npm SIGBUS issues](https://github.com/npm/cli/issues?q=sigbus)
- Next.js Turbopack Docker: https://nextjs.org/docs/app/api-reference/config/next-config-js/turbopack#using-turbopack-with-docker