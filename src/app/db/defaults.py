from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AiWorkflow, TriggerRule
from app.dify.schemas import (
    CHAT_PROACTIVE_REMINDER_OUTPUT_SCHEMA,
    CONVERSATION_BOUNDARY_DETECTION_OUTPUT_SCHEMA,
    GROUP_KNOWLEDGE_REPLY_OUTPUT_SCHEMA,
    PAYLOAD_ONLY_INPUT_SCHEMA,
    USER_PROFILE_UPDATE_OUTPUT_SCHEMA,
)


def initialize_default_records(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _delete_legacy_dify_defaults(session)
        _ensure_default_mention_rule(session)
        _ensure_default_group_knowledge_reply_workflow(session)
        _ensure_default_chat_proactive_reminder_workflow(session)
        _ensure_default_conversation_boundary_detection_workflow(session)
        _ensure_default_user_profile_update_workflow(session)
        session.commit()


def _delete_legacy_dify_defaults(session: Session) -> None:
    legacy_rule_codes = {"default_scheduled_intent_detection"}
    for rule in session.scalars(
        select(TriggerRule).where(TriggerRule.rule_code.in_(legacy_rule_codes))
    ).all():
        session.delete(rule)

    legacy_workflow_codes = {
        "reply_generation",
        "intent_detection",
        "conversation_segmentation",
        "user_profile_analysis",
    }
    for workflow in session.scalars(
        select(AiWorkflow).where(AiWorkflow.workflow_code.in_(legacy_workflow_codes))
    ).all():
        session.delete(workflow)


def _ensure_default_mention_rule(session: Session) -> None:
    existing = session.scalar(
        select(TriggerRule).where(TriggerRule.rule_code == "default_mention_reply")
    )
    if existing:
        existing.workflow_code = "group_knowledge_reply"
        return

    session.add(
        TriggerRule(
            rule_code="default_mention_reply",
            rule_name="默认 @ 机器人知识库答疑规则",
            trigger_type="mention",
            config={"field": "mentioned_bot", "equals": True},
            workflow_code="group_knowledge_reply",
            priority=100,
            enabled=True,
        )
    )


def _ensure_default_group_knowledge_reply_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "group_knowledge_reply",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        existing.workflow_name = "群知识库答疑"
        existing.dify_app_id = "group_knowledge_reply"
        existing.dify_workflow_id = None
        existing.response_mode = "blocking"
        existing.input_schema = PAYLOAD_ONLY_INPUT_SCHEMA
        existing.output_schema = GROUP_KNOWLEDGE_REPLY_OUTPUT_SCHEMA
        existing.enabled = True
        return

    session.add(
        AiWorkflow(
            workflow_code="group_knowledge_reply",
            workflow_name="群知识库答疑",
            provider="dify",
            dify_app_id="group_knowledge_reply",
            dify_workflow_id=None,
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=PAYLOAD_ONLY_INPUT_SCHEMA,
            output_schema=GROUP_KNOWLEDGE_REPLY_OUTPUT_SCHEMA,
            enabled=True,
        )
    )


def _ensure_default_conversation_boundary_detection_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "conversation_boundary_detection",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        existing.workflow_name = "每日会话切分边界检测"
        existing.dify_app_id = "conversation_boundary_detection"
        existing.dify_workflow_id = None
        existing.response_mode = "blocking"
        existing.input_schema = PAYLOAD_ONLY_INPUT_SCHEMA
        existing.output_schema = CONVERSATION_BOUNDARY_DETECTION_OUTPUT_SCHEMA
        existing.enabled = True
        return

    session.add(
        AiWorkflow(
            workflow_code="conversation_boundary_detection",
            workflow_name="每日会话切分边界检测",
            provider="dify",
            dify_app_id="conversation_boundary_detection",
            dify_workflow_id=None,
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=PAYLOAD_ONLY_INPUT_SCHEMA,
            output_schema=CONVERSATION_BOUNDARY_DETECTION_OUTPUT_SCHEMA,
            enabled=True,
        )
    )


def _ensure_default_user_profile_update_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "user_profile_update",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        existing.workflow_name = "单会话用户画像更新"
        existing.dify_app_id = "user_profile_update"
        existing.dify_workflow_id = None
        existing.response_mode = "blocking"
        existing.input_schema = PAYLOAD_ONLY_INPUT_SCHEMA
        existing.output_schema = USER_PROFILE_UPDATE_OUTPUT_SCHEMA
        existing.enabled = True
        return

    session.add(
        AiWorkflow(
            workflow_code="user_profile_update",
            workflow_name="单会话用户画像更新",
            provider="dify",
            dify_app_id="user_profile_update",
            dify_workflow_id=None,
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=PAYLOAD_ONLY_INPUT_SCHEMA,
            output_schema=USER_PROFILE_UPDATE_OUTPUT_SCHEMA,
            enabled=True,
        )
    )


def _ensure_default_chat_proactive_reminder_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "chat_proactive_reminder",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        existing.workflow_name = "意图触发知识库主动答疑"
        existing.dify_app_id = "chat_proactive_reminder"
        existing.dify_workflow_id = None
        existing.response_mode = "blocking"
        existing.input_schema = PAYLOAD_ONLY_INPUT_SCHEMA
        existing.output_schema = CHAT_PROACTIVE_REMINDER_OUTPUT_SCHEMA
        existing.enabled = True
        return

    session.add(
        AiWorkflow(
            workflow_code="chat_proactive_reminder",
            workflow_name="意图触发知识库主动答疑",
            provider="dify",
            dify_app_id="chat_proactive_reminder",
            dify_workflow_id=None,
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=PAYLOAD_ONLY_INPUT_SCHEMA,
            output_schema=CHAT_PROACTIVE_REMINDER_OUTPUT_SCHEMA,
            enabled=True,
        )
    )
