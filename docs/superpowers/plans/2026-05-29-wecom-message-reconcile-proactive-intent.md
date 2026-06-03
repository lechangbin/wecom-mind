# WeCom Message Reconcile And Proactive Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the next-stage historical message supplement and database-driven proactive intent scan so missed non-callback messages can be ingested, deduplicated, filtered, and safely considered for `chat_proactive_reminder`.

**Architecture:** Keep the long-connection @ reply path as the real-time main path. Add a lightweight message reconcile loop that pulls a short recent WeCom window, writes all messages into the existing message tables idempotently, then runs proactive intent detection from the database only. Do not split user and bot messages into separate tables; keep chronological conversation data in one `messages` table and filter by durable sender/handled metadata.

**Tech Stack:** FastAPI, SQLAlchemy ORM, SQLite-compatible schema upgrades, Dify blocking workflow client, WeCom AI bot long-connection sender, pytest.

---

## Execution Status

Implemented in this branch:

- Puller configuration defaults: 10 second interval, 12 second lookback, 2 second overlap.
- `messages.sender_type` and `messages.bot_role` classification while keeping one chronological message table.
- SQLite lightweight schema upgrades for the new message classification columns and index.
- Startup backfill for existing message rows so legacy bot messages are not scanned as user messages.
- Cross-source idempotency for real `msgid`, preventing long-connection and reconcile paths from duplicating the same WeCom message.
- Proactive scan SQL window filtering with `sender_type = "user"`.
- Durable `handled_records` built from `trigger_events` and passed into `chat_proactive_reminder`.
- Cross-source handled mention alignment for history messages that lack a real `msgid`: when long connection has already handled a mention and the puller later sees the same chat/user/content within a short time window under a payload-derived id, the pulled row is also passed as `handled_records`.
- Proactive output validation rejects any `quote_msgid` that is already listed in `handled_records`.
- `run_message_reconcile_once()` for single-run historical supplement: calculate window, fetch from injected source, idempotently ingest, run DB-driven proactive scan, update `wecom_mcp_pull_cursors`.
- Real WeCom MCP message source adapter for `get_message`, normalized into the same `ingest_message()` raw message format.
- Defensive long-connection frame normalization: when an AiBot frame has text content but omits `msgtype`, infer `text`; when both `msgid` and `req_id` are absent, use a stable payload idempotency key without inventing a business `msgid`.
- Proactive scan excludes `mentioned_bot = true` messages so `chat_proactive_reminder` can never answer an @ message with proactive wording.
- `mention_recovery` now handles history-pulled @ messages that have not produced a reply outbox, routes them back through `group_knowledge_reply`, and creates `scene=reply_recovery`.
- `wecom_reply_sessions` persists callback placeholder state (`frame_json`, `stream_id`, message identity, status) so recovered @ replies can reuse the original stream when available.
- The long-connection worker dispatcher sends both `proactive` and `reply_recovery` pending outboxes in `aibot_ws` mode.
- Permanent `MessageReconcileWorker` plus `scripts/run_message_reconcile_worker.py`, launched by `scripts/start_local_services.ps1` when `WECOM_MESSAGE_RECONCILE_ENABLED=true`.
- `WECOM_SENDER_MODE=aibot_ws` automatic proactive sending now avoids a second temporary WebSocket connection. The reconcile worker creates pending proactive outbox records; the long-connection worker reuses the reply bot WebSocket connection to send new proactive outboxes.
- Documentation updates for the WeCom adapter, trigger handling, live-test guide, README, and execution-flow HTML.

Deferred after this implementation:

- Durable retry, backoff, and rate-limit policy for failed proactive outboxes.
- Per-chat frequency control and gray release strategy for proactive customer-service replies.
- Redis or cross-process recent processed cache. The database remains the source of truth even if this cache is added.
- Strict per-chat durable processing queue and explicit interaction state machine.
- True Dify streaming for real-time @ replies. Non-@ proactive replies remain blocking because MCP/history-pulled messages do not have an original callback frame to bind a stream to.
- Broader identity fallback is out of scope. This branch treats `userid` as required for automatic proactive replies.

