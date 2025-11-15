import os
from motor.motor_asyncio import AsyncIOMotorClient

# --- CONFIG ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "telegram_bot_db")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "albums")

# --- INIT CLIENT ---
client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]

# --- SAVE ALBUM ---
async def save_album(payload: str, media_list: list):
    """
    Save album to MongoDB.
    media_list: list of dicts -> [{"type": "photo", "file_id": "..."}]
    """
    if not payload or not media_list:
        return False
    doc = {
        "payload": payload,
        "media": media_list
    }
    result = await collection.update_one(
        {"payload": payload},
        {"$set": doc},
        upsert=True
    )
    return result.upserted_id or True

# --- GET ALBUM ---
async def get_album(payload: str):
    """
    Fetch album by payload.
    Returns list of media dicts or None
    """
    if not payload:
        return None
    doc = await collection.find_one({"payload": payload})
    if doc:
        return doc.get("media", [])
    return None

# --- DELETE ALBUM (optional) ---
async def delete_album(payload: str):
    result = await collection.delete_one({"payload": payload})
    return result.deleted_count > 0
