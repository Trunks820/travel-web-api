from __future__ import annotations

import json
from typing import Any

from src.config import Settings


class GuideFragmentCache:
    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> GuideFragmentCache:
        url = settings.admin_guide_cache_redis_url.get_secret_value().strip()
        if not url:
            return cls()
        from redis.asyncio import Redis

        return cls(Redis.from_url(url, decode_responses=True))

    @staticmethod
    def key(result_record_id: int, schema_version: str = "1.0") -> str:
        return f"yuntu:admin:guide-fragment:{schema_version}:{result_record_id}"

    async def get(self, result_record_id: int) -> dict[str, Any] | None:
        if self._client is None:
            return None
        value = await self._client.get(self.key(result_record_id))
        if value is None:
            return None
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None

    async def set(self, result_record_id: int, fragment: dict[str, Any]) -> None:
        if self._client is None:
            return
        await self._client.set(
            self.key(result_record_id),
            json.dumps(fragment, ensure_ascii=False, separators=(",", ":")),
            ex=1800,
        )

    async def invalidate(self, result_record_id: int) -> None:
        if self._client is not None:
            await self._client.delete(self.key(result_record_id))

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
