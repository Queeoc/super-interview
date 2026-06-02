"""批量导入试用模型 Provider 配置。

用法示例：
    conda run -n biz_agent python scripts/import_trial_providers.py --file scripts/providers.json --default-code qwen-trial-001

输入 JSON 示例：
[
  {
    "provider_code": "qwen-trial-001",
    "provider_name": "Qwen Trial 001",
    "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model_name": "qwen-plus",
    "api_key": "sk-xxx"
  }
]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger

from app.core.database import database_manager
from app.models.provider import LlmProviderEntity, LlmProviderStatus
from app.repositories.provider_repository import ProviderRepository
from app.services.provider_service import provider_service
from app.utils.logger import setup_logger


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量导入试用模型 Provider")
    parser.add_argument("--file", required=True, help="Provider JSON 文件路径")
    parser.add_argument("--default-code", required=True, help="要设为默认 Provider 的 provider_code")
    parser.add_argument(
        "--provider-type",
        default="chat",
        help="统一 Provider 类型，默认 chat",
    )
    return parser


async def _async_main(file_path: str, default_code: str, provider_type: str) -> int:
    setup_logger(force=True)
    payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("输入 JSON 顶层必须是数组")

    await database_manager.connect()
    session_factory = database_manager.get_session_factory()

    async with session_factory() as session:
        repository = ProviderRepository(session)
        imported_count = 0
        for item in payload:
            provider_code = str(item["provider_code"]).strip()
            existing = await repository.get_provider_by_code(provider_code)
            entity = existing or LlmProviderEntity(
                provider_code=provider_code,
                provider_name=str(item.get("provider_name", provider_code)).strip(),
            )
            entity.provider_name = str(item.get("provider_name", provider_code)).strip()
            entity.provider_type = provider_type
            entity.api_base = str(item.get("api_base", "")).strip() or None
            entity.model_name = str(item.get("model_name", "")).strip() or None
            entity.credentials_json = {
                "api_key": str(item.get("api_key", "")).strip(),
            }
            entity.settings_json = dict(item.get("settings_json", {}))
            entity.status = str(item.get("status", LlmProviderStatus.ACTIVE.value))
            entity.is_default = provider_code == default_code

            if existing is None:
                await repository.add_provider(entity)
            else:
                await repository.upsert_provider(entity)
            imported_count += 1

        default_provider = await repository.get_provider_by_code(default_code)
        if default_provider is None:
            raise RuntimeError(f"未找到默认 Provider: {default_code}")

        await repository.set_default_provider(default_provider.id)
        await session.commit()
        await provider_service.refresh_runtime_registry(session)

    await database_manager.close()
    logger.success("Provider 导入完成: imported_count={}, default_code={}", imported_count, default_code)
    return 0


def main() -> int:
    args = _build_arg_parser().parse_args()
    return asyncio.run(_async_main(args.file, args.default_code, args.provider_type))


if __name__ == "__main__":
    raise SystemExit(main())
