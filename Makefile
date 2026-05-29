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
# DJANGO
# ============================================

migrate:
	$(COMPOSE) exec backend python manage.py migrate --noinput

makemigrations:
	$(COMPOSE) exec backend python manage.py makemigrations

shell:
	$(COMPOSE) exec backend python manage.py shell

createsuperuser:
	$(COMPOSE) exec backend python manage.py createsuperuser

collectstatic:
	$(COMPOSE) exec backend python manage.py collectstatic --noinput

# ============================================
# TESTING
# ============================================

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
