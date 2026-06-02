"""在终端中展示文字面试 SSE 返回效果的演示测试。"""

from __future__ import annotations

import json

import pytest

from tests.test_phase5_text_interview import _build_api_client, _build_test_service


def _print_stream_trace(title: str, raw_lines: list[str]) -> list[dict[str, object]]:
    """打印原始 SSE 行与解析后的业务事件，便于终端观察流式返回效果。"""

    print(f"\n===== {title} =====")
    print("原始 SSE 行：")
    for index, line in enumerate(raw_lines, start=1):
        print(f"[{index:02d}] {line}")

    payloads = [
        json.loads(line.removeprefix("data: "))
        for line in raw_lines
        if line.startswith("data: ")
    ]

    print("解析后的事件：")
    for index, payload in enumerate(payloads, start=1):
        event_type = payload.get("type", "unknown")
        print(f"[{index:02d}] type={event_type} payload={json.dumps(payload, ensure_ascii=False)}")

    return payloads


def test_interview_sse_terminal_demo_for_next_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """展示正常回答后切到下一题时的 SSE 帧序列。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 2},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["data"]["session_id"]

    with client.stream(
        "POST",
        f"/api/interview/sessions/{session_id}/answers",
        json={
            "answer_text": (
                "我会先说明缓存目标，再结合热点数据、一致性要求和失效策略设计 Redis 方案，"
                "同时补充持久化、监控告警和故障降级处理。"
            )
        },
    ) as response:
        assert response.status_code == 200
        raw_lines = [line for line in response.iter_lines() if line]

    payloads = _print_stream_trace("正常回答 -> 下一题", raw_lines)
    payload_types = [payload["type"] for payload in payloads]

    assert payload_types == ["status", "plan", "content", "step_complete", "done"]
    assert payloads[-1]["session"]["current_question"]["question_key"] == "q-2"


def test_interview_sse_terminal_demo_for_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """展示简短回答后触发追问时的 SSE 帧序列。"""

    service, _, session, _ = _build_test_service()
    client = _build_api_client(monkeypatch, service, session)

    create_response = client.post(
        "/api/interview/sessions",
        json={"skill_id": "python-backend", "max_rounds": 2},
    )
    assert create_response.status_code == 200
    session_id = create_response.json()["data"]["session_id"]

    with client.stream(
        "POST",
        f"/api/interview/sessions/{session_id}/answers",
        json={"answer_text": "会用 Redis。"},
    ) as response:
        assert response.status_code == 200
        raw_lines = [line for line in response.iter_lines() if line]

    payloads = _print_stream_trace("简短回答 -> 追问", raw_lines)
    payload_types = [payload["type"] for payload in payloads]

    assert payload_types == ["status", "plan", "content", "step_complete", "done"]
    assert payloads[-1]["session"]["current_question"]["question_key"] == "q-1-f-1"
