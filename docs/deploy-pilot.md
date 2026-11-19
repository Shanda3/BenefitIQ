# Deploy the private pilot

This is the runbook for one dental office. The app is decision support for a human biller. It is not a place to type patient details. Do not put real patient information in the database, the logs, the browser cases, or this repository.

Nothing in this repository deploys the app or stores a credential. `make deploy` reads values from the environment only.

Local development is unchanged. `docker compose up --build` still starts Postgres and the app on port 8000 and loads the fictional stub corpus.

## What runs where

The pages are rendered by the FastAPI app. They are not a separate static site. The same container serves the pages and the API. Cloudflare Pages would mean splitting the templates away from the routes, which this code does not do.

One Worker accepts the request and forwards it to one container named `pilot`. The container sleeps after 15 minutes with no traffic. The first page after a sleep can take a minute or two while the process starts.

Postgres with pgvector runs on Neon. The container connects to Neon directly with TLS. It does not use Cloudflare Hyperdrive.

Hyperdrive is a pool for many short-lived Workers. This pilot is one long-lived container with its own small connection pool. Neon already includes a pooled connection. Cloudflare's Neon notes say not to put Hyperdrive in front of that pool. Migrations also create the `vector` extension and the search index, which need a direct connection. A direct TLS connection is the simpler path and keeps vector search on ordinary Postgres.

## The embedding model and the image

Search embeds a question with the local model `BAAI/bge-small-en-v1.5` and compares it with vectors already stored in Neon. Those two steps must use the same model.

- `make load-pilot` computes the library vectors on the machine that loads Neon and writes them into the database. The container does not re-embed the library on boot.
- The Docker image downloads that model once, during the image build, into `/opt/fastembed`. The running container does not fetch it.

Cloudflare limits the image to the disk size of the instance. The `basic` instance is 1 GiB of memory and 4 GB of disk. The Worker starts that size. A local image built on 19 November 2026 used 739 MB of disk (`docker images` disk usage; the compressed content size was 208 MB). That fits the 4 GB disk. If the container is killed while the model loads, change `instance: "basic"` to `instance: "standard-1"` in `worker/index.js` and deploy again. `standard-1` is 4 GiB of memory and 8 GB of disk. Memory is billed for the size you select, for the time the container is awake.

Leave `OPENAI_API_KEY` unset. Chat is not built. The panel stays off whether or not a key is present. A key would also switch embeddings to another provider, and the stored vectors would no longer match. Do not set it for this pilot.

## Values you provide

Set these in the shell before `make deploy`. Do not write them into a file in this repository.

Required:

| Name | What it is |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | API token that can edit Workers and Containers on your account. Used only by the deploy command. It is not put in the container. |
| `CLOUDFLARE_ACCOUNT_ID` | The account id from the Workers dashboard. |
| `DATABASE_URL` | Neon connection string for the running app. Use the pooled host and `sslmode=require`. The command stores it as a secret. |
| `ADMIN_EMAILS` | Your email. Separate more than one with commas. These people can open Access requests and the rest of the app before anyone else is approved. |
| `CLOUDFLARE_ACCESS_TEAM_DOMAIN` | Your Zero Trust team host, such as `your-team.cloudflareaccess.com`. No `https://`. |
| `CLOUDFLARE_ACCESS_AUD` | The Application Audience tag from the protected Access application. |
| `BENEFITIQ_SESSION_SECRET` | A long random string for the office cookie. Create it with `openssl rand -hex 32` and keep it. |

The load step uses a direct Neon connection string, not the pooled one. Export that as `DATABASE_URL` only while you run `make load-pilot`, then switch `DATABASE_URL` to the pooled string before `make deploy`.

Optional:

| Name | What it is |
| --- | --- |
| `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY` | Both, or neither. They add the Cloudflare person-check on the request form. Rate limiting still runs without them. |
| `ACCESS_NOTIFY_WEBHOOK_URL` | A URL that accepts a JSON POST when someone asks for access. Leave it unset to use the count on the Access requests page only. |
| `OPENAI_API_KEY` | Leave unset for this pilot. |

