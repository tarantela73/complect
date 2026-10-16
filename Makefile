DEV_ROOT ?= $(CURDIR)
COMPOSE=python3 -B infra/scripts/dev_environment.py compose --root "$(DEV_ROOT)"

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

agent-infra-test:
	python3 -B infra/scripts/run_infra_tests.py all $(INFRA_TEST_ARGS)

agent-infra-image:
	docker build -f backend/Dockerfile.dev -t "$${INFRA_TEST_BACKEND_IMAGE:-complect_backend:dev}" backend
	docker build --build-arg BASE_IMAGE="$${INFRA_TEST_BACKEND_IMAGE:-complect_backend:dev}" -t "$${INFRA_TEST_QUALITY_IMAGE:-complect-infra-quality:dev}" -f infra/Dockerfile.tests .

legacy-stand-prepare:
	python3 -B -m infra.scripts.prepare_legacy_stand prepare $(LEGACY_STAND_ARGS)

legacy-stand-cleanup:
	python3 -B -m infra.scripts.prepare_legacy_stand cleanup $(LEGACY_STAND_ARGS)

legacy-stand-test:
	python3 -B -m infra.scripts.run_legacy_regressions $(LEGACY_STAND_ARGS)

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
	python3 -B infra/scripts/dev_environment.py sdk --root "$(DEV_ROOT)"

test-e2e: generate-sdk
	python3 -B infra/scripts/dev_environment.py e2e --root "$(DEV_ROOT)"

doctor:
	python3 -B infra/scripts/dev_environment.py doctor --root "$(DEV_ROOT)"

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

# Disposable Payments contract: same runner as the required CI job.
test-payments-stand:
	python3 -B infra/scripts/run_payments_gate.py $(PAYMENTS_GATE_ARGS)

test-payments-gate:
	python3 -B infra/scripts/run_payments_gate.py --scope full $(PAYMENTS_GATE_ARGS)
