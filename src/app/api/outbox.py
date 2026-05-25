from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_id
from app.core.responses import success_response
from app.db.models import OutboxMessage
from app.db.session import get_session
from app.outbound.schemas import OutboxCreateRequest
from app.outbound.services import (
    create_outbox_message,
    get_outbox_message,
    outbox_to_dict,
    send_outbox_message,
)

router = APIRouter(prefix="/api/outbox-messages", tags=["outbox"])


@router.post("")
def create_outbox(
    payload: OutboxCreateRequest,
    session: Session = Depends(get_session),
):
    outbox, duplicated = create_outbox_message(session, payload)
    session.commit()
    return success_response(outbox_to_dict(outbox, duplicated=duplicated), request_id=get_request_id())


@router.get("")
def list_outbox_messages(
    status: str | None = Query(default=None),
    chatid: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    statement = select(OutboxMessage)
    count_statement = select(func.count()).select_from(OutboxMessage)
    filters = []
    if status:
        filters.append(OutboxMessage.status == status)
    if chatid:
        filters.append(OutboxMessage.chatid == chatid)
    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = session.scalar(count_statement)
    items = session.scalars(
        statement.order_by(OutboxMessage.created_at.desc(), OutboxMessage.id.desc())
    ).all()
    return success_response(
        {
            "total": total,
            "items": [outbox_to_dict(item) for item in items],
        },
        request_id=get_request_id(),
    )


@router.get("/{outbox_id}")
def get_outbox(
    outbox_id: str,
    session: Session = Depends(get_session),
):
    outbox = get_outbox_message(session, outbox_id)
    return success_response(outbox_to_dict(outbox), request_id=get_request_id())


@router.post("/{outbox_id}/send")
def send_outbox(
    outbox_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    outbox, duplicated = send_outbox_message(
        session,
        outbox_identifier=outbox_id,
        sender=request.app.state.wecom_message_sender,
    )
    session.commit()
    return success_response(outbox_to_dict(outbox, duplicated=duplicated), request_id=get_request_id())
