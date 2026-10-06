# Changelog

## 0.1.2

- The registry package leaves out tests, maintainer scripts, screenshots and CI files
  (`.comfyignore`); they stay in the GitHub repository. No change to the nodes.

## 0.1.1

- Example workflows get real thumbnails in ComfyUI's template browser, and the LoRA example comes
  ready to run with an Apache-2.0 pixel-art LoRA.
- Every model node's description links to its page on spicyapi.ai (prices, examples, parameters).
- README screenshots of the settings panel, node search and a LoRA chain.

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
