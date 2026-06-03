from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def id_column():
    return mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )


class Base(DeclarativeBase):
    pass


class WeComChat(Base):
    __tablename__ = "wecom_chats"

    id: Mapped[int] = id_column()
    chatid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    chattype: Mapped[str] = mapped_column(String(32), default="group", nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="mcp", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class WeComUser(Base):
    __tablename__ = "wecom_users"

    id: Mapped[int] = id_column()
    userid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    alias: Mapped[str | None] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class WeComMcpCallback(Base):
    __tablename__ = "wecom_mcp_callbacks"

    id: Mapped[int] = id_column()
    callback_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(128))
    chatid: Mapped[str | None] = mapped_column(String(128), index=True)
    raw_query: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    raw_body: Mapped[Any] = mapped_column(JSON, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="received", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WeComMcpPullCursor(Base):
    __tablename__ = "wecom_mcp_pull_cursors"
    __table_args__ = (
        UniqueConstraint("chatid", "cursor_type", name="uq_wecom_pull_cursor_chat_type"),
    )

    id: Mapped[int] = id_column()
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cursor_value: Mapped[str | None] = mapped_column(String(512))
    last_pulled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class MessageIngestionJob(Base):
    __tablename__ = "message_ingestion_jobs"

    id: Mapped[int] = id_column()
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chatid: Mapped[str | None] = mapped_column(String(128), index=True)
    callback_id: Mapped[str | None] = mapped_column(String(64), index=True)
    cursor_before: Mapped[str | None] = mapped_column(String(512))
    cursor_after: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageRaw(Base):
    __tablename__ = "messages_raw"
    __table_args__ = (
        UniqueConstraint("source", "external_msgid", name="uq_messages_raw_source_msgid"),
    )

    id: Mapped[int] = id_column()
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_msgid: Mapped[str] = mapped_column(String(255), nullable=False)
    req_id: Mapped[str | None] = mapped_column(String(255))
    chatid: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    userid: Mapped[str | None] = mapped_column(String(128), index=True)
    msgtype: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_chatid_create_time", "chatid", "create_time"),
        Index("ix_messages_userid_create_time", "userid", "create_time"),
        Index("ix_messages_mentioned_bot_create_time", "mentioned_bot", "create_time"),
        Index("ix_messages_msgtype_create_time", "msgtype", "create_time"),
        Index("ix_messages_business_identity_key", "business_identity_key"),
        Index("ix_messages_canonical_message_id", "canonical_message_id"),
        Index(
            "ix_messages_chatid_sender_type_create_time",
            "chatid",
            "sender_type",
            "create_time",
        ),
    )

    id: Mapped[int] = id_column()
    raw_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages_raw.id"),
        unique=True,
        nullable=False,
    )
    external_msgid: Mapped[str] = mapped_column(String(255), nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    chattype: Mapped[str] = mapped_column(String(32), nullable=False)
    userid: Mapped[str | None] = mapped_column(String(128))
    msgtype: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_type: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    bot_role: Mapped[str | None] = mapped_column(String(32))
    content_text: Mapped[str | None] = mapped_column(Text)
    business_identity_key: Mapped[str | None] = mapped_column(String(128))
    canonical_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    normalized_content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    quote_message: Mapped[Any | None] = mapped_column(JSON)
    quote_msgid: Mapped[str | None] = mapped_column(String(255))
    mentioned_bot: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    mentioned_users: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class MentionRequest(Base):
    __tablename__ = "mention_requests"
    __table_args__ = (
        UniqueConstraint(
            "business_identity_key",
            name="uq_mention_requests_business_identity_key",
        ),
        Index("ix_mention_requests_status_updated_at", "status", "updated_at"),
        Index("ix_mention_requests_canonical_message_id", "canonical_message_id"),
        Index("ix_mention_requests_outbox_id", "outbox_id"),
    )

    id: Mapped[int] = id_column()
    request_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    business_identity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    userid: Mapped[str | None] = mapped_column(String(128))
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_msgid: Mapped[str | None] = mapped_column(String(255))
    req_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="claimed", nullable=False)
    owner: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id"))
    ai_run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_runs.id"))
    outbox_id: Mapped[str | None] = mapped_column(String(64))
    reply_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("wecom_reply_sessions.id")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class TriggerRule(Base):
    __tablename__ = "trigger_rules"

    id: Mapped[int] = id_column()
    rule_code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    workflow_code: Mapped[str] = mapped_column(String(128), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class TriggerEvent(Base):
    __tablename__ = "trigger_events"
    __table_args__ = (
        UniqueConstraint("rule_code", "message_id", name="uq_trigger_event_rule_message"),
    )

    id: Mapped[int] = id_column()
    rule_code: Mapped[str] = mapped_column(String(128), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    userid: Mapped[str | None] = mapped_column(String(128))
    workflow_code: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AiWorkflow(Base):
    __tablename__ = "ai_workflows"
    __table_args__ = (
        UniqueConstraint("workflow_code", "version", name="uq_ai_workflow_code_version"),
    )

    id: Mapped[int] = id_column()
    workflow_code: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="dify", nullable=False)
    dify_app_id: Mapped[str | None] = mapped_column(String(255))
    dify_workflow_id: Mapped[str | None] = mapped_column(String(255))
    dify_webhook_url: Mapped[str | None] = mapped_column(String(1024))
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    response_mode: Mapped[str] = mapped_column(String(32), default="blocking", nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class AiRun(Base):
    __tablename__ = "ai_runs"
    __table_args__ = (
        UniqueConstraint(
            "trigger_event_id",
            "workflow_code",
            "workflow_version",
            name="uq_ai_run_trigger_workflow_version",
        ),
    )

    id: Mapped[int] = id_column()
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    workflow_code: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id"))
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_json: Mapped[Any | None] = mapped_column(JSON)
    response_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotReply(Base):
    __tablename__ = "bot_replies"

    id: Mapped[int] = id_column()
    reply_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id"), unique=True, nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    userid: Mapped[str | None] = mapped_column(String(128))
    reply_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    stream_id: Mapped[str | None] = mapped_column(String(128))
    external_msgid: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[int] = id_column()
    outbox_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scene: Mapped[str] = mapped_column(String(32), nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    target_userids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    msgtype: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    external_msgid: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WeComReplySession(Base):
    __tablename__ = "wecom_reply_sessions"
    __table_args__ = (
        Index(
            "ix_wecom_reply_sessions_chat_user_fingerprint",
            "chatid",
            "userid",
            "content_fingerprint",
        ),
        Index("ix_wecom_reply_sessions_outbox_id", "outbox_id"),
        Index("ix_wecom_reply_sessions_mention_request_id", "mention_request_id"),
    )

    id: Mapped[int] = id_column()
    session_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    userid: Mapped[str | None] = mapped_column(String(128))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    mention_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("mention_requests.id")
    )
    source_msgid: Mapped[str | None] = mapped_column(String(255), index=True)
    req_id: Mapped[str | None] = mapped_column(String(255), index=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64))
    frame_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    stream_id: Mapped[str | None] = mapped_column(String(128))
    placeholder_status: Mapped[str] = mapped_column(
        String(32),
        default="sent",
        nullable=False,
    )
    final_status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        nullable=False,
    )
    trigger_event_id: Mapped[int | None] = mapped_column(ForeignKey("trigger_events.id"))
    outbox_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class ScheduledIntent(Base):
    __tablename__ = "scheduled_intents"
    __table_args__ = (
        Index("ix_scheduled_intents_chatid_status", "chatid", "status"),
        UniqueConstraint("idempotency_key", name="uq_scheduled_intent_idempotency_key"),
    )

    id: Mapped[int] = id_column()
    intent_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), nullable=False)
    target_userid: Mapped[str] = mapped_column(String(128), nullable=False)
    intent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_msgids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    suggested_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id"), nullable=False)
    outbox_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ConversationSegment(Base):
    __tablename__ = "conversation_segments"
    __table_args__ = (
        UniqueConstraint(
            "conversation_no",
            "version",
            name="uq_conversation_segment_no_version",
        ),
        UniqueConstraint(
            "chatid",
            "start_message_id",
            "end_message_id",
            name="uq_conversation_segment_chat_start_end",
        ),
    )

    id: Mapped[int] = id_column()
    conversation_no: Mapped[str] = mapped_column(String(64), nullable=False)
    chatid: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    start_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    end_message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    participants: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id"), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        UniqueConstraint("userid", "version", name="uq_user_profile_userid_version"),
        Index("ix_user_profiles_userid_status", "userid", "status"),
    )

    id: Mapped[int] = id_column()
    userid: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id"), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )


class UserProfileFact(Base):
    __tablename__ = "user_profile_facts"
    __table_args__ = (
        Index("ix_user_profile_facts_userid_fact_type", "userid", "fact_type"),
        Index("ix_user_profile_facts_confidence", "confidence"),
    )

    id: Mapped[int] = id_column()
    userid: Mapped[str] = mapped_column(String(128), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), nullable=False)
    source_ai_run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id"), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_msgids: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    evidence_conversation_nos: Mapped[list[Any]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
