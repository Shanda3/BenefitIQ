# BenefitIQ

BenefitIQ is a dental coding and billing assistant for a human biller. A case includes procedures, diagnoses, clinical documentation, and insurance context. The app shows how dental and medical insurance could be billed, and every coding or coverage line cites a corpus source.

This repository is a working skeleton. Recommendations come from rules and retrieval. There is no language model in the path. The repo contains synthetic cases, a fictional stub corpus used by the tests and the offline demo, and loaders for a public Oklahoma source set. The public files are downloaded into a gitignored cache. They are not committed.

**Decision support for a human biller. Not a final coding or coverage determination.**

The home page is a four-step check for a dental office manager: pick a procedure, answer yes / no / not sure, read one result, and print a one-page summary. Questions and result rules live in `questions/procedures.yaml`. Each one cites a source registered from `corpus/office/`. Answers stay in the browser session. The case is not written to the database, and there is no login.

The engineer case screen is at `/advanced`. `POST /api/evaluate` is unchanged.

Two procedures are active: sleep apnea oral appliance, and dental care needed before a medical treatment (Medicare). The registered public sources are Medicare LCD L33611, Policy Article A52512, Medicare Benefit Policy Manual Chapter 15 §150, and the CMS Medicare dental coverage page. `corpus/office/` stores the short facts this tool is allowed to cite, with the public URL and effective date. It is not a copy of those publications. A line that cannot be tied to an in-window source is shown as `not found`.

## The five rules

1. **Codes follow documentation.** The engine does not suggest upcoding, unbundling, or a different date of service. If the note does not support a code, the result lists that item as `documentation missing` instead of recommending the code.
2. **No citation, no recommendation.** A coding or coverage line is returned only with a corpus source that has a URL and an effective window covering the date of service. Otherwise the line is `not found`. The app does not guess.
3. **Synthetic patients only.** Do not put real patient data in the repo, fixtures, logs, prompts, or docs. CI runs `scripts/phi_scan.py`, which fails the build on SSN-shaped numbers, telephone numbers, member identifiers, and a personal name paired with a birth date.
4. **No CDT or CPT descriptors.** Code numbers are shown with short placeholder labels from `labels.yaml` only. HCPCS Level II, including D-codes, is stored as the code number plus that placeholder. Descriptor text from the HCPCS file is dropped, and CPT/Level I codes are rejected. ICD-10-CM descriptors in the CMS code tables are public domain and may be stored. Structured stub rows still reject descriptor fields.
5. **The decision-support banner is part of every result.** The UI and the API both carry this exact sentence: `Decision support for a human biller. Not a final coding or coverage determination.`

## Architecture

```text
synthetic case
    -> rules drafter          (corpus rules + documentation spans)
    -> hard guards            (upcoding, unbundling, malignancy, date of service)
    -> citation validator     (drops anything without an in-window source)
    -> API and server-rendered UI
```

Postgres stores the corpus. Prose policies are chunked on section headings. Structured code tables, checklists, coverage rules, and bundling rules are stored as rows and also as citable chunks. A new version is a new `source_id` with its own effective dates. Ingestion refuses to overwrite an existing source.

Retrieval is an exact code-table lookup plus hybrid search: Postgres full text and pgvector. Filters are payer, state, plan type, and date of service inside the effective window. Each hit includes the chunk id, URL, and effective dates. Embeddings are a deterministic local hash so the skeleton does not need an API key.

`RulesDrafter` is the only drafter wired up today. `evaluate()` accepts another drafter with the same `draft()` shape. Hard guards and `CitationValidator` still run after it, which is the slot for a later LLM drafting step. A drafted URL is ignored. The validator reloads the URL and the effective dates from the registry.

Logs record the case id only. Clinical note text is not written to the log, and cases are not stored.

## Local development

Docker Compose starts Postgres with pgvector and the app. The app waits for the database, creates tables, ingests the stub corpus, and serves on port 8000.

```bash
docker compose up --build
```

Open `http://localhost:8000`. The database password in `docker-compose.yml` is a local development default, not a deployed secret.