## Stage Decisions

1. The historical puller is a supplement, not the primary callback path.
2. The scanner interval remains 10 seconds, but the message fetch lookback is 12 seconds to absorb about 2 seconds of clock/network/timer drift.
3. Dify proactive intent detection must read from the local database, not directly from WeCom pull responses.
4. Exclusion must be based on structured fields and durable processing facts: `mentioned_bot = true` messages are not proactive candidates, and already handled @ messages are represented by `trigger_events`. Do not rely on text prefix checks such as whether content starts with `@`.
5. Keep all group messages in one normalized `messages` table. Add sender classification fields instead of splitting robot/user messages into separate tables.
6. A temporary in-memory processed set is allowed only as a performance cache. The database remains the source of truth.
7. This stage does not require a strict global database queue. If later tests show ordering or retry pressure, add a per-chat job queue as a separate stage.
8. Treat `userid` as a required contract for automatic proactive replies. The current real-machine `get_message` path can return `userid`, and this branch relies on that. Do not fabricate `userid`; if a pulled message has no real userid, it cannot become a proactive reply target and must not create an automatic @ outbox.
9. Non-@ proactive replies cannot use callback-bound placeholder streams. Placeholder and future streaming are only available when the system receives an original long-connection frame with callback metadata. History/MCP-pulled messages can only use normal proactive `send_message` after Dify finishes, unless the product explicitly accepts a separate "processing" message.
10. In `aibot_ws` mode, the reconcile worker must not open a separate temporary WebSocket to send proactive replies. It creates the outbox; the long-connection worker owns the reply bot WebSocket and sends new pending proactive outboxes.
11. @ fallback recovery is not a proactive intent path. History-pulled @ messages must go through `mention_recovery -> group_knowledge_reply`; if a `wecom_reply_sessions` row exists, final delivery should reuse that callback stream.

## Current Facts To Preserve

- `messages_raw` and `messages` already provide message idempotency via `idempotency_key` and `source + external_msgid`.
- `trigger_events` already provides durable @ trigger records with a unique constraint on `rule_code + message_id`.
- `chat_proactive_reminder` is the current proactive Dify app. The old `/api/scheduled-intents/run` endpoint delegates to the proactive reply path for compatibility.
- Long-connection @ reply work is already concurrent in the worker. Do not re-serialize the whole worker just to support the puller.
- The proactive scan helper now uses a SQL `chatid + create_time` window and `sender_type = "user"` filtering.
- Current real-machine tests have shown that the active `get_message` path can return user identity in this environment. Automatic proactive customer-service replies require `userid`; without it, the system cannot distinguish users or safely @ a target.
- @ replies can show an early placeholder because the long-connection frame is still available. Proactive replies created from pulled history do not have that frame and must not promise stream replacement.
- The system now persists that early @ placeholder in `wecom_reply_sessions`, giving the reconcile path a durable way to finish a previously placeholdered @ reply.

## Follow-Up Backlog For This Branch

The cross-version roadmap is maintained in [docs/architecture/version-roadmap.md](../../architecture/version-roadmap.md). This backlog only records items discovered while implementing the WeCom reconcile and proactive reply branch.

### P0: Userid Contract Verification

Goal: make the current successful `userid` path explicit as a required input contract for proactive replies.

- Keep real-machine acceptance focused on `messages.userid` being non-empty for user messages pulled by `WeComMcpMessageSource`.
- Keep Dify output validation strict: every `target_userids` value must come from input `members`.
- Do not build nickname/text/msgid-based identity inference.
- If a future WeCom change removes `userid` from history reads, log an error with `chatid/msgid/msgtype` and treat it as a platform-contract break for proactive replies.

### P0: Live Regression And Proactive Send Reliability

Goal: keep the automatic customer-service reply path testable through real-machine checks without duplicating WebSocket connections.

