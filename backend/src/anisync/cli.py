"""Maintenance commands: `uv run anisync <command>`.

generate-key   print a new encryption key
rotate-keys    re-encrypt every stored provider token with the primary key (see crypto.py)
seed           import the offline ID dataset (see seed.py); `--force` re-imports the current version
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anisync import crypto, logs
from anisync.models import LinkedAccount

BATCH = 200


@dataclass
class RotationResult:
    tokens: int = 0  # stored tokens looked at
    rotated: int = 0  # re-encrypted with the primary key
    failed: int = 0  # no configured key can read them


async def rotate_all_tokens(session: AsyncSession) -> RotationResult:
    """Re-encrypt every provider token that isn't under the primary key yet. Safe to run repeatedly."""
    result = RotationResult()
    last_id = 0
    while accounts := (
        await session.scalars(
            select(LinkedAccount).where(LinkedAccount.id > last_id).order_by(LinkedAccount.id).limit(BATCH)
        )
    ).all():
        for account in accounts:
            for column in ("_access_token", "_refresh_token"):
                value = getattr(account, column)
                if value is None:
                    continue
                result.tokens += 1
                try:
                    if crypto.needs_rotation(value):
                        setattr(account, column, crypto.rotate(value))
                        result.rotated += 1
                except crypto.InvalidToken:
                    result.failed += 1
        await session.commit()
        last_id = accounts[-1].id
    return result


async def _rotate_keys() -> int:
    from anisync.db import SessionLocal

    async with SessionLocal() as session:
        result = await rotate_all_tokens(session)
    print(f"{result.tokens} tokens: {result.rotated} re-encrypted, {result.failed} unreadable")
    if result.failed:
        print("Some tokens can't be read with the configured keys. Keep the old key until they are re-linked.")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anisync", description="AniSyncHub maintenance commands")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("generate-key", help="print a new token encryption key")
    commands.add_parser("rotate-keys", help="re-encrypt stored tokens with the primary key")
    seed = commands.add_parser("seed", help="import the offline ID dataset")
    seed.add_argument("--force", action="store_true", help="import even if this version was imported before")
    args = parser.parse_args(argv)

    if args.command == "generate-key":
        print(Fernet.generate_key().decode())
        return 0
    logs.configure()
    if args.command == "rotate-keys":
        return asyncio.run(_rotate_keys())
    from anisync import seed as seed_module

    asyncio.run(seed_module._main(["--force"] if args.force else []))
    return 0


if __name__ == "__main__":
    sys.exit(main())
