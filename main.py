import os
import json
import asyncio
from pyrogram import Client
import requests
import pathlib

# Environment variables
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
CLOUDFLARE_API = os.getenv("CLOUDFLARE_API")  # Worker KV endpoint

# Ensure downloads folder exists
pathlib.Path("downloads").mkdir(exist_ok=True)

# Pyrogram client (user account)
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
            try:
                response = requests.post(CLOUDFLARE_API, json=post)
                if response.status_code == 200:
                    print(f"✅ Uploaded post {post['id']}")
                else:
                    print(f"❌ Failed to upload post {post['id']} - {response.status_code}")
            except Exception as e:
                print(f"❌ Exception uploading post {post['id']}: {e}")

        print(f"✅ Finished uploading {len(posts)} posts from channel {channel_id}")

if __name__ == "__main__":
    channel_ids = [-1001234567890]  # Replace with your private channels
    # Run for all channels (optional)
    async def main():
        for cid in channel_ids:
            await fetch_channel(cid)

    asyncio.run(main())
