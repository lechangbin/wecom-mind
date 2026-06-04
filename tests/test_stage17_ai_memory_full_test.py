from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.db.models import ConversationSegment, Message, MessageRaw, UserProfile
from app.main import create_app


def test_ai_memory_full_test_endpoint_segments_actual_messages_and_updates_profiles(
    tmp_path,
):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'ai_memory.db'}",
        dify_client_mode="mock",
    )
    app = create_app(settings=settings)

    with app.state.SessionLocal() as session:
        _add_message(
            session,
            msgid="MSG_001",
            chatid="CHAT_A",
            userid="USER_A",
            content="以后回答我先给结论。",
            create_time=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
        )
        _add_message(
            session,
            msgid="MSG_002",
            chatid="CHAT_A",
            userid="BOT",
            content="好的，我会先给结论。",
            sender_type="bot",
            create_time=datetime(2026, 6, 4, 1, 1, tzinfo=timezone.utc),
        )
        _add_message(
            session,
            msgid="MSG_003",
            chatid="CHAT_A",
            userid="USER_B",
            content="我希望最多三条要点。",
            create_time=datetime(2026, 6, 4, 1, 2, tzinfo=timezone.utc),
        )
        session.commit()

    client = TestClient(app)
    response = client.post(
        "/api/ai-memory/full-test/run",
        json={
            "target_date": "2026-06-04",
            "chatids": ["CHAT_A"],
            "run_profiles": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["target_date"] == "2026-06-04"
    assert data["chat_count"] == 1
    assert data["segment_count"] == 1
    assert data["profile_update_count"] == 2

    with app.state.SessionLocal() as session:
        segments = session.scalars(select(ConversationSegment)).all()
        profiles = session.scalars(select(UserProfile)).all()

    assert len(segments) == 1
    assert segments[0].chatid == "CHAT_A"
    assert segments[0].start_message_id < segments[0].end_message_id
    assert {profile.userid for profile in profiles} == {"USER_A", "USER_B"}

    response = client.post(
        "/api/ai-memory/full-test/run",
        json={
            "target_date": "2026-06-04",
            "chatids": ["CHAT_A"],
            "run_profiles": True,
        },
    )

    assert response.status_code == 200
    rerun_data = response.json()["data"]
    assert rerun_data["segment_count"] == 1
    assert rerun_data["profile_update_count"] == 0

    with app.state.SessionLocal() as session:
        profiles_after_rerun = session.scalars(select(UserProfile)).all()

    assert len(profiles_after_rerun) == 2


def _add_message(
    session,
    *,
    msgid: str,
    chatid: str,
    userid: str,
    content: str,
    create_time: datetime,
    sender_type: str = "user",
) -> None:
    raw = MessageRaw(
        source="test",
        external_msgid=msgid,
        chatid=chatid,
        userid=userid,
        msgtype="text",
        raw_payload={"text": {"content": content}},
        idempotency_key=f"raw_{msgid}",
        create_time=create_time,
    )
    session.add(raw)
    session.flush()
    session.add(
        Message(
            raw_message_id=raw.id,
            external_msgid=msgid,
            chatid=chatid,
            chattype="group",
            userid=userid,
            msgtype="text",
            sender_type=sender_type,
            content_text=content,
            normalized_content={"text": content},
            mentioned_bot=False,
            mentioned_users=[],
            create_time=create_time,
        )
    )
