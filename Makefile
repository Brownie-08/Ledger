.PHONY: up down migrate test lint format run

up:
	docker compose up --build -d
down:
	docker compose down
migrate:
	python manage.py migrate
run:
	python manage.py runserver 0.0.0.0:8000
test:
	pytest
lint:
	ruff check . && ruff format --check .
format:
	ruff check --fix . && ruff format .
