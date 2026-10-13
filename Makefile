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

# Local stand and complete Payments acceptance gate.
test-payments-stand:
	python3 -B infra/scripts/run_payments_gate.py $(PAYMENTS_GATE_ARGS)

test-payments-gate:
	python3 -B infra/scripts/run_payments_gate.py --scope full $(PAYMENTS_GATE_ARGS)

# ============================================
# DJANGO
# ============================================

migrate:
	$(COMPOSE) run --rm --no-deps migrate

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

tsc: generate-sdk
	$(COMPOSE) exec frontend sh -c "cd /app && npx tsc --noEmit --incremental false"

generate-sdk:
	python3 -B infra/scripts/dev_environment.py sdk --root "$(DEV_ROOT)"

test-e2e: generate-sdk
	python3 -B infra/scripts/dev_environment.py e2e --root "$(DEV_ROOT)"

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
	bash infra/scripts/doctor.sh

# Dependency tooling runs in disposable containers, without the development stack.
.PHONY: lock-backend check-backend-lock check-toolchain
TOOLCHAIN_IMAGE := complect-dependency-toolchain:uv-0.11.21

lock-backend:
	docker build --target toolchain -f backend/Dockerfile.dev -t $(TOOLCHAIN_IMAGE) backend
	docker run --rm --user "$$(id -u):$$(id -g)" -e UV_CACHE_DIR=/tmp/uv-cache -v "$(CURDIR):/workspace" -w /workspace $(TOOLCHAIN_IMAGE) python backend/requirements/lock.py compile

check-backend-lock:
	docker build --target toolchain -f backend/Dockerfile.dev -t $(TOOLCHAIN_IMAGE) backend
	docker run --rm --user "$$(id -u):$$(id -g)" -e UV_CACHE_DIR=/tmp/uv-cache -v "$(CURDIR):/workspace:ro" -w /workspace $(TOOLCHAIN_IMAGE) python backend/requirements/lock.py check

check-toolchain:
	docker run --rm -v "$(CURDIR):/workspace:ro" -w /workspace $$(jq -r .python.image toolchain.json) python infra/scripts/check_toolchain.py \
		--host-version docker="$$(docker version --format '{{.Server.Version}}')" \
		--host-version docker_compose="$$(docker compose version --short)"

.PHONY: bootstrap sync doctor
bootstrap:
	bash infra/scripts/bootstrap.sh --root "$(DEV_ROOT)"

sync:
	bash infra/scripts/sync.sh --root "$(DEV_ROOT)"

doctor:
	bash infra/scripts/doctor.sh --root "$(DEV_ROOT)"

.PHONY: harness-check
harness-check:
	bash "$(dir $(abspath $(lastword $(MAKEFILE_LIST))))infra/scripts/check_agent_harness.sh"
