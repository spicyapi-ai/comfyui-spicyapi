"""SpicyAPI nodes for ComfyUI.

Pure modules (fields, pricing, catalog, config, client) import nothing from ComfyUI, so the
tests can run them with a plain Python interpreter. Only ``nodes``, ``media``, ``routes`` and
``extension`` touch ComfyUI, torch or PyAV.
"""

VERSION = "0.1.0"