- Keep `MessageReconcileWorker` responsible for fetching, ingesting, Dify, and creating proactive outbox records.
- Keep `WeComAiBotLongConnectionWorker` responsible for sending new pending proactive outboxes in `aibot_ws` mode.
- Combine proactive send reliability and live regression into one manual test pass: @ reply, non-@ proactive reply, duplicate-message suppression, bot-message exclusion, invalid Dify output no-send, and `messages.userid` non-empty.
- Keep automatic retry out of this branch until frequency control is defined, to avoid repeated group messages.
- Verify outbox status transitions during the manual pass: `pending -> sending -> sent` or `pending -> sending -> failed`.

### P0: Required Next Feature Modules

Goal: raise the AI modules that are necessary for the product experience instead of leaving them as distant follow-ups.

- Prioritize conversation sedimentation / conversation summary as the next Dify-backed module after live regression.
- Prioritize user profile auto-update as a required Dify-backed module after conversation sedimentation.
- Prioritize the first frontend interface in the same next-stage plan, because manual verification and operations become inefficient without a UI.
- Keep module inputs grounded in the current database and system APIs; do not revive the removed old four-workflow Dify contract.

### P1: Streaming And Placeholder Scope

Goal: avoid over-promising streaming where WeCom does not provide a callback frame.

- Real-time @ reply can evolve from blocking Dify plus placeholder stream into true streaming Dify output.
- Non-@ proactive reply remains blocking and one-shot send.
- Do not add a "processing..." proactive group message unless explicitly approved as a product behavior, because it is a separate message, not a replaceable placeholder.

### P1: Interaction State And Operations

Goal: make troubleshooting tell whether a message is stuck at ingest, Dify, outbox, or send.

- Delay full interaction-state UI until the frontend foundation exists.
- Before the frontend, rely on structured logs, `ai_runs`, `outbox_messages`, and targeted admin queries for troubleshooting.
- Add a lightweight interaction state view or table only after real-machine testing and frontend design show the most useful fields.
- Preserve existing facts in `messages`, `trigger_events`, `ai_runs`, and `outbox_messages`; do not duplicate full payloads into a broad state table without a clear read use case.
- Add admin queries for recent proactive decisions, invalid Dify outputs, and failed sends.

### P2: Performance And Scale

Goal: keep the current SQLite/local design simple until load proves otherwise.

- Add Redis recent-processed cache only as an optimization. A cache miss must still fall back to durable database facts.
- Consider Redis/cache work when frontend usage or production traffic exposes slow reads, repeated expensive queries, or page-load latency.
- Add per-chat worker locking only if multi-process deployment begins to run the same chatid concurrently.
- Add a durable queue if retry pressure, ordering pressure, or worker restarts start losing useful work.

## File Structure

- Modify: `src/app/config/settings.py`
  - Add puller interval/window settings and keep defaults safe for local testing.
- Modify: `.env.example`
  - Document the new puller settings without adding secrets.
- Modify: `src/app/db/models.py`
  - Add sender classification fields to `Message`.
  - Reuse `WeComMcpPullCursor` for pull watermark tracking.
- Modify: `src/app/db/connection.py`
  - Add lightweight SQLite schema upgrades for new `messages` columns.
- Modify: `src/app/wecom/services.py`
  - Classify each ingested message as user/bot/system during normalization.
- Create: `src/app/wecom/message_reconcile.py`
  - Own the pull-window calculation, cursor update, idempotent ingest, and DB-driven scan handoff.
- Modify: `src/app/proactive_replies/services.py`
  - Use SQL time-window filtering.
  - Exclude bot messages.
  - Build `handled_records` from durable DB facts.
- Create or modify: `tests/test_stage15_message_reconcile.py`
  - Cover puller window, idempotent ingest, sender filtering, handled @ exclusion, and proactive handoff.
- Modify: `docs/modules/01-wecom-adapter.md`
  - Record the historical puller as a supplement path.
- Modify: `docs/modules/03-trigger-router.md`
  - Record that @ handled status is derived from `trigger_events`.
- Modify: `docs/dify-dsl/live-test-guide.md`
  - Add real-machine test steps for @ reply plus non-@ proactive scan.