Without Compose, against a Postgres instance that has pgvector:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://benefitiq:benefitiq@localhost:5432/benefitiq
python -m benefitiq.cli wait-db
python -m benefitiq.cli init-db
python -m benefitiq.cli ingest --manifest corpus/stub/manifest.yaml
python -m benefitiq.cli ingest --manifest corpus/office/manifest.yaml
uvicorn benefitiq.main:app --reload
```

Run the tests and the PHI scan:

```bash
python scripts/phi_scan.py
pytest
```

Load the public Oklahoma corpus into Postgres with `make ingest`. That downloads the files below into `corpus/public/cache/` (gitignored), then ingests `corpus/public/manifest.yaml`. `make count` prints the Oklahoma row counts without writing the database. `make ingest-stub` loads only the fictional corpus. Docker Compose still loads the stub corpus so the sample UI can start offline.

## Stub corpus

`corpus/stub/` is fictional. Titles say `FICTIONAL STUB`. It exists so the tests and the UI can run before a real corpus arrives. It includes:

- a trauma coding note and rules
- a biopsy note and rules
- a fictional Stub Payer B appliance policy
- exam-level rules
- a Stub Payer A bundling rule
- two Stub Payer A dental policy versions split on 2025-07-01
- a code-number index
- one fictional Medicaid note used to exercise filters

Nothing in that folder is a CMS, ADA, AMA, or payer publication. The tests and the Docker demo keep using it. It is separate from `corpus/public/`.

## Public Oklahoma sources

Each source below is one registry row. It stores the exact file URL, the publisher, the effective window, the retrieval time, and the SHA-256 of the downloaded bytes. Ingestion will not overwrite an id. A republication needs a new `source_id`. Marketplace and landscape loaders keep Oklahoma rows (`OK` or `Oklahoma`) and drop every other state. SHOP landscape files are part of the set; a plan year with no Oklahoma SHOP rows is stored as a source with zero plan rows, not as an invented plan.

The effective window is the plan year, the ICD-10-CM fiscal year, or the HCPCS quarter named in the file. It is not the day the file was posted.

| Source | File URL | Effective window |
| --- | --- | --- |
| PY2027 Plan Attributes PUF | https://download.cms.gov/marketplace-puf/2027/plan-attributes-puf.zip | 2027-01-01 to 2027-12-31 |
| PY2027 Benefits and Cost Sharing PUF | https://download.cms.gov/marketplace-puf/2027/benefits-and-cost-sharing-puf.zip | 2027-01-01 to 2027-12-31 |
| PY2027 Rate PUF | https://download.cms.gov/marketplace-puf/2027/rate-puf.zip | 2027-01-01 to 2027-12-31 |
| PY2027 Service Area PUF | https://download.cms.gov/marketplace-puf/2027/service-area-puf.zip | 2027-01-01 to 2027-12-31 |
| PY2027 QHP Landscape, individual medical | https://data.healthcare.gov/datafile/py2027/individual_market_medical.zip | 2027-01-01 to 2027-12-31 |
| PY2027 QHP Landscape, individual dental (SADP) | https://data.healthcare.gov/datafile/py2027/individual_market_dental.zip | 2027-01-01 to 2027-12-31 |
| PY2027 QHP Landscape, SHOP medical | https://data.healthcare.gov/datafile/py2027/shop_market_medical.zip | 2027-01-01 to 2027-12-31 |
| PY2027 QHP Landscape, SHOP dental (SADP) | https://data.healthcare.gov/datafile/py2027/shop_market_dental.zip | 2027-01-01 to 2027-12-31 |
| ICD-10-CM FY2027 code tables and tabular index | https://www.cms.gov/files/zip/2027-code-tables-tabular-index.zip | 2026-10-01 to 2027-09-30 |
| HCPCS Level II, October 2026 | https://www.cms.gov/files/zip/october-2026-alpha-numeric-hcpcs-file.zip | 2026-10-01 to 2026-12-31 |
| HCPCS Level II, January 2027 | https://www.cms.gov/files/zip/january-2027-alpha-numeric-hcpcs-file.zip | 2027-01-01 to 2027-03-31 |

Publishers are the Centers for Medicare & Medicaid Services, and the Center for Consumer Information and Insurance Oversight for the landscape files. The PUF links are the files listed for plan year 2027 on the [CMS Exchange PUF page](https://www.cms.gov/marketplace/resources/data/public-use-files). The HCPCS links are the October 2026 and January 2027 alpha-numeric files on the [HCPCS Quarterly Update page](https://www.cms.gov/medicare/coding-billing/healthcare-common-procedure-system/quarterly-update). The landscape links are the PY2027 zip distributions under `https://data.healthcare.gov/datafile/py2027/`.

