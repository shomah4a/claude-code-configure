.PHONY: serve

venv/.initialized: requirements.txt
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt
	touch $@

serve: venv/.initialized
	venv/bin/python3 mcp-proxy.py
