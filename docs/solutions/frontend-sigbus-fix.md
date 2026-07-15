# Frontend SIGBUS Fix (2026-07-09)

## Проблема

```
npm error path /app
npm error command failed
npm error signal SIGBUS
npm error command sh -c next dev --turbopack
```

## Причина

`.next` и `node_modules` монтировались через bind mount из хоста в Docker контейнер. Файлы создавались от `root` внутри контейнера, что приводило к ошибке `BUSY_FILE_ERROR` (SIGBUS) при маппинге на host-файловую систему.

## Решение

1. Остановить и удалить контейнер frontend:
   ```bash
   docker compose -f docker-compose.dev.yml rm -sf frontend
   ```

2. Очистить повреждённые volumes:
   ```bash
   docker volume prune -f
   ```

3. Перезапустить стек:
   ```bash
   make up
   ```

4. Если проблема повторяется — исправить права на файлы:
   ```bash
   chown -R 1000:1000 frontend/node_modules frontend/.next
   ```

## Профилактика

- `node_modules` и `.next` должны создаваться **внутри** контейнера, не через bind mount с хоста
- В `docker-compose.dev.yml` для frontend использовать named volume или bind mount только для исходного кода (`src/`), не для `node_modules/.next`