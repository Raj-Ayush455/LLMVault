import hmac
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.models import ApiKey, UsageRecord, utc_now
from app.providers import ProviderFailure, configured_providers, create_chat_completion
from app.security import generate_virtual_key, hash_virtual_key

app = FastAPI(
    title="RentOk Minimal LLM Gateway",
    version="1.0.0",
    description="A small LLM gateway with virtual keys, request quotas, usage logs, and one fallback provider.",
)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    request_limit: int = Field(gt=0, le=1_000_000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = Field(min_length=1, max_length=200)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False


def require_admin(authorization: str | None = Header(default=None)) -> None:
    configured = settings.admin_api_key
    if not configured:
        raise HTTPException(status_code=503, detail={"error": {"code": "admin_not_configured"}})
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail={"error": {"code": "invalid_admin_key"}})


def authenticate_virtual_key(authorization: str | None, db: Session) -> ApiKey:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": {"code": "missing_api_key"}})
    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key:
        raise HTTPException(status_code=401, detail={"error": {"code": "missing_api_key"}})
    key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_virtual_key(raw_key)))
    if key is None or not key.is_active:
        raise HTTPException(status_code=401, detail={"error": {"code": "invalid_api_key"}})
    return key


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    rates = settings.pricing.get(model)
    if rates is None or input_tokens is None or output_tokens is None:
        return None
    value = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    return round(value, 10)


def get_token_counts(data: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    return (
        prompt if isinstance(prompt, int) and prompt >= 0 else None,
        completion if isinstance(completion, int) and completion >= 0 else None,
    )


def finish_usage(
    db: Session,
    usage_id: str,
    *,
    status: str,
    http_status: int | None,
    provider: str | None = None,
    actual_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error_code: str | None = None,
) -> None:
    cost = estimate_cost(actual_model, input_tokens, output_tokens) if actual_model else None
    db.execute(
        update(UsageRecord)
        .where(UsageRecord.id == usage_id)
        .values(
            status=status,
            http_status=http_status,
            provider=provider,
            actual_model=actual_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=cost,
            error_code=error_code,
        )
    )
    db.commit()


def usage_dict(record: UsageRecord) -> dict[str, Any]:
    created_at = record.created_at
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=utc_now().tzinfo)
    return {
        "id": record.id,
        "key_id": record.api_key_id,
        "requested_model": record.requested_model,
        "actual_model": record.actual_model,
        "provider": record.provider,
        "status": record.status,
        "http_status": record.http_status,
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "estimated_cost_usd": record.estimated_cost_usd,
        "error_code": record.error_code,
        "created_at": created_at.isoformat() if created_at else None,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "RentOk Minimal LLM Gateway",
        "status": "ok",
        "health": "/health",
        "api_docs": "/docs",
        "chat_completions": "/v1/chat/completions",
    }