---

### Task 1: Add Puller Configuration

**Files:**
- Modify: `src/app/config/settings.py`
- Modify: `.env.example`
- Test: `tests/test_foundation.py`

- [ ] **Step 1: Add a failing settings test**

Add this test to `tests/test_foundation.py`:

```python
from app.config.settings import Settings


def test_message_reconcile_defaults_are_short_window():
    settings = Settings()

    assert settings.wecom_message_reconcile_enabled is False
    assert settings.wecom_message_reconcile_interval_seconds == 10
    assert settings.wecom_message_reconcile_lookback_seconds == 12
    assert settings.wecom_message_reconcile_overlap_seconds == 2
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_foundation.py::test_message_reconcile_defaults_are_short_window -q
```

Expected: FAIL because the settings fields do not exist yet.

- [ ] **Step 3: Add settings fields**

Add these fields to `Settings` in `src/app/config/settings.py` near the WeCom settings:

```python
    wecom_message_reconcile_enabled: bool = False
    wecom_message_reconcile_interval_seconds: int = 10
    wecom_message_reconcile_lookback_seconds: int = 12
    wecom_message_reconcile_overlap_seconds: int = 2
```

- [ ] **Step 4: Document env defaults**

Add these lines to `.env.example`:

```env
# Historical message supplement. Keep disabled until the scanner worker is started.
WECOM_MESSAGE_RECONCILE_ENABLED=false
WECOM_MESSAGE_RECONCILE_INTERVAL_SECONDS=10
WECOM_MESSAGE_RECONCILE_LOOKBACK_SECONDS=12
WECOM_MESSAGE_RECONCILE_OVERLAP_SECONDS=2
```

- [ ] **Step 5: Verify settings test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_foundation.py::test_message_reconcile_defaults_are_short_window -q
```

Expected: PASS.

---

### Task 2: Classify Message Sender Without Splitting Tables

**Files:**
- Modify: `src/app/db/models.py`
- Modify: `src/app/db/connection.py`
- Modify: `src/app/wecom/services.py`
- Test: `tests/test_stage15_message_reconcile.py`

- [ ] **Step 1: Write a failing sender classification test**

Create `tests/test_stage15_message_reconcile.py` with this initial test:

```python
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import Settings
from app.db.models import Message
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'stage15.db'}",
        wecom_aibot_id="INTENT_BOT",
        wecom_aibot_name="意图机器人",
        wecom_reply_aibot_id="REPLY_BOT",
        wecom_reply_aibot_name="回复机器人",
    )
    app = create_app(settings=settings)
    return TestClient(app), app


def ingest_text(client, *, msgid, userid, content, mentioned_users=None):
    response = client.post(
        "/api/wecom/messages/ingest",
        json={
            "source": "aibot_ws",
            "idempotency_key": f"aibot_ws_msg_{msgid}",
            "raw_message": {
                "msgid": msgid,
                "chatid": "CHAT_STAGE15",
                "chattype": "group",
                "from": {"userid": userid, "name": userid},
                "msgtype": "text",
                "text": {"content": content},
                "mentioned_users": mentioned_users or [],
                "create_time": 1777827600,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["message_id"]


def test_ingested_messages_have_sender_classification(tmp_path):
    client, app = make_client(tmp_path)

    user_message_id = ingest_text(
        client,
        msgid="MSG_USER",
        userid="USER_A",
        content="客户问报价审批要求。",
    )
    reply_bot_message_id = ingest_text(
        client,
        msgid="MSG_REPLY_BOT",
        userid="REPLY_BOT",
        content="这是机器人回复。",
    )

    with app.state.SessionLocal() as session:
        user_message = session.get(Message, user_message_id)
        bot_message = session.get(Message, reply_bot_message_id)

        assert user_message.sender_type == "user"
        assert user_message.bot_role is None
        assert bot_message.sender_type == "bot"
        assert bot_message.bot_role == "reply_bot"
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_ingested_messages_have_sender_classification -q
```

Expected: FAIL because `Message.sender_type` and `Message.bot_role` do not exist yet.

- [ ] **Step 3: Add columns and index**

Add these fields to `Message` in `src/app/db/models.py`:

```python
    sender_type: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    bot_role: Mapped[str | None] = mapped_column(String(32))
```

Add this index to `Message.__table_args__`:

```python
        Index("ix_messages_chatid_sender_type_create_time", "chatid", "sender_type", "create_time"),
```

- [ ] **Step 4: Add lightweight SQLite schema upgrades**

In `src/app/db/connection.py`, extend `_ensure_lightweight_schema_upgrades()` so it checks `PRAGMA table_info(messages)` and runs:

```python
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN sender_type VARCHAR(32) NOT NULL DEFAULT 'user'"
            )
```

and:

```python
            connection.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN bot_role VARCHAR(32)"
            )
```

Then create the index if it is missing:

```python
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_messages_chatid_sender_type_create_time "
                "ON messages (chatid, sender_type, create_time)"
            )
