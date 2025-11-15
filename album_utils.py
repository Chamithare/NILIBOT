# album_utils.py
from aiogram import types
from typing import List

def extract_file_ids(message: types.Message) -> List[str]:
    """
    Extract a normalized file token list from a Message.
    Returns list like ["photo:<file_id>", "doc:<file_id>"].

    Note:
    - For photos returns "photo:<file_id>" (sends as InputMediaPhoto).
    - For videos/documents/animation/audio returns "doc:<file_id>" (sent as document).
    """
    out = []
    # photo (pick largest size)
    if message.photo:
        out.append(f"photo:{message.photo[-1].file_id}")
        return out

    # video -> treat as document (telegram allows InputMediaVideo but bot sends as document in final)
    if message.video:
        out.append(f"doc:{message.video.file_id}")
        return out

    # document
    if message.document:
        out.append(f"doc:{message.document.file_id}")
        return out

    # animation / audio fallback to document
    if message.animation:
        out.append(f"doc:{message.animation.file_id}")
        return out

    if message.audio:
        out.append(f"doc:{message.audio.file_id}")
        return out

    return out