`make ingest` checks `robots.txt` before each download. A missing robots.txt is treated as allowed. A disallow, a non-HTTPS URL, or a host other than `cms.gov`, `www.cms.gov`, `download.cms.gov`, or `data.healthcare.gov` stops the fetch. The fetcher does not log in and does not submit a license or terms form. ICD-10-CM tabular descriptors are stored. HCPCS rows store the Level II code number and the placeholder label only. D-code descriptor text from the HCPCS file is discarded. A CPT or other Level I code rejects the file.

Small fixtures under `corpus/public/fixtures/` cover the loaders. They are a few synthetic rows. Any D-code descriptor text in the HCPCS fixture is a replacement token, not CDT wording.

## How to add a real source

1. Put the file in a folder outside the stub corpus. Do not copy CDT or CPT descriptor text into it. For a code table, store the code system, the code number, and a `label_ref` that already exists in `labels.yaml`.
2. Add a manifest entry with `source_id`, `family_id`, `title`, `publisher`, `url`, `effective_from`, `doc_type`, and `loader`. `url` and `effective_from` are required. Set `effective_to` when the document was superseded. Set `payer`, `state`, `program`, and `plan_type` when the document is not general guidance.
3. Use `loader: prose` for a Markdown policy. Headings that start with `##` become section anchors. Use `loader: structured` for YAML rows (`coding_rules`, `coverage_rules`, `routing_rules`, `checklist`, `bundle_rules`, `code_rows`).
4. A replacement version gets a new `source_id` and a new effective window. Do not edit a source that was already ingested. The loader refuses an overwrite when the content hash changes.
5. Run `python -m benefitiq.cli ingest --manifest path/to/manifest.yaml`.

To support a new file shape, write a loader function and register it:

```python
from benefitiq.ingestion.loaders import register_loader

@register_loader("my_format")
def load_my_format(path, spec):
    ...
```

Point the manifest `loader` field at that name. Keep copyrighted descriptors out of the loader output. Real patient data does not belong in a source file.

Coverage rows only match when the source payer, plan type, state, and effective window fit the case. A code-index row can prove that a code number is in the table. It is not treated as a coverage determination.

## API

`POST /api/evaluate` accepts a synthetic case. `synthetic` must be true and `case_id` must start with `SYN-`. The response always includes the decision-support sentence, the original date of service, dental and medical lines, documentation gaps, guardrails, and citations with URLs and effective dates.

`GET /health` reports whether the database is up and how many sources are loaded. It is not a billing result.

## Tests

The eight case files in `samples/` are the behaviors CI checks against the stub corpus:

| Sample | Expected behavior |
| --- | --- |
| Trauma | Medical pathway first, injury and external-cause codes cited. Missing injury date or place is `documentation missing` and the external-cause code is not recommended. |
| Biopsy | Lesion code only. A malignancy code is refused. Missing pathology is flagged. |
| Sleep apnea appliance | Medical equipment pathway. Sleep study, prescription, and CPAP-intolerance requirements are shown from the stub policy. The missing intolerance note blocks the device code. |
| CBCT for implant planning | No coverage policy is in the corpus, so the lookup is `not found`. |
| Upcoding | A higher exam code than the note supports is refused, with the missing elements listed. An alternate date of service is ignored. |
| Unbundling | Components of a stub bundle entered separately are flagged. They are not returned as separately payable lines. |
| Effective date | A date of service before the policy change cites the older version only. |
| Unknown payer | No matching source, so every line is `not found`. |

## What this skeleton does not do

It does not call a payer, a clearinghouse, or a model provider. It does not commit the CMS or Healthcare.gov downloads. `make ingest` fetches those public files into the gitignored cache. It does not produce a claim file. A biller still has to make the coding and coverage decision.