```

- [ ] **Step 5: Classify senders during ingest**

In `src/app/wecom/services.py`, add a helper near the other private helpers:

```python
def _sender_classification(userid: str | None, settings: Settings) -> tuple[str, str | None]:
    if not userid:
        return "system", None
    if userid == settings.effective_reply_aibot_id:
        return "bot", "reply_bot"
    if userid == settings.effective_intent_aibot_id:
        return "bot", "intent_bot"
    if userid in {settings.wecom_aibot_id, settings.wecom_bot_id}:
        return "bot", "unknown_bot"
    return "user", None
```

Call it before constructing `Message`:

```python
    sender_type, bot_role = _sender_classification(userid, settings)
```

Set the new fields on `Message`:

```python
        sender_type=sender_type,
        bot_role=bot_role,
```

- [ ] **Step 6: Verify sender classification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_ingested_messages_have_sender_classification -q
```

Expected: PASS.

---

### Task 3: Move Proactive Scan To Indexed Database Window Reads

**Files:**
- Modify: `src/app/proactive_replies/services.py`
- Test: `tests/test_stage15_message_reconcile.py`

- [ ] **Step 1: Write a failing test that bot messages are excluded**

Append this test to `tests/test_stage15_message_reconcile.py`:

```python
from app.proactive_replies.services import _messages_in_window
from datetime import datetime, timezone


def test_proactive_window_reads_only_user_messages(tmp_path):
    client, app = make_client(tmp_path)
    ingest_text(client, msgid="MSG_USER_2", userid="USER_A", content="我不确定审批要求。")
    ingest_text(client, msgid="MSG_BOT_2", userid="REPLY_BOT", content="机器人上一条回复。")

    with app.state.SessionLocal() as session:
        messages = _messages_in_window(
            session,
            "CHAT_STAGE15",
            datetime.fromtimestamp(1777827500, tz=timezone.utc),
            datetime.fromtimestamp(1777827700, tz=timezone.utc),
        )

        assert [message.external_msgid for message in messages] == ["MSG_USER_2"]
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_proactive_window_reads_only_user_messages -q
```

Expected: FAIL because `_messages_in_window()` currently reads all messages for the chat and filters only in Python.

- [ ] **Step 3: Replace Python time filtering with SQL filtering**

Change `_messages_in_window()` in `src/app/proactive_replies/services.py` to:

```python
def _messages_in_window(
    session: Session,
    chatid: str,
    start_time: datetime,
    end_time: datetime,
) -> list[Message]:
    start_utc = _to_utc(start_time)
    end_utc = _to_utc(end_time)
    return session.scalars(
        select(Message)
        .where(
            Message.chatid == chatid,
            Message.create_time >= start_utc,
            Message.create_time <= end_utc,
            Message.sender_type == "user",
        )
        .order_by(Message.create_time.asc(), Message.id.asc())
    ).all()
```

