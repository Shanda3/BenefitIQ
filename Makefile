.PHONY: fetch ingest ingest-stub ingest-payers count test

DATABASE_URL ?= postgresql+psycopg://benefitiq:benefitiq@localhost:5432/benefitiq
export DATABASE_URL

fetch:
	python -m benefitiq.cli fetch-public --manifest corpus/public/manifest.yaml

ingest: fetch
	python -m benefitiq.cli wait-db
	python -m benefitiq.cli init-db
	python -m benefitiq.cli ingest --manifest corpus/public/manifest.yaml

ingest-stub:
	python -m benefitiq.cli wait-db
	python -m benefitiq.cli init-db
	python -m benefitiq.cli ingest --manifest corpus/stub/manifest.yaml
	python -m benefitiq.cli ingest --manifest corpus/payers/manifest.yaml

ingest-payers:
	python -m benefitiq.cli fetch-payers
	python -m benefitiq.cli wait-db
	python -m benefitiq.cli init-db
	python -m benefitiq.cli ingest --manifest corpus/payers/manifest.yaml
	@if [ -f corpus/payers/cache/indexed-manifest.yaml ]; then python -m benefitiq.cli ingest --manifest corpus/payers/cache/indexed-manifest.yaml; fi

count: fetch
	python -m benefitiq.cli count-public --manifest corpus/public/manifest.yaml

test:
	python scripts/phi_scan.py
	python -m pytest -q
