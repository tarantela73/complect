---
title: "Docker Desktop WSL2 port proxy ghost — EADDRINUSE with nothing in ss -tlnp"
date: 2026-06-25
category: developer-experience
module: infrastructure
problem_type: environment_issue
component: docker
symptoms:
  - "Docker container fails to start: Ports are not available: address already in use"
  - "ss -tlnp / netstat -tlnp shows no process on the port"
  - "NetworkSettings.Ports returns [] (empty) even though HostConfig.PortBindings shows the mapping"
  - "Python socket.bind() raises OSError: [Errno 98] Address already in use on ports 3000-3003"
  - "docker restart <container> fails with OCI runtime error — no such file or directory"
root_cause: environment_issue
resolution_type: workaround
severity: high
tags:
  - docker-desktop
  - wsl2
  - port-conflict
  - frontend
---

## Failure Mode: Docker Desktop WSL2 proxy ghost-claims ports at kernel level

**Problem.** В WSL2-среде с Docker Desktop порты 3000–3003 недоступны для bind — контейнеры не стартуют с ошибкой "Ports are not available". При этом `ss -tlnp` не показывает ни одного процесса на этих портах. Стандартные инструменты диагностики видят порты как свободные, но bind всё равно падает.

**Symptoms:**

- Запуск контейнера с `ports: "3000:3000"` завершается с ошибкой привязки порта
- `ss -tlnp | grep 3000` — пустой вывод
- `lsof -i :3000` — ничего
- `docker inspect <container> --format '{{.NetworkSettings.Ports}}'` возвращает `map[]` (пустой), но `docker inspect <container> --format '{{.HostConfig.PortBindings}}'` показывает конфигурацию
- `docker restart <container>` завершается с ошибкой OCI runtime: `open /run/docker/runtime-runc/...: no such file or directory`

**Diagnosis.** Проверить реальную доступность порта через Python `socket.bind()` — единственный надёжный метод:

```bash
# Из WSL2 shell:
python3 -c "
import socket
for port in [3000, 3001, 3002, 3003, 8080, 8081]:
    s = socket.socket()
    try:
        s.bind(('0.0.0.0', port))
        print(f'port {port}: FREE')
    except OSError as e:
        print(f'port {port}: BLOCKED — {e}')
    finally:
        s.close()
"
```

**Root Cause.** Docker Desktop на Windows запускает процесс `/mnt/wsl/docker-desktop/docker-desktop-user-distro proxy`, который перехватывает порты на уровне ядра Linux. Этот процесс ghost-claims порты 3000–3003 (и потенциально другие) без создания видимых сокетов — именно поэтому `ss`/`netstat`/`lsof` ничего не показывают. Это known behavior Docker Desktop при работе через WSL2 integration layer.

**What Didn't Work:**

- `ss -tlnp` — не видит ghost-процессы Docker Desktop proxy
- `lsof -i :3000` — аналогично
- `docker restart <container>` — fail с OCI runtime ошибкой (stale bind mount paths в Docker Desktop WSL2 после перезапуска); нужен `--force-recreate`
- Перезапуск только контейнера без `--force-recreate` — не помогает, состояние Docker Desktop proxy не меняется

**Solution.** Сменить порт на хосте на значение выше 3003 (проверить доступность через `socket.bind()`):

```yaml
# docker-compose.dev.yml — было:
ports:
  - "3000:3000"

# стало (порт 8080 проверен как свободный):
ports:
  - "8080:3000"
```

Применить с `--force-recreate` (не `docker restart`):

```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate frontend
```

**После смены порта фронтенда — обязательно:**

Обновить `origins` в CORS-конфиге Kong (`infra/api_gateway/kong.yml`) и пересоздать gateway:

```yaml
# infra/api_gateway/kong.yml
origins:
  - "http://localhost:3000"
  - "http://localhost:8080"   # добавить новый порт
```

```bash
docker compose -f docker-compose.dev.yml up -d --force-recreate api_gateway
```

Без этого браузерный POST будет заблокирован CORS несмотря на `OPTIONS → 200`. Подробности: `docs/solutions/integration-issues/kong-cors-login-timeout.md`.

## Prevention

- **При первом запуске в новой WSL2-среде** — проверить доступность порта `socket.bind()` до добавления маппинга в `docker-compose.dev.yml`. Не доверять `ss -tlnp` в WSL2 + Docker Desktop.
- Диапазон `3000–3003` исторически ghost-claimed Docker Desktop; использовать `8080+` для dev-сервисов.
- `docker restart` ненадёжен в Docker Desktop WSL2 (OCI stale path). Стандартная команда — `docker compose up -d --force-recreate <service>`.
- Любое изменение порта фронтенда требует синхронного изменения CORS origins в Kong.

## Related Issues

- `docker-compose.dev.yml` — port mapping `frontend: "8080:3000"` (постоянно, порт 3000 заблокирован Docker Desktop)
- `infra/api_gateway/kong.yml` — CORS origins включает `http://localhost:8080`
- `docs/solutions/integration-issues/kong-cors-login-timeout.md` — Failure Mode B: stale origin после смены порта