- [ ] **Step 4: Verify bot exclusion**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_proactive_window_reads_only_user_messages -q
```

Expected: PASS.

---

### Task 4: Pass Durable Handled Records To Dify

**Files:**
- Modify: `src/app/proactive_replies/services.py`
- Test: `tests/test_stage15_message_reconcile.py`

- [ ] **Step 1: Write a failing handled-record test**

Append this test to `tests/test_stage15_message_reconcile.py`:

```python
from app.db.models import TriggerEvent


def test_proactive_payload_marks_already_handled_mentions(tmp_path):
    client, app = make_client(tmp_path)
    message_id = ingest_text(
        client,
        msgid="MSG_HANDLED_AT",
        userid="USER_A",
        content="@回复机器人 查一下审批要求",
        mentioned_users=["REPLY_BOT"],
    )

    with app.state.SessionLocal() as session:
        session.add(
            TriggerEvent(
                rule_code="default_mention_reply",
                trigger_type="mention",
                message_id=message_id,
                chatid="CHAT_STAGE15",
                userid="USER_A",
                workflow_code="group_knowledge_reply",
                reason={"source": "long_connection"},
                status="handled",
            )
        )
        session.commit()

        messages = _messages_in_window(
            session,
            "CHAT_STAGE15",
            datetime.fromtimestamp(1777827500, tz=timezone.utc),
            datetime.fromtimestamp(1777827700, tz=timezone.utc),
        )
        payload = build_chat_proactive_reminder_input(session=session, messages=messages)

        assert payload["payload"]["handled_records"] == [
            {
                "msgid": "MSG_HANDLED_AT",
                "message_id": str(message_id),
                "handler": "group_knowledge_reply",
                "reason": "mention_already_handled",
            }
        ]
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_proactive_payload_marks_already_handled_mentions -q
```

Expected: FAIL because `build_chat_proactive_reminder_input()` currently does not accept `session` and sends `handled_records: []`.

- [ ] **Step 3: Change the input builder signature**

Change the signature in `src/app/proactive_replies/services.py`:

```python
def build_chat_proactive_reminder_input(
    *,
    session: Session,
    messages: list[Message],
) -> dict[str, Any]:
```

Update its caller in `run_proactive_reply()`:

```python
    input_json = build_chat_proactive_reminder_input(session=session, messages=messages)
```

- [ ] **Step 4: Add a durable handled-record helper**

Add this helper in `src/app/proactive_replies/services.py`:

```python
def _handled_records_for_messages(
    session: Session,
    messages: list[Message],
) -> list[dict[str, str]]:
    if not messages:
        return []

    by_id = {message.id: message for message in messages}
    events = session.scalars(
        select(TriggerEvent).where(
            TriggerEvent.message_id.in_(by_id.keys()),
            TriggerEvent.status.in_(["handled", "sent", "success"]),
        )
    ).all()

    records = []
    for event in events:
        message = by_id.get(event.message_id)
        if not message:
            continue
        records.append(
            {
                "msgid": message.external_msgid,
                "message_id": str(message.id),
                "handler": event.workflow_code,
                "reason": "mention_already_handled",
            }
        )
    return records
```

Also import `TriggerEvent`:

```python
from app.db.models import AiRun, Message, TriggerEvent, now_utc
```

- [ ] **Step 5: Use handled records in the Dify input**

Inside `build_chat_proactive_reminder_input()`, replace:

```python
            "handled_records": [],
```

with:

```python
            "handled_records": _handled_records_for_messages(session, messages),
```

- [ ] **Step 6: Verify handled-record behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_proactive_payload_marks_already_handled_mentions -q
```

Expected: PASS.

---

### Task 5: Add Message Reconcile Module

**Files:**
- Create: `src/app/wecom/message_reconcile.py`
- Test: `tests/test_stage15_message_reconcile.py`

- [ ] **Step 1: Write a failing window-calculation test**

Append this test to `tests/test_stage15_message_reconcile.py`:

