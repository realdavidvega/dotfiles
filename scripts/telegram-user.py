#!/usr/bin/env python3
"""Act as the Telegram user account where the Bot API cannot.

A bot can neither change a person's notification settings nor list a chat's
history, and it deletes only its own recent messages. Muting a fresh topic and
clearing a topic both need a person, so this runs as the user over MTProto with
Telethon. agent-bridge.py and black-system.py stay stdlib only and call it from
its own venv, so one login serves both groups.

Usage:
  telegram-user.py login
  telegram-user.py status
  telegram-user.py mute CHAT_ID THREAD_ID
  telegram-user.py clear CHAT_ID [--thread N]... [--general] [--keep MESSAGE_ID]...
                         [--older-than-days DAYS] [--dry-run]

The account lives in /srv/services/telegram/user.env, mode 0600:
TELEGRAM_USER_API_ID and TELEGRAM_USER_API_HASH from my.telegram.org, and
TELEGRAM_USER_SESSION, which login writes and never prints. Every command but
login prints one JSON line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

ENV_FILE = os.environ.get("TELEGRAM_USER_ENV") or "/srv/services/telegram/user.env"
SESSION_KEY = "TELEGRAM_USER_SESSION"
# Telegram's own "mute forever" is the largest signed 32-bit timestamp.
MUTE_FOREVER = 2**31 - 1
# channels.deleteMessages takes at most 100 ids per call.
BATCH = 100


class Refusal(Exception):
    pass


def load_env(path: str) -> None:
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key and not key.startswith("#"):
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except OSError:
        pass


def credentials() -> tuple[int, str]:
    try:
        return int(os.environ["TELEGRAM_USER_API_ID"]), os.environ["TELEGRAM_USER_API_HASH"]
    except (KeyError, ValueError) as exc:
        raise Refusal(f"set TELEGRAM_USER_API_ID and TELEGRAM_USER_API_HASH in {ENV_FILE}") from exc


async def connect() -> TelegramClient:
    raw = os.environ.get(SESSION_KEY, "").strip()
    if not raw:
        raise Refusal(f"no {SESSION_KEY} yet. Run: telegram-user.py login")
    try:
        session = StringSession(raw)
    except ValueError as exc:
        raise Refusal(f"{SESSION_KEY} is not a Telethon session. Run login again") from exc
    client = TelegramClient(session, *credentials())
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise Refusal("the session is logged out. Run login again")
        # A bot token typed at the login prompt makes a working bot session,
        # which can do neither job and would fail only when first needed.
        if await client.is_bot():
            raise Refusal("the session is a bot, not your account. Run login with your phone number")
    except BaseException:
        await client.disconnect()
        raise
    return client


async def resolve(client: TelegramClient, chat_id: int):
    try:
        return await client.get_input_entity(chat_id)
    except ValueError:
        # A string session keeps no entity cache, so the group's access hash
        # is unknown until the dialogs have been listed once.
        await client.get_dialogs()
        return await client.get_input_entity(chat_id)


async def status(client: TelegramClient, _args: argparse.Namespace) -> dict:
    me = await client.get_me()
    return {"ok": True, "user": me.first_name}


async def mute(client: TelegramClient, args: argparse.Namespace) -> dict:
    peer = await resolve(client, args.chat_id)
    await client(functions.account.UpdateNotifySettingsRequest(
        peer=types.InputNotifyForumTopic(peer=peer, top_msg_id=args.thread_id),
        settings=types.InputPeerNotifySettings(mute_until=MUTE_FOREVER),
    ))
    return {"ok": True, "muted": args.thread_id}


def deletable(message, keep: set[int], cutoff: datetime | None) -> bool:
    if message.id in keep:
        return False
    # A topic's creation message is its root, and Telegram never deletes it.
    if isinstance(getattr(message, "action", None), types.MessageActionTopicCreate):
        return False
    return cutoff is None or message.date < cutoff


def in_topic(message) -> bool:
    """Whether a message belongs to a topic other than General."""
    reply = message.reply_to
    return bool(reply and getattr(reply, "forum_topic", False))


async def clear(client: TelegramClient, args: argparse.Namespace) -> dict:
    peer = await resolve(client, args.chat_id)
    keep = set(args.keep)
    cutoff = None
    if args.older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than_days)
    found: dict[str, list[int]] = {}
    for thread in args.thread:
        ids = found.setdefault(str(thread), [])
        async for message in client.iter_messages(peer, reply_to=thread, offset_date=cutoff):
            if message.id != thread and deletable(message, keep, cutoff):
                ids.append(message.id)
    if args.general:
        ids = found.setdefault("general", [])
        async for message in client.iter_messages(peer, offset_date=cutoff):
            if not in_topic(message) and deletable(message, keep, cutoff):
                ids.append(message.id)
    doomed = sorted({message_id for ids in found.values() for message_id in ids})
    if not args.dry_run:
        for start in range(0, len(doomed), BATCH):
            await client.delete_messages(peer, doomed[start:start + BATCH])
    return {"ok": True, "dry_run": args.dry_run, "deleted": len(doomed),
            "by_topic": {topic: len(ids) for topic, ids in found.items()}}


def ask_phone() -> str:
    while True:
        answer = input("Phone number with country code, e.g. +34600000000: ").strip()
        if ":" in answer:
            print("That is a bot token. This needs your own account, so enter your phone number.")
            continue
        if answer:
            return answer


def store_session(session: str) -> None:
    """Replace the session line in the env file, which stays 0600."""
    try:
        with open(ENV_FILE, encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if not line.startswith(f"{SESSION_KEY}=")]
    except FileNotFoundError:
        lines = []
    lines.append(f"{SESSION_KEY}={session}")
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


async def login() -> str:
    client = TelegramClient(StringSession(), *credentials())
    try:
        await client.start(phone=ask_phone)
        if await client.is_bot():
            raise Refusal("that logged in a bot. Run login again with your phone number")
        me = await client.get_me()
        session = client.session.save()
    finally:
        await client.disconnect()
    store_session(session)
    return f"Logged in as {me.first_name}. Stored {SESSION_KEY} in {ENV_FILE}."


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("login", help="log in with your phone number and store the session")
    sub.add_parser("status", help="check the stored session")
    mute_cmd = sub.add_parser("mute", help="mute one forum topic for your account")
    mute_cmd.add_argument("chat_id", type=int)
    mute_cmd.add_argument("thread_id", type=int)
    clear_cmd = sub.add_parser("clear", help="delete messages in topics")
    clear_cmd.add_argument("chat_id", type=int)
    clear_cmd.add_argument("--thread", type=int, action="append", default=[], help="a topic id, repeatable")
    clear_cmd.add_argument("--general", action="store_true", help="also the General topic")
    clear_cmd.add_argument("--keep", type=int, action="append", default=[], help="a message id to keep, repeatable")
    clear_cmd.add_argument("--older-than-days", type=float, help="only messages older than this")
    clear_cmd.add_argument("--dry-run", action="store_true", help="count, delete nothing")
    return ap


async def run(args: argparse.Namespace) -> dict | str:
    if args.command == "login":
        return await login()
    client = await connect()
    try:
        return await {"status": status, "mute": mute, "clear": clear}[args.command](client, args)
    finally:
        await client.disconnect()


def main(argv: list[str]) -> int:
    load_env(ENV_FILE)
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Refusal as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except Exception as exc:  # noqa: BLE001 - the calling bot shows the reason
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(result if isinstance(result, str) else json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