## 1. Create the Neon database

1. Create a Neon project. The Free plan is enough to start. pgvector is included.
2. Copy the **direct** connection string (the host that is not the pooler).
3. On a machine with this repository and Python dependencies installed, from the repository root:

```bash
export DATABASE_URL="postgresql://…direct-host…/neondb?sslmode=require"
make load-pilot
```

The command creates the tables, including the access-request table, loads the fictional stub corpus, the office sources, the payer cards, and the payer policy text, and stores embeddings. It prints the database host and name, not the password. Re-running it skips sources that are already loaded.

Use the direct string for this step. Use the pooled string for the running app.

A later run of `make load-pilot` inserts a source id that is not in the database yet and skips a source whose file has not changed. It stops if a source id is already stored and the file hash changed. New office files use new source ids, so this command can load them without editing the sources that are already stored.

Sources added for the additional procedures:

| Source id | File | Effective date |
| --- | --- | --- |
| `cms-dental-exceptions` | `corpus/office/cms-dental-exceptions.md` | 2025-07-01 |
| `oid-36-6060-6` | `corpus/office/oid-36-6060-6.md` | 2010-01-12 |

On the machine that loads Neon, from the repository root, with the direct connection string:

```bash
export DATABASE_URL="postgresql://…direct-host…/neondb?sslmode=require"
make load-pilot
```

That command does not deploy the app. After it finishes, the new source ids are in the database. Sources that were already loaded are skipped. Payer policy text that was already current is left as it is. Humana files stay pending and are not downloaded.

## 2. Deploy the container

The public address is `https://benefitiq.devyo.app`. `wrangler.jsonc` keeps that address on every deploy: `workers_dev` is false, `preview_urls` is false, and the Worker route is the custom domain `benefitiq.devyo.app`. A redeploy does not publish a `workers.dev` hostname or a preview URL.

Export the required values. `DATABASE_URL` here is the **pooled** Neon string. Then:

```bash
make deploy
```

The command stops if a required name is missing. It uploads the application secrets, builds the image, and deploys the Worker. It does not print secret values. This repository does not run that command for you.

Docker must be running locally. The image is built for `linux/amd64`.

After a deploy, open `https://benefitiq.devyo.app/healthz`. A healthy database returns `{"status":"ok"}`. If the database setting is missing or the database is down, it returns `{"status":"unavailable"}` and does not describe the connection. The rest of the app stays closed until Access is in place and a person is approved. The custom domain has to be on the Cloudflare account before the route will answer. This runbook does not create that DNS record.

## 3. Turn on Cloudflare Access

The app checks a signed Access token on every protected page and then checks the approved list in the database. Signing in is not the same as being approved.

This is the more robust way to grant access. The other way is a Cloudflare API token that adds the email to an Access policy or group when you click Approve. That token can also lock the office out or open the policy to everyone, and a revoke waits until Access publishes the new rule. This app does not call that API, so the deploy does not need a token that can edit Access. Cloudflare Access still makes every person prove they hold the email, with a one-time PIN. The app then reads the approved row on every request. Approve, deny, and revoke change that row, and each action is written to the log as the action, the admin email, the staff email, and the time. A revoke applies on the next page even while the Access login is still valid. An email that is not approved, and is not in `ADMIN_EMAILS`, is refused by the app. A missing or invalid token is refused as well. The email header Access adds is not trusted on its own.

Create two self-hosted applications on `benefitiq.devyo.app`. The more specific paths must win over the catch-all. Do not add an email allowlist to either policy.

1. **Public paths.** Paths `/request-access`, `/healthz`, and `/static`. Policy action **Bypass**, include everyone. These are the only public paths. `/request-access` is the only public page besides the health check. `/static` is the stylesheet and script those pages use.
2. **The app.** `benefitiq.devyo.app` with no extra path. Policy action **Allow**, include everyone, login method **One-time PIN**. Copy this application's Audience (AUD) tag into `CLOUDFLARE_ACCESS_AUD`.

If you created the Access application after the first deploy, put the audience tag and the team domain in the environment and run `make deploy` again so the container receives them.

