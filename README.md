# RentOk Minimal LLM Gateway

A small FastAPI service that sits between callers and an OpenAI-compatible LLM provider. It issues virtual API keys, enforces an exact per-key request quota, records provider usage, estimates cost when a price table is configured, and can fail over once to a second provider.

## What is implemented

- `POST /v1/chat/completions` forwards non-streaming chat completion requests.
- `POST /admin/keys` issues a virtual key and returns the secret only once. The database stores its SHA-256 hash, not the raw key.
- `GET /admin/keys` lists key metadata and remaining quota. `POST /admin/keys/{id}/revoke` revokes a key.
- `GET /admin/usage` returns recent usage and a cost sum for the returned records; add `?key_id=<id>` to filter.
- Request quota reservation is one conditional database update. The quota and a pending usage row are committed together before the provider call, so simultaneous requests cannot both spend the final slot.
- One fallback provider is attempted only for timeouts/network failures and transient HTTP statuses (408, 429, 5xx). Provider authentication and other 4xx errors do not trigger fallback.
- The service does not store request or response text. Configure model pricing to get estimated USD cost; unpriced usage remains `null` rather than being reported as zero.

See [DECISIONS.md](DECISIONS.md) for tradeoffs and [AI-LOG.md](AI-LOG.md) for AI use and review notes.

## Run locally

Requires Python 3.12 or newer.

1. Run `uv sync` to create the virtual environment and install the dependencies from `pyproject.toml`.
2. Copy `.env.example` to `.env` and set `ADMIN_API_KEY`, `PRIMARY_BASE_URL`, and `PRIMARY_API_KEY`. Never commit `.env`.
3. Set `ALLOWED_MODELS` to the comma-separated model IDs you intend callers to use. The default is `gpt-4o-mini`.
4. Optionally configure `MODEL_PRICING_USD_PER_1M` with the provider's current input/output rates, in USD per million tokens. For example:

   ```text
   {"gpt-4o-mini":{"input":0.15,"output":0.60}}
   ```

5. Start the service with `uvicorn app.main:app --reload`.

The local default database is `./gateway.db` (SQLite). For a deployed service, set `DATABASE_URL` to a persistent PostgreSQL database URL. Tables are created on startup; this take-home does not include schema migrations.

## Example request lifecycle

Set the admin secret in your shell to the same value as `ADMIN_API_KEY` in `.env`, then create a virtual key:

```powershell
$env:ADMIN_API_KEY = "your-admin-secret"
$key = Invoke-RestMethod -Method Post -Uri http://localhost:8000/admin/keys `
  -Headers @{ Authorization = "Bearer $env:ADMIN_API_KEY" } `
  -ContentType "application/json" `
  -Body '{"name":"demo","request_limit":10}'
$key.api_key
```

Save the returned `api_key` securely; it will not be shown again. Call the gateway with it:

```powershell
$virtualKey = $key.api_key
$payload = @{
  model = "gpt-4o-mini"
  messages = @(@{ role = "user"; content = "Say hello in one sentence." })
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post -Uri http://localhost:8000/v1/chat/completions `
  -Headers @{ Authorization = "Bearer $virtualKey" } `
  -ContentType "application/json" -Body $payload
```

Query usage totals and records:

```powershell
Invoke-RestMethod -Uri http://localhost:8000/admin/usage `
  -Headers @{ Authorization = "Bearer $env:ADMIN_API_KEY" }
```

The quota is consumed when an authenticated, allowed request is admitted, including requests that later fail at the provider. Exhaustion returns HTTP 402. Streaming requests return HTTP 400.

## Provider and fallback configuration

The primary and fallback endpoints must implement the OpenAI chat completions request/response shape. `PRIMARY_BASE_URL` and `FALLBACK_BASE_URL` are base URLs ending at the API version prefix (for example `/v1`), not full completion URLs. Set all of `FALLBACK_BASE_URL`, `FALLBACK_API_KEY`, and, if its model name differs, `FALLBACK_MODEL` to enable fallback. The caller's requested model must be listed in `ALLOWED_MODELS`; the fallback model is configured by the operator.

Provider credentials are read from environment variables and are sent only from the gateway to the provider. Keep them in the hosting platform's secret manager. Do not put them in source control or in caller requests.

## Deploy

Use a persistent PostgreSQL service and set these environment variables in the host's secret/configuration settings:

- `DATABASE_URL` - persistent PostgreSQL URL
- `ADMIN_API_KEY` - long random secret
- `PRIMARY_BASE_URL`, `PRIMARY_API_KEY`, and `ALLOWED_MODELS`
- Optional fallback values and `MODEL_PRICING_USD_PER_1M`

Configure the host's health check to `GET /health`. After deploy, create a virtual key through `POST /admin/keys`, make a sample call, and submit the resulting HTTPS base URL plus the sample request. A live URL is not included in this source workspace: deployment requires a hosting account, database, and real provider credentials.

### Zero-cost demo path: Railway Free

Railway currently offers a $0 Free plan with a monthly usage allowance and no credit card requirement. It can run up to three services in one project, including a single-node PostgreSQL service. Free-plan services sleep when idle and wake on requests; if the allowance is exhausted, services stop until the next billing cycle or an upgrade. Review the current [Railway Free plan limits](https://docs.railway.com/pricing/free-plan) before deploying.

1. Push this source to a GitHub repository. The `.gitignore` excludes `.env` and local database files.
2. Create a Railway project from that repository. Railway will build the included `Dockerfile`.
3. Add a PostgreSQL service to the same project. Set the app's `DATABASE_URL` to the Postgres service's generated `DATABASE_URL` reference.
4. Set `ADMIN_API_KEY`, `PRIMARY_BASE_URL`, `PRIMARY_API_KEY`, `ALLOWED_MODELS`, and optionally the fallback and pricing values in the app service variables. Do not place secrets in the repository.
5. Set the app health check to `/health`, generate a public domain, and verify the first `/health` request and one provider call after deployment.

New Railway accounts start with a trial; accounts on its limited trial may have restricted outbound networking. Complete Railway's account/GitHub verification if the service cannot reach the configured LLM provider. The free monthly allowance is small, so a handful of assessment requests should be the intended workload; monitor Railway usage and note that the LLM provider may bill separately.

## Known limits

- The enforced budget is request count, not a hard currency or token-spend ceiling. Provider token usage and estimated USD cost are logged separately.
- Cost estimates rely on an operator-maintained pricing map and provider-reported token counts. They are not billing guarantees.
- The gateway supports non-streaming chat completions only.
- No retry is made against the same provider. A transient primary failure can trigger one fallback attempt.
- SQLite is for local development; production deployment should use persistent PostgreSQL.
- There is no rate limiting by time window, multi-tenant admin login, prompt storage, frontend, or schema migration system.
- A process crash after quota reservation can leave a `pending` usage row while still consuming the request slot. This keeps the quota conservative and makes interrupted work visible.

