.PHONY: up down migrate run worker beat test cov lint format reconcile loadtest demo schema

up:
	docker compose up --build -d
down:
	docker compose down
migrate:
	python manage.py migrate
run:
	python manage.py runserver 0.0.0.0:8000
worker:
	celery -A config worker -l info
beat:
	celery -A config beat -l info
test:
	pytest
cov:
	pytest --cov --cov-report=term
lint:
	ruff check . && ruff format --check .
format:
	ruff check --fix . && ruff format .
reconcile:
	python manage.py reconcile
schema:
	python manage.py spectacular --file openapi.yaml
# Needs a running server (see README "Load test"); creates users/wallets itself.
loadtest:
	locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 60s --host http://localhost:8000
demo:
	python scripts/demo.py
