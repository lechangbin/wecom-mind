import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.session import get_session
from app.wecom.jobs import (
    list_message_ingestion_jobs,
    process_message_ingestion_job,
    process_pending_message_ingestion_jobs,
)
from app.wecom.schemas import MessageIngestRequest
from app.wecom.services import ingest_message, record_mcp_callback

router = APIRouter(prefix="/api/wecom", tags=["wecom"])


class RunPendingIngestionJobsRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


@router.get("/callbacks/mcp")
async def verify_mcp_callback_url(request: Request):
    query = {key: value for key, value in request.query_params.items()}
    verification = request.app.state.wecom_callback_verifier.verify_url(query)

    if not verification.valid:
        raise AppError(
            ErrorCode.FORBIDDEN,
            verification.error_message or "Invalid WeCom callback signature",
        )

    return Response(content=verification.plaintext or "", media_type="text/plain")


@router.post("/callbacks/mcp")
async def receive_mcp_callback(request: Request, session: Session = Depends(get_session)):
    raw_body = await request.body()
    query = {key: value for key, value in request.query_params.items()}
    verification = request.app.state.wecom_callback_verifier.verify(query, raw_body)

    if not verification.valid:
        raise AppError(
            ErrorCode.FORBIDDEN,
            verification.error_message or "Invalid WeCom callback signature",
        )

    body_payload = (
        verification.decrypted_payload
        if verification.decrypted_payload is not None
        else _parse_body(raw_body)
    )
    result = record_mcp_callback(
        session,
        query=query,
        body_payload=body_payload,
        raw_body=raw_body,
        signature_valid=True,
    )
    if request.app.state.settings.wecom_mcp_verify_mode == "real":
        return Response(content="success", media_type="text/plain")
    return success_response(result, request_id=get_request_id())


@router.get("/ingestion-jobs")
def list_wecom_ingestion_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    result = list_message_ingestion_jobs(
        session,
        status=status,
        job_type=job_type,
        limit=limit,
        offset=offset,
    )
    return success_response(result, request_id=get_request_id())


@router.post("/ingestion-jobs/run")
def run_pending_wecom_ingestion_jobs(
    request: Request,
    payload: RunPendingIngestionJobsRequest | None = None,
    session: Session = Depends(get_session),
):
    payload = payload or RunPendingIngestionJobsRequest()
    result = process_pending_message_ingestion_jobs(
        session,
        request.app.state.settings,
        limit=payload.limit,
        job_type="normalize",
    )
    return success_response(result, request_id=get_request_id())


@router.post("/ingestion-jobs/{job_id}/run")
def run_wecom_ingestion_job(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    result = process_message_ingestion_job(
        session,
        job_id=job_id,
        settings=request.app.state.settings,
    )
    return success_response(result, request_id=get_request_id())


@router.post("/messages/ingest")
def ingest_wecom_message(
    payload: MessageIngestRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    result = ingest_message(
        session,
        payload=payload,
        settings=request.app.state.settings,
    )
    return success_response(result, request_id=get_request_id())


def _parse_body(raw_body: bytes) -> Any:
    if not raw_body:
        return {}

    text = raw_body.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw_text": text}
