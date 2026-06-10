.PHONY: demo seed pipeline dashboard test clean install

PY ?= python

install:                ## install the one runtime dep (streamlit pulls in pandas)
	pip install -r requirements.txt

seed:                   ## generate the simulated ERP/MRP/HRIS CSV exports
	$(PY) -m fip.seed

pipeline: seed          ## schema -> ETL+reconcile -> views -> tableau_export/
	$(PY) -m fip.pipeline

dashboard:              ## serve the local dashboard (reads the same views Tableau would)
	streamlit run app/dashboard.py

demo: pipeline          ## one command: seed, build, reconcile, export, then serve
	@echo ""
	@echo "Build complete. Launching dashboard (Ctrl-C to stop)..."
	@echo "  • reconciliation report : RECONCILIATION.md"
	@echo "  • tableau extracts       : tableau_export/"
	@echo ""
	streamlit run app/dashboard.py

test:                   ## run the test suite
	$(PY) -m pytest -q

clean:                  ## remove generated artifacts
	rm -f fip.db RECONCILIATION.md
	rm -rf tableau_export/*.csv seeds/*.csv
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
