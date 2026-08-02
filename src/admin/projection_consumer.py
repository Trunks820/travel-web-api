from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import ValidationError

from src.admin.projection import (
    ProjectionPoisonError,
    ProjectionSequenceBlocked,
    apply_projection_event,
    apply_projection_heartbeat,
    pause_projection_stream,
)
from src.config import Settings

logger = logging.getLogger("travel_web_api.projection_consumer")


class ProjectionConsumer:
    def __init__(self, session_factory, settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self._stop = asyncio.Event()
        self._connection: Any = None

    async def stop(self) -> None:
        self._stop.set()
        if self._connection is not None:
            await self._connection.close()

    async def run(self) -> None:
        import aio_pika

        self._connection = await aio_pika.connect_robust(
            self.settings.projection_rabbitmq_url.get_secret_value()
        )
        channel = await self._connection.channel()
        await channel.set_qos(prefetch_count=1)
        exchange = await channel.declare_exchange(
            self.settings.projection_exchange_name,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        dead_exchange = await channel.declare_exchange(
            self.settings.projection_dead_letter_exchange_name,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        dead_queue = await channel.declare_queue(
            self.settings.projection_dead_letter_queue_name,
            durable=True,
            arguments={"x-queue-type": "quorum"},
        )
        await dead_queue.bind(
            dead_exchange,
            routing_key=self.settings.projection_dead_letter_routing_key,
        )
        queue = await channel.declare_queue(
            self.settings.projection_queue_name,
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-single-active-consumer": True,
                "x-dead-letter-exchange": self.settings.projection_dead_letter_exchange_name,
                "x-dead-letter-routing-key": (
                    self.settings.projection_dead_letter_routing_key
                ),
            },
        )
        await queue.bind(exchange, routing_key=self.settings.projection_routing_key)
        async with queue.iterator() as messages:
            async for message in messages:
                if self._stop.is_set():
                    return
                should_continue = await self._handle_message(message)
                if not should_continue:
                    return

    @staticmethod
    def _delivery_attempt(message: Any) -> int:
        headers = message.headers or {}
        # RabbitMQ 4.2 quorum queues report client-nack redeliveries via
        # x-acquired-count; x-delivery-count only grows on channel-loss
        # redelivery. Read both so bounded poison retry actually terminates
        # (verified against a disposable RabbitMQ 4.2.5 during P0).
        best = 0
        for header in ("x-delivery-count", "x-acquired-count"):
            try:
                best = max(best, int(headers.get(header, 0)))
            except (TypeError, ValueError):
                continue
        return best + 1

    async def _handle_message(self, message: Any) -> bool:
        try:
            payload = json.loads(message.body)
            if not isinstance(payload, dict):
                raise ValueError("projection message must be a JSON object")
            event_type = payload.get("event_type")
            if event_type == "TRIP_PROJECTION_COMMITTED":
                await apply_projection_event(self.session_factory, payload)
            elif event_type == "PROJECTION_HEARTBEAT":
                contiguous = await apply_projection_heartbeat(self.session_factory, payload)
                if not contiguous:
                    raise ProjectionSequenceBlocked(
                        "heartbeat is ahead of the contiguous watermark"
                    )
            else:
                raise ValueError("unsupported projection event type")
        except (json.JSONDecodeError, ValueError, ValidationError, ProjectionPoisonError):
            attempt = self._delivery_attempt(message)
            if attempt < self.settings.projection_max_delivery_attempts:
                await asyncio.sleep(self.settings.projection_retry_seconds)
                await message.nack(requeue=True)
                return True
            logger.error("projection poison message moved to DLQ", exc_info=True)
            await pause_projection_stream(self.session_factory)
            await message.reject(requeue=False)
            return False
        except ProjectionSequenceBlocked:
            await asyncio.sleep(self.settings.projection_retry_seconds)
            await message.nack(requeue=True)
            return True
        except Exception:
            logger.warning("projection head apply failed; retaining ordered head", exc_info=True)
            await asyncio.sleep(self.settings.projection_retry_seconds)
            await message.nack(requeue=True)
            return True
        await message.ack()
        return True
