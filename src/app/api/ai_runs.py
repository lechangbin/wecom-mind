from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ErrorCode
from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.models import AiRun
from app.db.session import get_session
from app.dify.services import ai_run_to_dict

router = APIRouter(prefix="/api/ai-runs", tags=["ai-runs"])


@router.get("")
def list_ai_runs(
    workflow_code: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
):
    filters = []
    if workflow_code:
        filters.append(AiRun.workflow_code == workflow_code)
    if status:
        filters.append(AiRun.status == status)

    statement = select(AiRun)
    count_statement = select(func.count()).select_from(AiRun)
    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = session.scalar(count_statement)
    runs = session.scalars(
        statement.order_by(AiRun.created_at.desc(), AiRun.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return success_response(
        {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [ai_run_to_dict(run) for run in runs],
        },
        request_id=get_request_id(),
    )


@router.get("/{run_id}")
def get_ai_run(run_id: str, session: Session = Depends(get_session)):
    conditions = [AiRun.run_id == run_id]
    if run_id.isdigit():
        conditions.append(AiRun.id == int(run_id))

    run = session.scalar(select(AiRun).where(or_(*conditions)))
    if not run:
        raise AppError(ErrorCode.NOT_FOUND, f"AI run not found: {run_id}")

    return success_response(ai_run_to_dict(run), request_id=get_request_id())
