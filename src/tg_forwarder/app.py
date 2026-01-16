from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

from .config import Settings
from .matcher import KeywordMatcher
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(level: str) -> logging.Logger:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("tg_forwarder")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    # консоль
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # файл с ротацией
    fh = RotatingFileHandler(
        log_dir / "tg_forwarder.log",
        maxBytes=2_000_000,   # 2MB
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


@dataclass
class ForwardEvent:
    account: str
    chat_id: int
    chat_title: str
    chat_username: str | None
    msg_id: int
    message: Any


def message_link(chat_id: int, username: str | None, msg_id: int) -> str | None:
    if username:
        return f"https://t.me/{username}/{msg_id}"

    s = str(chat_id)
    # Telethon часто даёт супер-группы как -100xxxxxxxxxx
    if s.startswith("-100"):
        internal = s[4:]
        return f"https://t.me/c/{internal}/{msg_id}"

    # Иногда у вас уже internal_id (как 2201638710)
    if chat_id > 0:
        return f"https://t.me/c/{chat_id}/{msg_id}"

    return None


async def run_listener(
    name: str,
    client: TelegramClient,
    sources: list[str],
    matcher: KeywordMatcher,
    queue: asyncio.Queue[ForwardEvent],
    log: logging.Logger,
) -> None:
    me = await client.get_me()
    log.info("[listener:%s] ready as %s", name, getattr(me, "username", None) or me.id)
    # resolve sources once
    source_entities = [await client.get_entity(x) for x in sources]
    log.info("[listener:%s] sources resolved: %d", name, len(source_entities))

    @client.on(events.NewMessage(chats=source_entities))
    async def on_msg(e: events.NewMessage.Event) -> None:
        text = e.message.raw_text or ""
        if not matcher.matches(text):
            return

        chat = await e.get_chat()
        chat_id = int(getattr(chat, "id", None) or e.chat_id)
        chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or str(chat_id)
        chat_username = getattr(chat, "username", None)

        await queue.put(
            ForwardEvent(
                account=name,
                chat_id=chat_id,
                chat_title=str(chat_title),
                chat_username=chat_username,
                msg_id=int(e.message.id),
                message=e.message,
            )
        )
        log.info("[listener:%s] match | chat=%s msg_id=%s", name, chat_title, e.message.id)


    log.info("[listener:%s] running…", name)
    await client.run_until_disconnected()



async def run_sender(
    sender_client: TelegramClient,
    target_chat: str,
    queue: asyncio.Queue["ForwardEvent"],
    log: logging.Logger,
) -> None:
    target = await sender_client.get_entity(target_chat)
    log.info("[sender] ready, target resolved")

    def _make_header(ev: "ForwardEvent", link: str | None) -> str:
        return (
            f"Source: {ev.chat_title}\n"
            f"Link: {link or 'n/a'}\n"
        )

    async def _send_header(ev: "ForwardEvent", link: str | None) -> None:
        # header always as a separate message (as you had before)
        header = _make_header(ev, link)
        await sender_client.send_message(target, header, link_preview=False)

    async def _send_copy(ev: "ForwardEvent", link: str | None) -> None:
        # Keep header separate, then send message text (if any) as another message
        # await _send_header(ev, link)

        msg_text = (getattr(ev.message, "message", None) or "").strip()
        if msg_text:
            msg_text = msg_text[:3500]
            await sender_client.send_message(target, msg_text, link_preview=False)

    async def _deliver(ev: "ForwardEvent", *, send_header: bool = True) -> str:
        link = message_link(ev.chat_id, ev.chat_username, ev.msg_id)

        if send_header:
            await _send_header(ev, link)

        try:
            await sender_client.forward_messages(target, ev.message)
            return "forward"
        except FloodWaitError:
            raise
        except ValueError:
            await _send_copy(ev, link)
            return "copy"
        except Exception:
            log.exception(
                "[sender] forward failed, falling back to copy "
                "(source_account=%s chat=%s chat_id=%s msg_id=%s)",
                ev.account, ev.chat_title, ev.chat_id, ev.msg_id
            )
            await _send_copy(ev, link)
            return "copy"

    while True:
        ev = await queue.get()
        try:
            mode = await _deliver(ev, send_header=True)
            log.info(
                "[sender] delivered | mode=%s | source_account=%s | from=%s (chat_id=%s) msg_id=%s | to=%s",
                mode,
                ev.account,
                ev.chat_title,
                ev.chat_id,
                ev.msg_id,
                target_chat,
            )
        except FloodWaitError as ex:
            log.warning(
                "[sender] FloodWait %ss | will retry once | source_account=%s chat_id=%s msg_id=%s",
                ex.seconds,
                ev.account,
                ev.chat_id,
                ev.msg_id,
            )
            await asyncio.sleep(ex.seconds)

            # retry once after waiting
            try:
                mode = await _deliver(ev, send_header=False)
                log.info(
                    "[sender] delivered_after_floodwait | mode=%s | source_account=%s | from=%s (chat_id=%s) msg_id=%s | to=%s",
                    mode,
                    ev.account,
                    ev.chat_title,
                    ev.chat_id,
                    ev.msg_id,
                    target_chat,
                )
            except Exception:
                log.exception(
                    "[sender] Failed to deliver after FloodWait "
                    "(source_account=%s chat_id=%s msg_id=%s)",
                    ev.account,
                    ev.chat_id,
                    ev.msg_id,
                )
        except Exception:
            log.exception(
                "[sender] Failed to deliver event (source_account=%s chat_id=%s msg_id=%s)",
                ev.account,
                ev.chat_id,
                ev.msg_id,
            )
        finally:
            queue.task_done()



async def run() -> None:
    s = Settings()
    cfg = s.load_yaml()

    log = setup_logging(s.effective_log_level(cfg))

    matcher = KeywordMatcher.build(
        keywords=[k.lower() for k in cfg.matcher.keywords],
        keyword_regex=cfg.matcher.regex,
    )

    queue: asyncio.Queue[ForwardEvent] = asyncio.Queue(maxsize=1000)

    # 1) создаём клиентов для всех аккаунтов
    clients: dict[str, TelegramClient] = {
        acc.name: TelegramClient(acc.session, s.tg_api_id, s.tg_api_hash)
        for acc in cfg.accounts
    }

    # 2) sender client = один из них
    if cfg.send_via not in clients:
        raise RuntimeError(f"send_via={cfg.send_via} not found in accounts")

    sender_client = clients[cfg.send_via]

    tasks: list[asyncio.Task] = []

    # стартуем всех клиентов ровно один раз
    for name, client in clients.items():
        await client.start()
        me = await client.get_me()
        log.info("[client:%s] authorized as %s", name, getattr(me, "username", None) or me.id)


    # 3) запускаем sender loop (использует sender_client)
    tasks.append(asyncio.create_task(run_sender(sender_client, cfg.target.chat, queue, log)))

    # 4) запускаем listeners для ВСЕХ клиентов (в т.ч. sender аккаунта),
    # но каждый listener использует СВОЙ client из dict, без дублей
    for acc in cfg.accounts:
        tasks.append(
            asyncio.create_task(
                run_listener(
                    name=acc.name,
                    client=clients[acc.name],
                    sources=acc.sources,
                    matcher=matcher,
                    queue=queue,
                    log=log,
                )
            )
        )

    try:
        await asyncio.gather(*tasks)
    except Exception:
        log.exception("Fatal error: main tasks crashed")
        raise


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
