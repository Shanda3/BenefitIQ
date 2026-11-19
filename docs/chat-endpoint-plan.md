# Chat endpoint plan

This document is a design only. BenefitIQ does not implement `POST /api/chat`, does not call a model provider, and does not deploy this service. No client code holds an API key. The office panel labeled "Ask about this case (coming soon)" is disabled and does not start a request.

The decision-support label on every answer, including a refusal and an error, is the existing sentence:

> Decision support for a human biller. Not a final coding or coverage determination.

## Endpoint

`POST /api/chat` accepts a JSON body from the office UI:

| Field | Rule |
| --- | --- |
| `case_id` | Required. Must start with `SYN-`. |
| `synthetic` | Required. Must be true. Anything else is rejected. |
| `message` | The biller's question, after the PHI scan below. |
| `as_of` | The date already stored for that check. The model cannot supply a different date of service. |

The response is JSON. It always includes `disclaimer` set to the sentence above, `case_id`, the answer text, and the citations that survived validation (`source_id`, URL, effective dates, section anchor). It does not include a claim file, a payable amount, or a code descriptor.

The browser sends this request to the BenefitIQ origin only. It never calls a model host itself, and the page never receives the API key.

## Secret

The model key is the environment variable `OPENAI_API_KEY`.

- Cloudflare secrets hold it for the hosted container. Local development can export it in the shell. It is not written to the repo, a fixture, a log line, a prompt dump, or the browser.
- The client bundle, HTML, and error bodies do not contain the variable's value.
- If the variable is missing, the endpoint returns a clear unavailable response with the decision-support sentence. It does not fall back to an uncited answer.

## Retrieval-only grounding

The model is a drafter, in the same slot `evaluate()` already reserves. It does not get to invent coverage.

1. Load the synthetic check (procedure, yes / no / not sure answers, date). Do not send clinical note text that is not already part of that synthetic check.
2. Retrieve chunks from the registered corpus with the existing filters: payer, state, plan type, and the date inside the source effective window. Each hit keeps its chunk id, URL, and effective dates.
3. The prompt may contain only those retrieved chunks, the question, the case id, and the placeholder labels from `labels.yaml`. It may not contain CDT or CPT descriptor text, CPT codes, or a passage that is not in the retrieved set.
4. The draft comes back as the same draft shape the rules drafter uses (lines, gaps, guards), plus short prose that names the chunk ids it relied on.
5. `apply_hard_guards` runs unchanged.
6. The citation validator runs unchanged. It reloads the URL and the effective dates from the registry and drops a drafted URL. A line or a sentence with no in-window source becomes the internal status `not found`.
7. The office copy for that internal status stays the plain sentence: "Our sources don't cover this yet. Confirm with the payer."

If retrieval returns nothing, the answer is that plain sentence. The model is not asked to fill the gap from general knowledge.

## Refusals

These requests are refused, with the documented date and codes left as they are:

- **Upcoding.** A higher-level code than the documentation supports is refused. Missing documentation is listed. No higher code is offered as a workaround.
- **Unbundling.** Components of a bundle entered as separate codes are flagged. They are not returned as separately payable lines.
- **Date changes.** A request to use a different date of service is refused. `date_of_service_altered` stays false and the original date is the one that is cited.

The existing guard messages are the wording for these refusals. The chat layer does not add a second, softer recommendation after a refusal.

## No PHI

Cases stay synthetic. `synthetic: false`, a case id that does not start with `SYN-`, or a message that fails the PHI scan is rejected.

The scan is the same family of checks as `scripts/phi_scan.py`: SSN-shaped numbers, telephone numbers, member or policy identifier assignments, and a personal name paired with a birth date. The step either rejects the request or redacts the matching span before any prompt is built. A redacted message that still contains an identifier is rejected.

Chat content is not stored. It is not written to Postgres, to a log file, or to the case snapshot in the browser. The audit log line is the case id and the source ids that were cited, the same shape as `office_result` today. The message, the answer text, and any note text stay out of the log.

## Codes and the label

- Code numbers in the answer use placeholder labels from `labels.yaml` only.
- CPT codes are rejected, as they are in the question tree.
- CDT and CPT descriptor text is not placed in the prompt or the output.
- Every response, including refusals, validation errors, and rate-limit errors, carries the decision-support sentence.

## Rate limits

Limits are enforced on the server before the model is called:

- 20 requests per minute for one client address.
- 200 requests per day for one client address.

A request over the limit returns HTTP 429 with the decision-support sentence and no model output. The limit counters are not a case store. They do not keep the message text.

## Tests to add when this is built

The current suite does not call a model. When the endpoint exists, add tests that:

- An uncited draft is returned as "Our sources don't cover this yet. Confirm with the payer." and the internal status stays `not found`.
- Upcoding, unbundling, and a requested date change are refused, with missing documentation listed and the original date unchanged.
- A PHI-pattern message is rejected or redacted, and the log contains the case id and source ids only.
- A non-synthetic case is rejected and the message text is not echoed.
- The decision-support sentence is on the success body, the refusal body, and the 429 body.
- Prompt fixtures and response bodies contain no CDT or CPT descriptor text and no CPT code.
- The 21st request in a minute returns 429 without a model call.
- The key is read from the environment name only. A test looks for the value in the repo, the HTML, and the log and finds none.

## Planned hosting

The pilot layout is in `docs/deploy-pilot.md`. The FastAPI app and the server-rendered UI run together as one Cloudflare Container. Postgres with pgvector runs on Neon and is reached directly, not through Hyperdrive. `OPENAI_API_KEY` stays unset for the pilot. Chat is not built. This repository does not deploy the service and does not store credentials.
