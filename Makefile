.PHONY: help install run status login get-token validate-config test-notify replay test clean

help:
	@echo "========================================================================"
	@echo "Pragmatic Play Live Baccarat Monitoring Service - Management Commands"
	@echo "========================================================================"
	@echo "  make install         Install Python dependencies and Playwright browser"
	@echo "  make run             Start background monitoring service for all tables"
	@echo "  make status          Display ASCII status table (window, age, streak)"
	@echo "  make login           Open headed browser to log in and auto-capture token"
	@echo "  make get-token       Acquire fresh token via headless browser & save"
	@echo "  make validate-config Validate config.yaml syntax and rule schema"
	@echo "  make test-notify     Send test alert to Telegram/Console"
	@echo "  make replay          Replay captured HAR session stream"
	@echo "  make test            Run complete test suite (unit + integration)"
	@echo "  make clean           Clean up caches and temporary files"
	@echo "========================================================================"

install:
	pip install -e ".[dev]"
	playwright install chromium

run:
	python3 -m src.cli run --config config.yaml --env .env

status:
	python3 -m src.cli status --config config.yaml --env .env

login:
	python3 -m src.cli login

login-direct:
	python3 -m src.cli login --no-proxy

get-token:
	python3 -m src.cli get-token --table bc281koreanch281

validate-config:
	python3 -m src.cli validate-config --config config.yaml

test-notify:
	python3 -m src.cli test-notify --level info --config config.yaml --env .env

replay:
	python3 -m src.cli replay-har --har test/bxgnanhldtnm.top2.har --table spto11bctorobc11 --rounds 20

test:
	pytest -v

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	rm -f /tmp/pytest-of-*
