"""向量嵌入服务的最小回归验证。"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vector_embedding_service import DashScopeEmbeddings


class _FakeEmbeddingsApi:
    """记录嵌入调用批次的假 API。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def create(
        self,
        *,
        model: str,
        input: list[str],
        dimensions: int,
        encoding_format: str,
    ) -> SimpleNamespace:
        self.calls.append(list(input))
        return SimpleNamespace(
            data=[
                SimpleNamespace(embedding=[float(call_index), float(len(text))])
                for call_index, text in enumerate(input, start=1)
            ]
        )


def _build_embedding_service(max_batch_size: int = 10) -> tuple[DashScopeEmbeddings, _FakeEmbeddingsApi]:
    """构建不依赖真实 OpenAI 客户端的嵌入服务实例。"""

    fake_api = _FakeEmbeddingsApi()
    service = DashScopeEmbeddings.__new__(DashScopeEmbeddings)
    service.client = SimpleNamespace(embeddings=fake_api)
    service.model = "text-embedding-v4"
    service.dimensions = 1024
    service.max_batch_size = max_batch_size
    return service, fake_api


def test_embed_documents_splits_large_batches_and_preserves_order() -> None:
    """批量嵌入应自动拆分成不超过 10 条的批次，并保持返回顺序。"""

    service, fake_api = _build_embedding_service(max_batch_size=10)
    texts = [f"text-{index}" for index in range(21)]

    embeddings = service.embed_documents(texts)

    assert fake_api.calls == [
        [f"text-{index}" for index in range(10)],
        [f"text-{index}" for index in range(10, 20)],
        ["text-20"],
    ]
    assert len(embeddings) == 21
    assert embeddings[0] == [1.0, float(len("text-0"))]
    assert embeddings[-1] == [1.0, float(len("text-20"))]


def test_embed_documents_returns_empty_list_for_empty_input() -> None:
    """空文本列表不应触发 API 调用。"""

    service, fake_api = _build_embedding_service()

    embeddings = service.embed_documents([])

    assert embeddings == []
    assert fake_api.calls == []
