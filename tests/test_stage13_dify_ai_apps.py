import httpx

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.db.models import AiRun, AiWorkflow, BotReply, OutboxMessage
from app.dify.client import DifyHttpClient
from app.main import create_app


class GroupKnowledgeReplyDifyClient:
    def __init__(self):
        self.calls = []

    def run_workflow(self, workflow, input_json):
        self.calls.append((workflow, input_json))
        return {
            "action": "reply",
            "content": "这是来自知识库的答复。",
            "reason": "命中知识库。",
            "confidence": 0.91,
        }


class ChatProactiveReminderDifyClient:
    def __init__(self):
        self.calls = []

    def run_workflow(self, workflow, input_json):
        self.calls.append((workflow, input_json))
        return {
            "should_send": True,
            "target_userids": ["USER_A"],
            "quote_msgid": "MSG_P1",
            "content": "猜你可能想了解这条知识：报价前先确认审批要求。",
            "confidence": 0.82,
        }


def make_client(tmp_path, *, dify_client):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage13.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
    )
    app = create_app(settings=settings)
    app.state.dify_client = dify_client
    return TestClient(app), app


def ingest_text_message(
    client: TestClient,
    *,
    msgid: str,
    userid: str = "USER_A",
    content: str,
    mentioned_bot: bool = False,
    create_time: int = 1777827600,
):
    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "mcp",
            "idempotency_key": f"mcp_msg_{msgid}",
            "raw_message": {
                "msgid": msgid,
                "chatid": "CHAT_STAGE13",
                "chattype": "group",
                "from": {"userid": userid, "name": userid},
                "msgtype": "text",
                "text": {"content": content},
                "mentioned_users": ["BOT_ID"] if mentioned_bot else [],
                "create_time": create_time,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def test_mention_message_uses_group_knowledge_reply_and_creates_reply_outbox(tmp_path):
    dify_client = GroupKnowledgeReplyDifyClient()
    client, app = make_client(tmp_path, dify_client=dify_client)
    message_id = ingest_text_message(
        client,
        msgid="MSG_G1",
        content="@机器人 化妆品标签合规要注意什么？",
        mentioned_bot=True,
    )

    response = client.post("/api/triggers/evaluate", json={"message_id": message_id})

    assert response.status_code == 200
    event = response.json()["data"]["events"][0]
    assert event["workflow_code"] == "group_knowledge_reply"
    assert event["ai_run"]["status"] == "success"
    assert event["outbox"]["outbox_id"]

    workflow, input_json = dify_client.calls[0]
    assert workflow.workflow_code == "group_knowledge_reply"
    assert input_json["payload"]["question"] == "@机器人 化妆品标签合规要注意什么？"
    assert input_json["payload"]["message"]["msgid"] == "MSG_G1"

    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        outbox = session.scalar(select(OutboxMessage))
        reply = session.scalar(select(BotReply))

        assert run.workflow_code == "group_knowledge_reply"
        assert run.output_json["action"] == "reply"
        assert reply.content == "这是来自知识库的答复。"
        assert outbox.scene == "reply"
        assert outbox.target_userids == []
        assert outbox.msgtype == "markdown"
        assert outbox.content["markdown"]["content"] == "这是来自知识库的答复。"


def test_proactive_reply_run_uses_chat_proactive_reminder_and_creates_outbox(tmp_path):
    dify_client = ChatProactiveReminderDifyClient()
    client, app = make_client(tmp_path, dify_client=dify_client)
    ingest_text_message(
        client,
        msgid="MSG_P1",
        userid="USER_A",
        content="客户要报价，我不确定审批要求。",
        create_time=1777827600,
    )
    ingest_text_message(
        client,
        msgid="MSG_P2",
        userid="USER_B",
        content="最好查一下知识库。",
        create_time=1777827660,
    )

    response = client.post(
        "/api/proactive-replies/run",
        json={
            "chatid": "CHAT_STAGE13",
            "time_range": {
                "start": "2026-05-04T00:00:00+08:00",
                "end": "2026-05-04T02:00:00+08:00",
            },
            "auto_enqueue": True,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["workflow_code"] == "chat_proactive_reminder"
    assert data["status"] == "success"
    assert data["outboxes"][0]["target_userids"] == ["USER_A"]

    workflow, input_json = dify_client.calls[0]
    assert workflow.workflow_code == "chat_proactive_reminder"
    assert input_json["payload"]["messages"][0]["msgid"] == "MSG_P1"
    assert input_json["payload"]["members"] == [
        {"userid": "USER_A"},
        {"userid": "USER_B"},
    ]

    with app.state.SessionLocal() as session:
        run = session.scalar(select(AiRun))
        outbox = session.scalar(select(OutboxMessage))

        assert run.workflow_code == "chat_proactive_reminder"
        assert outbox.scene == "proactive"
        assert outbox.target_userids == ["USER_A"]
        assert outbox.msgtype == "markdown"
        assert "<@USER_A>" in outbox.content["markdown"]["content"]
        assert "猜你可能想了解" in outbox.content["markdown"]["content"]


def test_dify_http_client_uses_workflow_specific_api_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"data": {"status": "succeeded", "outputs": {"action": "out_of_scope"}}},
        )

    client = DifyHttpClient(
        Settings(
            dify_client_mode="real",
            dify_base_url="https://api.dify.local/v1",
            dify_api_key="",
            dify_group_knowledge_reply_api_key="group-key",
            dify_chat_proactive_reminder_api_key="proactive-key",
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    workflow = AiWorkflow(
        workflow_code="group_knowledge_reply",
        workflow_name="群知识库答疑",
        provider="dify",
        version="v1",
        response_mode="blocking",
        input_schema={},
        output_schema={},
        enabled=True,
    )

    client.run_workflow(workflow, {"payload": {}})

    assert seen["authorization"] == "Bearer group-key"
