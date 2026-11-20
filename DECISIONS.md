# Engineering decisions

## What was built

This is a small HTTP gateway for OpenAI-compatible non-streaming chat completion APIs. Callers authenticate with gateway-issued keys, each with an exact request-count quota. The gateway records request outcome, provider, model, token counts, and an estimated USD cost when configured with model rates. The selected deployment target is Railway with a PostgreSQL service for durable keys and usage.

One request travels from the caller to the gateway, through virtual-key authentication and a conditional quota reservation, then to the primary provider. The usage row is finalized from provider-reported usage and the response is returned. A timeout, network failure, or transient provider status can cause one configured fallback attempt.

```text
client -> gateway -> virtual key + atomic quota reservation -> primary provider
                                      |                           |
                                      +-> usage row <--- response / fallback
```

## Important decisions and tradeoffs

1. **Python, FastAPI, and SQLAlchemy.** This keeps the HTTP boundary readable and supports both local SQLite and deployed PostgreSQL. I chose a short dependency list and did not add an ORM migration framework; `create_all` is enough for this first schema but is not an upgrade strategy for a long-lived production service.
2. **Request-count quota.** A request count is exact and can be enforced atomically before provider spend. A token or currency ceiling would require estimating/reserving variable usage before the call, then settling actual usage afterward. The tradeoff is that a request quota does not guarantee a maximum bill; token counts and estimated cost are still logged to make that limitation visible.
3. **Atomic reservation before the external call.** One conditional `UPDATE` increments the counter only while `requests_used < request_limit`. The update and pending usage insert commit together. Concurrent callers therefore compete for the final slot in the database. If the provider later fails, that slot remains consumed: releasing it could make actual spend exceed the configured count under retry or crash conditions.
4. **Provider-reported usage and explicit pricing map.** Tokens come from the provider response. Costs are estimates computed with operator-configured USD-per-million input/output rates. Missing usage or missing pricing produces a `null` cost, not a fabricated zero.
5. **One fallback, limited to transient failures.** Timeouts, network failures, 408, 429, and selected 5xx responses can fall through once. Authentication/configuration errors and other 4xx responses stop immediately, since retrying another provider can conceal a broken request or credentials. The fallback is not retried again.
6. **Railway with PostgreSQL.** The Dockerfile is the deployment entrypoint, and persistent state lives in a PostgreSQL service. SQLite remains the local development default; deployment must set `DATABASE_URL` to Railway's PostgreSQL connection string.

## Why enforce budgets at the gateway?

Callers are not trusted to count their own use: a caller can retry, have multiple workers, or make a bookkeeping mistake. The gateway is the shared point that authenticates each key and atomically reserves its quota before calling a provider. This makes the limit a property of the service rather than a convention clients have to follow.

## Concurrency and near-exhausted keys

The database conditional update is the enforcement point. If two calls race for one remaining request, only one update can match the `requests_used < request_limit` predicate after the database serializes the writes. The other gets HTTP 402. The request counter and pending usage record are committed in one transaction. A crash after that commit may leave a pending row and consume the slot; an operator can see that incomplete record.

## What was deliberately not built

- Streaming, because token accounting and fallback after a partial response are more complex.
- A frontend, multi-tenant auth, time-window rate limiting, prompt/response storage, and model fine-tuning.
- Semantic caching, routing, and local-model integration. These are stretch ideas, and the core gateway should work first.
- A hard spend cap. This version enforces the simplest exact budget allowed in the prompt and exposes cost estimates separately.
- Automated schema migrations. A first deployment creates tables on startup; schema evolution would need migrations before this is used as a maintained service.
- An automated test suite in this initial implementation pass. This leaves the concurrency and fallback paths unverified; they are the first cases to cover if extending the submission.

## Least-certain decision

The least-certain choice is the request-count cap. It is straightforward to prove correct and is explicitly allowed by the assignment, but it is less directly useful to a team asking “how much did this key spend?” The usage endpoint provides an estimated spend view, while only a token/currency reservation design could make spend itself the hard cap. With another week, I would add reservation/settlement semantics, provider-specific pricing updates, migration tooling, and tests for simultaneous requests and provider failover before claiming a hard spend ceiling.

## Where it breaks

The gateway depends on the provider matching the configured OpenAI-compatible API shape and returning token usage. A provider with different payload semantics, missing usage, or a changed model ID may need an adapter or pricing-map update. A process crash after reservation produces a visible `pending` row. Deployment on ephemeral local storage would lose keys and usage; production must use persistent PostgreSQL.

