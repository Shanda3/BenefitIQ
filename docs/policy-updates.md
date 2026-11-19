# Checking payer policies

Shanda runs this check at least once a year. It re-downloads each public, dated policy the library is allowed to fetch, then compares the file hash and the date in the file with the version already stored.

The office tool keeps citing the current version. A changed file becomes a new version that waits for a person. Nothing here logs in, accepts a disclaimer, or deploys the app.

Decision support for a human biller. Not a final coding or coverage determination.

## First load

```bash
make ingest-payers
```

That downloads the eligible files into `corpus/payers/cache/` (gitignored), extracts the text, and stores the chunks in Postgres. The first copy of each file is the current version. Humana files that need a disclaimer, licensed criteria, login-only files, unverified addresses, sources that do not apply to Oklahoma, and undated sources are skipped.

Raw policy text stays in that cache and in the database. It is not committed.

## Yearly check

```bash
make check-updates
```

This writes two files:

- `reports/policy-updates.md` — plain language, grouped by what happened
- `reports/policy-updates.csv` — the same rows in a spreadsheet

The groups are:

- unchanged
- changed, needs review
- new date, needs review
- not in the library yet
- page not found
- page moved
- robots or terms now blocking
- could not check
- still pending, including Humana and the other files we do not download

A changed file or a new date is stored as a waiting version. The previous version stays in the database and is still what retrieval cites.

## What the screen means

On Sources and on each payer page:

- **Current** — this is the version a recommendation can cite.
- **Needs review** — a newer file is waiting. The office tool still cites the older version.
- **Blocked** — the site's rules now stop a download. Retrieval stops citing it until that changes.

Last checked is the day the file was last compared. Version is the number of the cited copy.

## Approve a change

Read the report. Open the payer's file and confirm the change is the one you want on file. Then, using the source id from the report:

```bash
make approve-policy SOURCE=the-source-id
```

That makes the waiting version current. The older version remains queryable as history. Retrieval uses the current version unless a history search is asked for explicitly.

There is no scheduled job. Approving is a person running that command.

## GitHub

In the repository, open Actions, choose **Policy update check**, and run it by hand. The workflow has no schedule and no secrets. It re-fetches the public files, writes the same report, and uploads the report as an artifact. It does not update the database and it does not approve anything.

## Embeddings

Chunks are embedded with the local open-source model `BAAI/bge-small-en-v1.5`. No API key is required, including in tests and CI.

If `OPENAI_API_KEY` is set in the environment, embeddings are requested from OpenAI instead, at the same vector width. Do not commit that key. The two models are not interchangeable. After switching, run `make ingest-payers` again so every chunk uses the same model.
