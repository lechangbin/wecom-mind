from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AiWorkflow, TriggerRule
from app.dify.schemas import (
    CONVERSATION_SEGMENTATION_INPUT_SCHEMA,
    CONVERSATION_SEGMENTATION_OUTPUT_SCHEMA,
    INTENT_DETECTION_INPUT_SCHEMA,
    INTENT_DETECTION_OUTPUT_SCHEMA,
    REPLY_GENERATION_INPUT_SCHEMA,
    REPLY_GENERATION_OUTPUT_SCHEMA,
    USER_PROFILE_ANALYSIS_INPUT_SCHEMA,
    USER_PROFILE_ANALYSIS_OUTPUT_SCHEMA,
)


def initialize_default_records(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _ensure_default_mention_rule(session)
        _ensure_default_scheduled_intent_rule(session)
        _ensure_default_reply_generation_workflow(session)
        _ensure_default_intent_detection_workflow(session)
        _ensure_default_conversation_segmentation_workflow(session)
        _ensure_default_user_profile_analysis_workflow(session)
        session.commit()


def _ensure_default_mention_rule(session: Session) -> None:
    existing = session.scalar(
        select(TriggerRule).where(TriggerRule.rule_code == "default_mention_reply")
    )
    if existing:
        existing.workflow_code = "reply_generation"
        return

    session.add(
        TriggerRule(
            rule_code="default_mention_reply",
            rule_name="默认 @ 机器人回复规则",
            trigger_type="mention",
            config={"field": "mentioned_bot", "equals": True},
            workflow_code="reply_generation",
            priority=100,
            enabled=True,
        )
    )


def _ensure_default_scheduled_intent_rule(session: Session) -> None:
    existing = session.scalar(
        select(TriggerRule).where(
            TriggerRule.rule_code == "default_scheduled_intent_detection"
        )
    )
    if existing:
        existing.workflow_code = "intent_detection"
        return

    session.add(
        TriggerRule(
            rule_code="default_scheduled_intent_detection",
            rule_name="默认定时意图识别规则",
            trigger_type="schedule",
            config={"window": "manual"},
            workflow_code="intent_detection",
            priority=200,
            enabled=True,
        )
    )


def _ensure_default_reply_generation_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "reply_generation",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        return

    session.add(
        AiWorkflow(
            workflow_code="reply_generation",
            workflow_name="通用回复生成",
            provider="dify",
            dify_app_id="mock_reply_generation_app",
            dify_workflow_id="mock_reply_generation_workflow",
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=REPLY_GENERATION_INPUT_SCHEMA,
            output_schema=REPLY_GENERATION_OUTPUT_SCHEMA,
            enabled=True,
        )
    )


def _ensure_default_intent_detection_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "intent_detection",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        return

    session.add(
        AiWorkflow(
            workflow_code="intent_detection",
            workflow_name="意图识别与动作决策",
            provider="dify",
            dify_app_id="mock_intent_detection_app",
            dify_workflow_id="mock_intent_detection_workflow",
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=INTENT_DETECTION_INPUT_SCHEMA,
            output_schema=INTENT_DETECTION_OUTPUT_SCHEMA,
            enabled=True,
        )
    )


def _ensure_default_conversation_segmentation_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "conversation_segmentation",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        return

    session.add(
        AiWorkflow(
            workflow_code="conversation_segmentation",
            workflow_name="智能会话切分",
            provider="dify",
            dify_app_id="mock_conversation_segmentation_app",
            dify_workflow_id="mock_conversation_segmentation_workflow",
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=CONVERSATION_SEGMENTATION_INPUT_SCHEMA,
            output_schema=CONVERSATION_SEGMENTATION_OUTPUT_SCHEMA,
            enabled=True,
        )
    )


def _ensure_default_user_profile_analysis_workflow(session: Session) -> None:
    existing = session.scalar(
        select(AiWorkflow).where(
            AiWorkflow.workflow_code == "user_profile_analysis",
            AiWorkflow.version == "v1",
        )
    )
    if existing:
        return

    session.add(
        AiWorkflow(
            workflow_code="user_profile_analysis",
            workflow_name="用户画像分析",
            provider="dify",
            dify_app_id="mock_user_profile_analysis_app",
            dify_workflow_id="mock_user_profile_analysis_workflow",
            dify_webhook_url=None,
            version="v1",
            response_mode="blocking",
            input_schema=USER_PROFILE_ANALYSIS_INPUT_SCHEMA,
            output_schema=USER_PROFILE_ANALYSIS_OUTPUT_SCHEMA,
            enabled=True,
        )
    )
