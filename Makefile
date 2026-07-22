.PHONY: sandbox sandbox-down deps hosts

# Start the local Zabbix sandbox (web UI on :8080, Admin/zabbix)
sandbox:
	docker compose up -d

# Stop sandbox and wipe its data
sandbox-down:
	docker compose down -v

# Install Python dependencies
deps:
	pip install -r requirements.txt

# Smoke test: list hosts visible to the API
hosts:
	python pipeline/zabbix_client.py
