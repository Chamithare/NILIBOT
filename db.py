# db.py
import os
import secrets
import logging
from typing import List, Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

MONGO_URI = os.getenv("MONGODB_URI")
if not MONGO_URI:
    raise SystemExit("MONGO_URI not set in env")

_client = AsyncIOMotorClient(MONGO_URI)
_db = _client["album_bot_v1"]
_albums = _db["albums"]

async def save_album(files: List[str], caption: str = "") -> str:
    """
    Save an album (list of file tokens like "photo:<file_id>" or "doc:<file_id>").
    Returns generated album_id.
    """
    album_id = secrets.token_urlsafe(8)
    doc = {
        "album_id": album_id,
        "files": files,
        "caption": caption or "",
        "created_at": int(__import__("time").time()),
        "published": []
    }
    await _albums.insert_one(doc)
    logger.info("Saved album %s with %d files", album_id, len(files))
    return album_id

async def get_album(album_id: str) -> Optional[Dict[str, Any]]:
    """
    Return album document by album_id or None if not found.
    """
    doc = await _albums.find_one({"album_id": album_id})
    return doc

async def mark_published(album_id: str, chat_id: int, message_ids: List[int]):
    """
    Record published instance (useful for auto-delete later).
    """
    await _albums.update_one(
        {"album_id": album_id},
        {"$push": {"published": {"chat_id": chat_id, "message_ids": message_ids, "at": int(__import__("time").time())}}}
    )
