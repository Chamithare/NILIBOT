import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "album_bot")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "albums")

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]


async def save_album(payload: str, media_ids: list):
    """Save album to DB."""
    return await collection.update_one(
        {"payload": payload},
        {"$set": {"media_ids": media_ids}},
        upsert=True,
    )


async def get_album(payload: str):
    """Get album from DB."""
    return await collection.find_one({"payload": payload})
