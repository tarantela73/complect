COMPOSE=docker compose -f docker-compose.dev.yml

# ============================================
# CORE LIFECYCLE
# ============================================

up:
	$(COMPOSE) up --build -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

rebuild:
	$(COMPOSE) down
	$(COMPOSE) up --build -d

destroy:
	$(COMPOSE) down -v

# ============================================
# LOGS
# ============================================

logs:
	$(COMPOSE) logs -f --tail=200

backend-logs:
	$(COMPOSE) logs -f --tail=200 backend

worker-logs:
	$(COMPOSE) logs -f --tail=200 celery_worker

beat-logs:
	$(COMPOSE) logs -f --tail=200 celery_beat

postgres-logs:
	$(COMPOSE) logs -f --tail=200 postgres_primary

# ============================================
# LOCAL AGENT MEMORY
# ============================================

supermemory-up:
	curl --fail --silent --show-error http://127.0.0.1:8090/health >/dev/null
	$(COMPOSE) up --build -d supermemory

supermemory-logs:
	$(COMPOSE) logs --no-color -f --tail=200 supermemory | sed -u -E 's/sm_[A-Za-z0-9_-]{20,}/[REDACTED_SUPERMEMORY_KEY]/g'

supermemory-status:
	curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8090/health
	$(COMPOSE) ps supermemory
	curl --fail --silent --show-error --output /dev/null http://127.0.0.1:6767/

supermemory-down:
	$(COMPOSE) stop supermemory

agent-infra-test:
	python3 -m unittest discover -s infra/scripts/tests -v

# ============================================
# DJANGO
# ============================================

migrate:
	$(COMPOSE) exec backend python manage.py migrate --noinput

makemigrations:
	$(COMPOSE) exec backend python manage.py makemigrations

seed-finance-dev:
	$(COMPOSE) exec backend python manage.py seed_finance_dev

shell:
	$(COMPOSE) exec backend python manage.py shell

createsuperuser:
	$(COMPOSE) exec backend python manage.py createsuperuser

collectstatic:
	$(COMPOSE) exec backend python manage.py collectstatic --noinput

# ============================================
# TESTING
# ============================================


test:
	$(COMPOSE) exec backend pytest apps/common/tests/test_context_leakage.py -v

test-common:
	$(COMPOSE) exec backend pytest apps/common/tests/ -v

test-contracts:
	$(COMPOSE) exec backend pytest apps/contracts/tests/ -v

test-contragents:
	$(COMPOSE) exec backend pytest apps/contragents/tests/ -v

test-integration:
	$(COMPOSE) exec backend pytest apps/integration/tests/ -v

test-realtime:
	$(COMPOSE) exec backend pytest apps/realtime/tests/ -v

test-all:
	$(COMPOSE) exec backend pytest -v

test-fast:
	$(COMPOSE) exec backend pytest -x

test-failed:
	$(COMPOSE) exec backend pytest --lf -v

coverage:
	$(COMPOSE) exec backend pytest --cov=. --cov-report=term-missing

# ============================================
# FRONTEND
# ============================================

build-frontend:
	$(COMPOSE) exec -e NODE_ENV=production frontend sh -c "cd /app && npm run build"

tsc:
	$(COMPOSE) exec frontend sh -c "cd /app && npx tsc --noEmit"

generate-sdk:
	docker compose -f docker-compose.dev.yml exec backend python -c "\
import os, json; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local');\
import django; django.setup();\
from config.urls import api; print(json.dumps(api.get_openapi_schema()))" \
	> /tmp/openapi.json
	docker cp /tmp/openapi.json complect_frontend_dev:/tmp/openapi.json
	$(COMPOSE) exec frontend npx openapi-typescript /tmp/openapi.json -o src/shared/api/generated/schema.d.ts

test-e2e:
	cd frontend/e2e && PLAYWRIGHT_BROWSERS_PATH=/home/sever/.cache/ms-playwright npm install --silent && PLAYWRIGHT_BROWSERS_PATH=/home/sever/.cache/ms-playwright npx playwright test

# ============================================
# QUALITY
# ============================================

lint:
	$(COMPOSE) exec backend ruff check .

format:
	$(COMPOSE) exec backend ruff format .

types:
	$(COMPOSE) exec backend mypy .

# ============================================
# DATABASE
# ============================================

psql:
	$(COMPOSE) exec postgres_primary psql -U complect_admin -d complect_db

redis-cli:
	$(COMPOSE) exec redis redis-cli

# ============================================
# CELERY
# ============================================

celery-status:
	$(COMPOSE) exec celery_worker celery -A config inspect ping

# ============================================
# CLEANUP
# ============================================

prune:
	docker system prune -af

# ============================================
# HEALTH
# ============================================

health:
	curl http://localhost:8010/health/
