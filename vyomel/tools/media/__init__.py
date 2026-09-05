"""Media plugin: FFmpeg-backed tools on the shared Vyomel runtime (M14 / FR-607)."""

from vyomel.tools.media.tools import register_media_tools

__all__ = ["register_media_tools"]
