# Changelog

## 0.1.6

- Settings are harder to change from outside the settings panel. Saving or removing the key and
  changing the spending limit now need a header the panel sends and, for saves, a JSON body, so
  another web page cannot change them even when ComfyUI runs with `--enable-cors-header`.
- New `SPICYAPI_LOCK_SETTINGS=1` turns those changes off entirely. Use it, together with
  `SPICY_API_KEY`, on a ComfyUI that other people can reach.
- A key is no longer saved unchecked when SpicyAPI cannot be reached; the key already in use stays
  in use and you are asked to try again.
- Thanks to Shiqiang Chen for reporting this responsibly.

## 0.1.5

- A key that cannot read the account balance is no longer reported as rejected. Saving a key checks
  it by reading the balance, so a valid key without the `read_balance` permission was turned away
  even though it could run models. Only an invalid key (HTTP 401) is refused now.

## 0.1.4

- Optional reference groups no longer force a connection. Nodes such as Wan 3.0 Reference to Video
  required at least one reference video and one reference audio even though the model accepts
  reference images alone; the first socket of an optional group is now optional too.

## 0.1.3

- Nodes for models that take no seed (Seedream, for example) get a local `seed` widget with
  **control after generate**. It is never sent to the model; set it to `randomize` or `increment`
  to get a new result each time you queue the same graph. It starts on `fixed`, so a repeated queue
  still reuses the last result instead of billing again.

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
