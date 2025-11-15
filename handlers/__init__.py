from .start import register_start_handlers
from .admin import register_admin_handlers
from .albums import register_album_handlers
from .callback import register_callback_handlers

def register_all_handlers(dp):
    register_start_handlers(dp)
    register_admin_handlers(dp)
    register_album_handlers(dp)
    register_callback_handlers(dp)