Anyone can request a one-time code. The app still blocks an email that is not approved and is not in `ADMIN_EMAILS`.

### How a person gets in

1. They open `/request-access` and enter their name, work email, office name, and role. The form does not ask for patient information.
2. The request is stored as waiting. It holds only those fields and the times.
3. You open the app, sign in with the email in `ADMIN_EMAILS`, and go to **Access requests**. The page says how many people are waiting. That count is the notification. If `ACCESS_NOTIFY_WEBHOOK_URL` is set, a JSON POST is also sent. If that call fails, the request is still saved.
4. **Approve** lets that email in on the next page. **Deny** keeps them out. **Revoke** removes someone who was approved. Revoke takes effect on their next request, even if their Access login has not expired, because the app reads the database every time.
5. They open the office link and sign in with the same work email.

Synthetic demo cases stay on the Cases page for approved people.

The office check itself is still kept in the browser. It is not written to the database. Logs record case ids, source ids, and access decisions (the action, the admin email, the staff email, and the time). They do not record notes, names from a rejected entry, or secret values.

## 4. Take it offline or roll back

To take the pilot offline immediately, add a **Block** policy above the Allow policy on the Access application, or remove the public hostname. People cannot open the app. The Neon data stays.

To turn the Worker off, disable it in the Workers dashboard. `npx wrangler delete` removes the Worker. Use that only when you want it gone. It does not delete the Neon project.

To roll back the program, check out the previous git revision and run `make deploy` again. That builds the image from that revision and ships it. Do not edit a secret into the revision.

To wipe the database, delete or reset the project in the Neon console. The app does not store patient cases there. It does store the policy library and the access requests.

## What it costs

Prices below are copied from the providers' pages. This note does not add them up into a bill. Check the pages again before you rely on them. A single office that is idle most of the day is the workload these pages describe as small: one container that sleeps, and one Neon project that scales to zero.

Read on 19 November 2026.

**Cloudflare Containers**, [Containers pricing](https://developers.cloudflare.com/containers/platform/pricing/) (page metadata date modified 13 November 2026):

- Containers are part of the Workers Paid plan, listed there as $5 USD per month.
- Included each month on that plan: 25 GiB-hours of memory, 375 vCPU-minutes, and 200 GB-hours of disk. Additional use is $0.0000025 per GiB-second of memory, $0.000020 per vCPU-second, and $0.00000007 per GB-second of disk.
- You are billed while the container is running. Billing stops when it sleeps. Memory and disk use the size you selected. CPU is billed on active use.
- The `basic` size used here is 1/4 vCPU, 1 GiB memory, and 4 GB disk. `standard-1` is 1/2 vCPU, 4 GiB memory, and 8 GB disk. See [Limits and instance types](https://developers.cloudflare.com/containers/platform/limits/).
- Egress to North America and Europe is $0.025 per GB after 1 TB included.
- The Worker and the container's Durable Object are billed as well. The containers page points at [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) and [Durable Objects pricing](https://developers.cloudflare.com/durable-objects/platform/pricing/).

**Neon**, [Neon pricing](https://neon.com/pricing) (page text: last updated on 4 November 2026):

- Free is $0 with no credit card: 100 projects, 100 CU-hours per project each month, 1 GB of storage per project, and compute that scales to zero after 5 minutes of idle time. The page's own example says 0.25 CU for about 12 hours a day stays inside that 100 CU-hour allowance.
- Launch is usage-based with no monthly minimum: $0.106 per CU-hour and $0.35 per GB-month. The page shows a typical spend of $15 per month for its "intermittent load, 1 GB" example. That figure is Neon's, for 140 CU-hours and 1 GB-month, not a quote for this office.
- Scale lists HIPAA. This pilot is not a HIPAA hosting setup. It is built so patient details are not entered or stored. Free or Launch matches that use. Do not treat either plan as a business associate agreement.
- pgvector is one of the extensions Neon includes on every plan.

Turnstile's person-check and Access logins are separate Cloudflare products. Look at the current Access and Turnstile prices in the dashboard before you turn them on. This runbook does not copy those prices.
