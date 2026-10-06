"""SpicyAPI for ComfyUI: every SpicyAPI image, video and audio model as a node."""

from .spicyapi_nodes.extension import comfy_entrypoint

WEB_DIRECTORY = "./web"

__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
