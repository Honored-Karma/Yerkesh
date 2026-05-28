from .common import router as common_router
from .chat import router as chat_router
from .voice import router as voice_router
from .vision import router as vision_router
from .admin import router as admin_router
from .mcp_handler import router as mcp_router
from .calendar_handler import router as calendar_router

__all__ = [
    "common_router",
    "chat_router",
    "voice_router",
    "vision_router",
    "admin_router",
    "mcp_router",
    "calendar_router",
]