```python
from app.wecom.message_reconcile import calculate_reconcile_window


def test_reconcile_window_uses_last_pull_minus_overlap_when_available():
    now = datetime.fromtimestamp(1777827610, tz=timezone.utc)
    last_pulled_at = datetime.fromtimestamp(1777827600, tz=timezone.utc)

    start, end = calculate_reconcile_window(
        now=now,
        last_pulled_at=last_pulled_at,
        lookback_seconds=12,
        overlap_seconds=2,
    )

    assert start == datetime.fromtimestamp(1777827598, tz=timezone.utc)
    assert end == now


def test_reconcile_window_uses_lookback_without_cursor():
    now = datetime.fromtimestamp(1777827610, tz=timezone.utc)

    start, end = calculate_reconcile_window(
        now=now,
        last_pulled_at=None,
        lookback_seconds=12,
        overlap_seconds=2,
    )

    assert start == datetime.fromtimestamp(1777827598, tz=timezone.utc)
    assert end == now
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_reconcile_window_uses_last_pull_minus_overlap_when_available tests/test_stage15_message_reconcile.py::test_reconcile_window_uses_lookback_without_cursor -q
```

Expected: FAIL because `src/app/wecom/message_reconcile.py` does not exist.

- [ ] **Step 3: Create the pure window helper**

Create `src/app/wecom/message_reconcile.py` with:

```python
from datetime import datetime, timedelta


def calculate_reconcile_window(
    *,
    now: datetime,
    last_pulled_at: datetime | None,
    lookback_seconds: int,
    overlap_seconds: int,
) -> tuple[datetime, datetime]:
    if last_pulled_at is None:
        return now - timedelta(seconds=lookback_seconds), now
    return last_pulled_at - timedelta(seconds=overlap_seconds), now
```

- [ ] **Step 4: Verify window helper**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py::test_reconcile_window_uses_last_pull_minus_overlap_when_available tests/test_stage15_message_reconcile.py::test_reconcile_window_uses_lookback_without_cursor -q
```

Expected: PASS.

- [ ] **Step 5: Add the reconcile interface after tests define the expected behavior**

Extend `src/app/wecom/message_reconcile.py` with a module-level function named `run_message_reconcile_once()` whose interface is:

```python
def run_message_reconcile_once(
    session: Session,
    *,
    chatid: str,
    settings: Settings,
    message_source: WeComHistoryMessageSource,
    dify_client: DifyClient,
    auto_enqueue: bool = True,
) -> dict[str, Any]:
```

The function must do these operations in order:

1. Read `WeComMcpPullCursor` by `chatid` and `cursor_type="message_reconcile"`.
2. Calculate the window with `calculate_reconcile_window()`.
3. Fetch messages from `message_source.fetch_messages(chatid=chatid, start_time=start, end_time=end)`.
4. Sort fetched messages by `create_time`, then `msgid`.
5. Call `ingest_message()` for each fetched message with idempotency key `reconcile_msg_{msgid}` when `msgid` exists.
6. Commit after ingesting the batch.
7. Run `run_proactive_reply()` using the same `chatid`, window start/end, and `auto_enqueue`.
8. Update `WeComMcpPullCursor.last_pulled_at=end` only after ingest and proactive scan finish without raising.
9. Return counts: `fetched_count`, `ingested_count`, `duplicated_count`, `proactive_status`, and `outbox_count`.

Do not call Dify before the messages are committed to the database.

---

### Task 6: Add Optional Recent Processed Cache

**Files:**
- Create or modify: `src/app/wecom/message_reconcile.py`
- Test: `tests/test_stage15_message_reconcile.py`

- [ ] **Step 1: Add a small TTL cache only if duplicate DB reads become visible in tests**

Use this exact behavior if implemented:

```python
class RecentProcessedSet:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, float] = {}

    def add(self, key: str, *, now_monotonic: float) -> None:
        self._items[key] = now_monotonic + self.ttl_seconds

    def contains(self, key: str, *, now_monotonic: float) -> bool:
        self.prune(now_monotonic=now_monotonic)
        return key in self._items

    def prune(self, *, now_monotonic: float) -> None:
        expired = [key for key, expires_at in self._items.items() if expires_at <= now_monotonic]
        for key in expired:
            self._items.pop(key, None)
