from __future__ import annotations

import argparse
import asyncio

import asyncpg


async def create_database(host: str, port: int, database: str) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("local PostgreSQL helper refuses non-local hosts")
    if not database.startswith("travel_web_test"):
        raise SystemExit("test database name must start with travel_web_test")
    connection = await asyncpg.connect(
        host=host,
        port=port,
        user="postgres",
        database="postgres",
    )
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            database,
        )
        if not exists:
            quoted = '"' + database.replace('"', '""') + '"'
            await connection.execute(f"CREATE DATABASE {quoted}")  # noqa: S608
    finally:
        await connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--database", default="travel_web_test")
    args = parser.parse_args()
    asyncio.run(create_database(args.host, args.port, args.database))


if __name__ == "__main__":
    main()
