from pyrogram import Client
import asyncio, json, os

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

# First login will ask for phone number + code in Render logs (one time)
app = Client("restricted_session", api_id=API_ID, api_hash=API_HASH)

async def main():
    async with app:
        print("✅ Logged in successfully!")

        # Example: Replace with your private channel ID
        chat_id = -1001234567890
        print(f"Fetching posts from {chat_id}...")
        posts = []

        async for msg in app.get_chat_history(chat_id, limit=20):
            item = {
                "id": msg.id,
                "text": msg.text or "",
                "media": str(msg.media),
            }
            posts.append(item)

        with open("posts.json", "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        print("✅ Saved posts.json")

if __name__ == "__main__":
    asyncio.run(main())
