"""匿名访客上下文中间件测试。"""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.middleware.visitor_context import VISITOR_ID_COOKIE_NAME, VisitorContextMiddleware


def _build_client() -> TestClient:
    """构建挂载匿名访客中间件的最小测试应用。"""

    app = FastAPI()
    app.add_middleware(VisitorContextMiddleware)

    @app.get("/whoami")
    async def whoami(request: Request) -> dict[str, str]:
        return {"visitor_id": request.state.visitor_id}

    return TestClient(app, base_url="https://testserver")


def _get_cookie_value(client: TestClient) -> str | None:
    """稳定读取测试客户端中的 visitor_id Cookie。"""

    for cookie in client.cookies.jar:
        if cookie.name == VISITOR_ID_COOKIE_NAME and cookie.domain == "testserver.local":
            return cookie.value

    for cookie in client.cookies.jar:
        if cookie.name == VISITOR_ID_COOKIE_NAME:
            return cookie.value
    return None


def test_visitor_context_generates_cookie_for_first_request() -> None:
    """首次请求应自动生成 visitor_id 并写入 Cookie。"""

    client = _build_client()

    response = client.get("/whoami")

    assert response.status_code == 200
    visitor_id = response.json()["visitor_id"]
    assert str(UUID(visitor_id)) == visitor_id
    assert _get_cookie_value(client) == visitor_id
    assert response.headers.get("set-cookie")


def test_visitor_context_reuses_valid_cookie() -> None:
    """已存在的合法 visitor_id 应被复用。"""

    client = _build_client()
    visitor_id = "00000000-0000-4000-8000-000000000021"
    client.cookies.set(VISITOR_ID_COOKIE_NAME, visitor_id)

    response = client.get("/whoami")

    assert response.status_code == 200
    assert response.json()["visitor_id"] == visitor_id
    assert _get_cookie_value(client) == visitor_id


def test_visitor_context_replaces_invalid_cookie() -> None:
    """非法 visitor_id Cookie 应被替换为新的 UUID。"""

    client = _build_client()
    client.cookies.set(VISITOR_ID_COOKIE_NAME, "invalid-visitor-id")

    response = client.get("/whoami")

    assert response.status_code == 200
    visitor_id = response.json()["visitor_id"]
    assert visitor_id != "invalid-visitor-id"
    assert str(UUID(visitor_id)) == visitor_id
    assert _get_cookie_value(client) == visitor_id