```

The cache must never be the only exclusion source. A cache miss must fall back to database facts.

- [ ] **Step 2: Keep this task optional**

Skip this task if Task 4 and Task 5 are already fast enough with indexed SQL queries. The expected MVP path is to rely on `trigger_events`, `messages.external_msgid`, and database uniqueness first.

---

### Task 7: Document Runtime Boundary And Real-Machine Test Guide

**Files:**
- Modify: `docs/modules/01-wecom-adapter.md`
- Modify: `docs/modules/03-trigger-router.md`
- Modify: `docs/dify-dsl/live-test-guide.md`

- [ ] **Step 1: Update WeCom adapter documentation**

Add a section that says:

```markdown
## 历史消息补漏

历史消息补漏不是主接收链路。主接收链路仍是企业微信智能机器人长连接。

补漏任务每 10 秒运行一次，默认拉取最近 12 秒或 `last_pulled_at - 2s` 到当前时间的窗口。拉到的消息必须先写入 `messages_raw/messages`，再由数据库驱动后续意图识别。

补漏任务不得直接把企微拉取结果传给 Dify。Dify 输入只能从数据库窗口构造。
```

- [ ] **Step 2: Update trigger router documentation**

Add a section that says:

```markdown
## 已处理消息判定

主动提醒排除规则不得使用文本前缀判断。是否已经由 @ 回复处理，以 `trigger_events` 中同一 `message_id` 的 mention 规则记录为准。

`handled_records` 会传入 Dify 的 `chat_proactive_reminder` payload，用于提示模型不要重复回答已经处理过的 @ 消息。
```

- [ ] **Step 3: Update live test guide**

Add a test scenario:

```markdown
## 补漏与主动提醒复测

1. 启动 API、长连接 worker、消息补漏 worker。
2. 在群内发送一条 @ 回复机器人消息，确认实时 @ 回复正常。
3. 等待一次补漏扫描后，确认该消息入库但不会被主动提醒重复处理。
4. 在群内发送一条非 @ 的知识需求消息，等待补漏扫描和主动提醒扫描。
5. 确认主动提醒只引用输入中真实存在的 `msgid`，且不引用机器人消息。
```

---

### Task 8: Verification Before Handoff

**Files:**
- No source files beyond previous tasks

- [ ] **Step 1: Run targeted tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage15_message_reconcile.py -q
```

Expected: PASS.

- [ ] **Step 2: Run existing Dify and long-connection tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_stage13_dify_ai_apps.py tests/test_stage14_wecom_aibot_long_connection.py -q
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Manual local startup**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-services.ps1
```

Expected:

- API starts on the configured local port.
- Long-connection worker starts.
- No secret values are printed in logs.
- @ reply still works before enabling the puller.

---

## Acceptance Criteria

- The puller window is 12 seconds by default and uses `last_pulled_at - 2s` when a cursor exists.
- Every pulled message is ingested before any proactive Dify call.
- Proactive scan queries `messages` by `chatid + create_time` in SQL and excludes `sender_type != "user"`.
- Proactive scan also excludes `mentioned_bot = true`; @ messages must not be answered by `chat_proactive_reminder`.
- Already handled @ messages are passed to Dify as `handled_records`.
- If a history-pulled duplicate of a handled @ message has only a payload-derived id, it is still marked through `handled_records` by matching the existing mention trigger to the same chat/user/content within a short time window.
- Dify outputs that try to quote a handled record are rejected and do not create proactive outbox messages.
- Text-prefix exclusion is not used.
- Robot and user messages remain in one `messages` table.
- No new global queue is introduced in this stage.
- Existing @ reply real-machine behavior remains unchanged.

## Deferred Items

- Strict per-chat processing queue with retryable jobs.
- Redis-backed processed cache for multi-process deployments.
- PostgreSQL migration and production-grade migrations.
- Dify streaming.
- Long-window historical backfill.
- Splitting message tables by sender type. This remains rejected unless future query volume proves the single-table model insufficient.
