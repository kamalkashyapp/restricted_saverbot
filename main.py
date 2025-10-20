import os, json, asyncio
from pyrogram import Client
import requests

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH"))
CLOUDFLARE_API = os.getenv("CLOUDFLARE_API")  # Worker KV endpoint

app = Client("user_session", api_id=API_ID, api_hash=API_HASH)

async def fetch_channel(channel_id):
    async with app:
        posts = []
        async for msg in app.get_chat_history(channel_id, limit=50):  # adjust limit
            item = {
                "id": msg.id,
                "text": msg.text or "",
                "media_type": str(msg.media),
                "caption": msg.caption or "",
            }

            # Optional: download media if you want
            if msg.media:
                file_path = await msg.download(file_name=f"downloads/{channel_id}_{msg.id}")
                item["file_path"] = file_path

            posts.append(item)

        # Upload to Cloudflare Worker
        for post in posts:
            requests.post(CLOUDFLARE_API, json=post)
        print(f"✅ Uploaded {len(posts)} posts from channel {channel_id}")

if __name__ == "__main__":
    channel_ids = [-1001234567890]  # replace with your private channels
    asyncio.run(fetch_channel(channel_ids[0]))
