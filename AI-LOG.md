# AI use log

## Tools and use

- ChatGPT/Codex (GPT-6) was used to inspect the assignment, propose a scope, and draft the application code, configuration, README, and engineering decisions.
- Web search was used to check Vercel's current PostgreSQL offering and Railway's Free plan. Vercel Marketplace integrations provide external databases; the user selected Railway as the path to continue.
- The implementation follows the selected defaults: Python/FastAPI, request-count quotas, non-streaming requests, persistent database support, one primary provider, and one optional fallback.
- `uv` created a Python 3.12 virtual environment, but Windows Application Control blocked execution of its interpreter. Dependencies were installed with `uv` into an ignored workspace-local target and the app was started using the already-installed system Python 3.15.2. This is a local environment constraint, not a production deployment configuration.
- No other AI tools or models were used for this initial draft.

## One misleading AI moment

The assistant asked whether there was an existing repository to follow. That wording could imply a codebase had already been found. The workspace contained only the assignment PDF; this was checked and clarified before implementation. No repository-specific design assumptions were used.

## Review and overrides

The user authorized the recommended defaults and asked for the assignment to be created. The AI generated most of this first draft. The user has not yet reviewed the implementation or supplied independent corrections, so this log does not claim that a human has validated the security, quota, provider, or deployment behavior. Before submission, the candidate should personally trace the quota update, secret handling, provider failure behavior, and a real request through the code, then update this section with what they changed or rejected.

During code review, I rejected two generated implementation details and corrected them:

1. The first provider-selection draft could have used the fallback as the only configured provider if the primary settings were missing. That makes fallback semantics ambiguous and can hide a missing primary configuration. I changed provider selection to require a configured primary before any request is sent; fallback is strictly secondary.
2. The first quota flow attempted to start a new SQLAlchemy transaction after the key lookup had already opened a read transaction. I added an explicit commit before starting the quota reservation transaction so the two transaction scopes do not overlap.

The user accepted request-count quotas after the limitation that they do not cap rupee spend was explained. That was not an override. This review correction is by the coding agent; the candidate still needs to inspect the code and add their own genuine observation before submission if they make a further change.

## Code areas requiring the candidate's own explanation

- `app/main.py`: atomic request reservation, pending usage row, and quota-exhaustion response.
- `app/providers.py`: transient failure classification and one-time fallback.
- `app/security.py`: one-time virtual key generation and hash-only persistence.
- `app/config.py`: provider allowlist, pricing inputs, and deployment configuration.
- `Dockerfile` and `app/database.py`: Railway's Docker entrypoint and the persistent PostgreSQL requirement.

The candidate should not submit code they cannot explain. Revise or remove anything that does not match their own understanding and final choices.

