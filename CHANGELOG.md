# Changelog

## 0.1.0

First release.

- One node per SpicyAPI image, video and audio model, generated from the live catalogue at
  startup (with a bundled snapshot for the first run before a key is set).
- Inputs follow each model's published schema: dropdowns, sliders, switches, image, video and
  audio sockets, growable groups for multi-reference models, and a LoRA socket.
- A price badge on every node, updated as the options change.
- Quote before every run, an optional per-run spending limit, and the charged amount on the node
  when it finishes.
- SpicyAPI Chat for text models, with image input, and SpicyAPI LoRA for stacking LoRA files.
- The API key is kept in the ComfyUI user folder, never in workflows or image metadata.
