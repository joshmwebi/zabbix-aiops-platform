.PHONY: sandbox sandbox-down deps hosts triage triage-dry serve

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

# Enrich currently-active problems, once
triage:
	python alert-enrichment/poll.py --once

# Same, but print context instead of calling the LLM
triage-dry:
	python alert-enrichment/poll.py --once --dry-run

# Run the webhook receiver
serve:
	uvicorn app:app --host 0.0.0.0 --port 8000 --app-dir alert-enrichment
