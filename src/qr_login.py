import asyncio
import os
from getpass import getpass

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

load_dotenv()

api_id = int(os.environ["TG_API_ID"])
api_hash = os.environ["TG_API_HASH"]
session = os.environ.get("TG_SESSION", "forwarder.session")

async def main():
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()

    # Если уже авторизованы — выходим
    if await client.is_user_authorized():
        me = await client.get_me()
        print("Already authorized as:", me.username or me.id)
        await client.disconnect()
        return

    try:
        qr = await client.qr_login()
        print("Scan this QR (convert the URL to QR):")
        print(qr.url)
        await qr.wait()

    except SessionPasswordNeededError:
        # Для вашего аккаунта 2FA требуется уже на этапе export token
        pwd = getpass("2FA password: ")
        await client.sign_in(password=pwd)

        # После успешного ввода 2FA мы уже авторизованы.
        # Никакой второй QR не делаем.
        me = await client.get_me()
        print("Logged in (after 2FA) as:", me.username or me.id)
        await client.disconnect()
        return

    me = await client.get_me()
    print("Logged in as:", me.username or me.id)
    await client.disconnect()

asyncio.run(main())
