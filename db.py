import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGO_URI)
db = client["telegram_bot"]
albums_col = db["albums"]

async def save_album(album_id, media_files):
    await albums_col.update_one(
        {"album_id": album_id},
        {"$set": {"media": media_files}},
        upsert=True
    )

async def get_album(album_id):
    album = await albums_col.find_one({"album_id": album_id})
    if album:
        return album.get("media", [])
    return []