@app.post("/admin/keys", dependencies=[Depends(require_admin)], status_code=201)
def create_key(body: KeyCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    raw_key = generate_virtual_key()
    key = ApiKey(
        id=str(uuid.uuid4()),
        name=body.name,
        key_hash=hash_virtual_key(raw_key),
        key_prefix=raw_key[:11],
        request_limit=body.request_limit,
        requests_used=0,
        is_active=True,
        created_at=utc_now(),
    )
    db.add(key)
    db.commit()
    return {
        "id": key.id,
        "name": key.name,
        "api_key": raw_key,
        "key_prefix": key.key_prefix,
        "request_limit": key.request_limit,
        "requests_used": key.requests_used,
        "created_at": key.created_at.isoformat(),
        "notice": "The full API key is shown once. Store it securely; the gateway keeps only its hash.",
    }


@app.get("/admin/keys", dependencies=[Depends(require_admin)])
def list_keys(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    keys = db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all()
    return [
        {
            "id": key.id,
            "name": key.name,
            "key_prefix": key.key_prefix,
            "request_limit": key.request_limit,
            "requests_used": key.requests_used,
            "remaining_requests": max(0, key.request_limit - key.requests_used),
            "is_active": key.is_active,
            "created_at": key.created_at.isoformat(),
        }
        for key in keys
    ]


@app.post("/admin/keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
def revoke_key(key_id: str, db: Session = Depends(get_db)) -> dict[str, str]:
    result = db.execute(update(ApiKey).where(ApiKey.id == key_id).values(is_active=False))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail={"error": {"code": "key_not_found"}})
    db.commit()
    return {"id": key_id, "status": "revoked"}


@app.get("/admin/usage", dependencies=[Depends(require_admin)])
def list_usage(
    key_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    filters = []
    if key_id:
        filters.append(UsageRecord.api_key_id == key_id)
    totals = db.execute(
        select(
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0.0),
            func.coalesce(func.sum(case((UsageRecord.estimated_cost_usd.is_(None), 1), else_=0)), 0),
        ).where(*filters)
    ).one()
    query = select(UsageRecord).where(*filters)
    records = db.scalars(query.order_by(UsageRecord.created_at.desc()).limit(limit)).all()
    costs = [record.estimated_cost_usd for record in records]
    known_cost = round(sum(cost for cost in costs if cost is not None), 10)
    return {
        "count": len(records),
        "lifetime_request_count": totals[0],
        "lifetime_estimated_cost_usd": round(float(totals[1]), 10),
        "lifetime_unpriced_records": totals[2],
        "estimated_cost_usd_for_returned_records": known_cost,
        "records": [usage_dict(record) for record in records],
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    body: ChatRequest,
    response: Response,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Any:
    if body.stream:
        raise HTTPException(status_code=400, detail={"error": {"code": "streaming_not_supported"}})
    if body.model not in settings.allowed_model_names:
        raise HTTPException(status_code=400, detail={"error": {"code": "model_not_allowed"}})

    key = authenticate_virtual_key(authorization, db)
    db.commit()
    usage_id = str(uuid.uuid4())
    with db.begin():
        reservation = db.execute(
            update(ApiKey)
            .where(
                ApiKey.id == key.id,
                ApiKey.is_active.is_(True),
                ApiKey.requests_used < ApiKey.request_limit,
            )
            .values(requests_used=ApiKey.requests_used + 1)
        )
        if reservation.rowcount != 1:
            raise HTTPException(status_code=402, detail={"error": {"code": "request_budget_exhausted"}})
        db.add(
            UsageRecord(
                id=usage_id,
                api_key_id=key.id,
                requested_model=body.model,
                status="pending",
                created_at=utc_now(),
            )
        )

    providers = configured_providers()
    if not providers:
        finish_usage(db, usage_id, status="failed", http_status=503, error_code="provider_not_configured")
        raise HTTPException(status_code=503, detail={"error": {"code": "provider_not_configured"}})

    payload = body.model_dump(exclude_none=True)
    last_failure: ProviderFailure | None = None
    for index, provider in enumerate(providers[:2]):
        try:
            data, status_code = await create_chat_completion(provider, payload)
            actual_model = data.get("model") if isinstance(data.get("model"), str) else (
                provider.model_override or body.model
            )
            input_tokens, output_tokens = get_token_counts(data)
            finish_usage(
                db,
                usage_id,
                status="succeeded",
                http_status=status_code,
                provider=provider.name,
                actual_model=actual_model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            response.status_code = status_code
            return data
        except ProviderFailure as exc:
            last_failure = exc
            is_last_provider = index == min(len(providers), 2) - 1
            if not exc.retryable or is_last_provider:
                break

    assert last_failure is not None
    failure_status = 504 if last_failure.error_code == "provider_timeout" else 502
    finish_usage(
        db,
        usage_id,
        status="failed",
        http_status=failure_status,
        error_code=last_failure.error_code,
    )
    raise HTTPException(
        status_code=failure_status,
        detail={"error": {"code": last_failure.error_code}},
    )
