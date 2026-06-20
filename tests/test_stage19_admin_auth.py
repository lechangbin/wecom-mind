from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def make_client(tmp_path):
    settings = Settings(
        app_env="local",
        database_url=f"sqlite:///{tmp_path / 'stage19.db'}",
        wecom_aibot_id="BOT_ID",
        wecom_aibot_name="机器人",
        dify_client_mode="mock",
        admin_username="admin",
        admin_password="secret-password",
        admin_session_secret="test-session-secret",
    )
    app = create_app(settings=settings)
    return TestClient(app)


def test_admin_api_requires_login_when_admin_credentials_are_configured(tmp_path):
    client = make_client(tmp_path)

    response = client.get("/api/admin/reply-tasks")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_admin_login_me_and_logout_manage_cookie_session(tmp_path):
    client = make_client(tmp_path)

    bad_login = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )
    assert bad_login.status_code == 401

    login = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "secret-password"},
    )
    assert login.status_code == 200
    assert login.json()["data"]["authenticated"] is True
    assert login.json()["data"]["username"] == "admin"
    assert "wecom_admin_session" in client.cookies

    me = client.get("/api/admin/auth/me")
    assert me.status_code == 200
    assert me.json()["data"] == {
        "auth_enabled": True,
        "authenticated": True,
        "username": "admin",
    }

    protected = client.get("/api/admin/reply-tasks")
    assert protected.status_code == 200

    logout = client.post("/api/admin/auth/logout")
    assert logout.status_code == 200

    after_logout = client.get("/api/admin/reply-tasks")
    assert after_logout.status_code == 401
