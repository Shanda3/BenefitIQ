.PHONY: fetch ingest ingest-stub ingest-payers count test check-updates approve-policy load-pilot deploy

LOCAL_DATABASE_URL := postgresql+psycopg://benefitiq:benefitiq@localhost:5432/benefitiq

fetch ingest ingest-stub ingest-payers count test check-updates approve-policy: export DATABASE_URL ?= $(LOCAL_DATABASE_URL)

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
	python -m benefitiq.cli wait-db
	python -m benefitiq.cli init-db
	python -m benefitiq.cli ingest --manifest corpus/payers/manifest.yaml
	python -m benefitiq.cli ingest-policies

check-updates:
	python -m benefitiq.cli wait-db
	python -m benefitiq.cli check-updates

approve-policy:
	python -m benefitiq.cli approve-policy --source-id $(SOURCE)

count: fetch
	python -m benefitiq.cli count-public --manifest corpus/public/manifest.yaml

test:
	python scripts/phi_scan.py
	python -m pytest -q

load-pilot:
	python -m benefitiq.cli load-pilot

deploy:
	./scripts/deploy_pilot.sh
