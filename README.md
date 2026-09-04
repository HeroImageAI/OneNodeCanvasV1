# One Node Canvas · FLUX.2 [klein]

An infinite design board for FLUX.2 [klein], inside a single ComfyUI node.

No graph to build and no wires to connect. You get a pannable, zoomable canvas where images are
frames you arrange, connect and iterate on — closer to a design tool than a node editor. Built
for product and apparel work: concepting, colourways, technical flats and handoff.

<!-- TODO: record a short demo of the board and drop it in as assets/demo.gif -->

---

## What it is

The node is one DOM widget hosting a **board**. Two kinds of thing live on it:

**Frames** — images. Generated results, uploads, sketches, or anything from the gallery. Drag,
group, tag, rename and arrange them.

**Blocks** — the things that make images. Connect one or more frames to a block, pick what it
should do, and press Generate. The results land back on the board, linked to their sources so
you can trace where anything came from.

Boards are saved as **projects** and persist across restarts.

## What it does

Select frames and open **Actions**, or connect them to a block:

**Render** — re-render from the prompt. With a source it holds the composition; without one it
generates from text and offers an aspect-ratio row.

**Variations** — alternatives around the same design. Explores form, deliberately holding the
palette.

**New Views** — front, side, rear, 3/4, top or detail. One queued generation per view selected,
consistent product and materials across all of them.

**Modify** — change something by instruction. The one that repaints materials and colours while
holding the silhouette.

**Refine** — a finishing pass at the same size. Not an upscale.

**Expand** — grow the frame outwards and let the model invent the new area.

**Cut out** — lift the subject off its background to a transparent PNG.

**Upscale** — 2×, 4×, 6× or 8× with SeedVR2.

**Extract Colors** — pull a palette out of an image, edit the swatches, save it to your library.

**Pose** and **Faceswap** — copy a pose, or a face, from a second image.

### Reference images

Any source connected to a block starts as **Composite** — several sources are merged into one
image and worked on together. Click the label on the connection line to switch a source to
**Reference** instead, and it becomes a style donor rather than content: its colours and
materials influence the result while the other source supplies the shape.

This is how you apply the look of one image to the drawing of another. It works on the
instruction-driven modes — Modify, New Views and the restaging Variations presets.

### Organising the board

**Lineage** — every generated frame records what it came from. Ask any frame to show its
ancestry and the chain back to the original source is drawn on the board.

**Groups and tags** — collect related frames into a named group, or tag them and use search to
select every match at once.

**Compare** — put two frames side by side with a wipe, to judge a change properly rather than by
memory.

**Contact sheet** — lay a selection out as a single sheet for review or handoff.

**Arrange** — align and distribute a selection, so a board stays readable once it has fifty
images on it.

### The editor

Double-click a frame for a layered editor: brush, eraser, fill, line, rectangle, ellipse, lasso,
move and transform, with optional mirrored symmetry. Paint an inpaint mask and regenerate only
that area, blend a layer in, or run any board action from inside it.

Export the whole document as a layered **PSD**, or any single layer as PNG.

### SVG export

Line and vector artwork can be traced to real vector paths. Select frames and press **SVG**, or
export from the editor.

Two engines, chosen per image: **potrace** for line art (bitonal outline tracing) and **vtracer**
for flat colour artwork (colour region tracing). The dialog previews every trace before anything
is saved, with an engine override and a Simplify control.

Photographs are detected and refused — tracing one produces thousands of unusable paths — with a
"Trace anyway" escape if you really want it.

---

## Installation

Clone into your ComfyUI `custom_nodes` folder:

```
git clone https://github.com/HeroImageAI/OneNodeCanvasV1.git
```

Install the Python dependencies (ComfyUI-Manager does this automatically from
`requirements.txt`):

```
pip install -r requirements.txt
```

These power PSD export (`psd-tools`) and SVG export (`potracer`, `vtracer`). The node loads
without them; those features report what is missing.

You also need one additional custom node for inpaint and outpaint modes:
[ComfyUI-Inpaint-CropAndStitch](https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch) by
lquesada. Clone it into the same folder:

```
git clone https://github.com/lquesada/ComfyUI-Inpaint-CropAndStitch.git
```

For POSE mode you also need
[comfyui_controlnet_aux](https://github.com/Fannovel16/comfyui_controlnet_aux) by Fannovel16,
which provides the DWPose preprocessor.

### A note on exposed instances

This node adds HTTP routes under `/flux_klein_canvas/`, and like all ComfyUI routes they are
**unauthenticated**. Paths are validated and confined to ComfyUI's own input/output folders, but
if you run ComfyUI with `--listen` on an untrusted network, anyone who can reach the port can
read, write and delete images in the node's gallery folder. Run it on localhost, or put it
behind authentication.

---

## Models

This node works with any FLUX.2 [klein] model officially released by Black Forest Labs.

You will find all officially released FLUX.2 [klein] models on the [Black Forest Labs HuggingFace page](https://huggingface.co/collections/black-forest-labs/flux2). Pick the variant that fits your VRAM and use case. You will need a diffusion model, a matching text encoder, and the VAE.

The Faceswap LoRA is required for the Faceswap mode, and the Pose LoRA for the POSE mode. The BiRefNet model is optional, only needed for the Remove Background feature in PAINT mode.

**Text encoder** (place in `models/text_encoders/`)
- [qwen_3_8b for 9b models](https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b/tree/main/split_files/text_encoders)
- [qwen_3_4b for 4b model](https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-4b/tree/main/split_files/text_encoders)

**VAE** (place in `models/vae/`)
- [flux2-vae](https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b/tree/main/split_files/vae)

**Faceswap LoRA** (place in `models/loras/`)
- [BFS Head Swap v1 (9b)](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap/blob/main/bfs_head_v1_flux-klein_9b_step3500_rank128.safetensors)
- [BFS Head Swap v1 (4b)](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap/blob/main/bfs_head_v1_flux-klein_4b.safetensors)

**Pose LoRA** (place in `models/loras/`) — for POSE mode
- [RefControl v2 Poses (9b)](https://huggingface.co/thedeoxen/refcontrol-FLUX.2-klein-9B-reference-pose-lora/blob/main/refcontrol_v2_poses.safetensors)

**Remove Background** (place in `models/background_removal/`)
- [birefnet](https://huggingface.co/Comfy-Org/BiRefNet/tree/main/background_removal)

**Upscaler (SeedVR2)** — for UPSCALE mode

The UPSCALE mode uses SeedVR2, a fast and genuinely great upscaler. I picked it as the main upscaler because it works really well, especially on small resolutions and pixelated images where most upscalers struggle.

**Upscale model** (place in `models/diffusion_models/`)
- [SeedVR2 diffusion models](https://huggingface.co/Comfy-Org/SeedVR2/tree/main/diffusion_models) — pick the one that fits your VRAM and speed needs

**Upscale VAE** (place in `models/vae/`)
- [ema_vae_fp16](https://huggingface.co/Comfy-Org/SeedVR2/blob/main/vae/ema_vae_fp16.safetensors)

---

---

## License note on FLUX.2 [klein] 9B

This node works with both the 4B and 9B variants of FLUX.2 [klein]. The 4B model is released under Apache 2.0 and can be used freely including commercially.

The 9B model is released under the **FLUX Non-Commercial License** by Black Forest Labs. This means you can use it for personal and research purposes, but commercial use is not permitted. If you use the 9B model, you are responsible for complying with that license.

This node itself is fully open source with no restrictions.

---

Thanks. Now go make something cool. :)

---

Built with the help of [Claude](https://claude.ai) by Anthropic.

---

---

## Credits and licence

Created by JP de Beer.

This project is a fork of [one-node-flux-2-klein](https://github.com/yanokusnir-ai/one-node-flux-2-klein)
by yanokusnir, which supplied the original single-node FLUX.2 [klein] workflow this was built on.

Released under the MIT licence — see [LICENSE](LICENSE), which also records what is and is not
covered, since the upstream project shipped no licence file of its own.

### Original node's tutorial

These videos cover the **upstream** node, before the board existed. They are still a good
introduction to the underlying FLUX.2 [klein] workflow, but the interface shown is not this one.

▶ [Watch on YouTube](https://youtu.be/L4ItbBWXqCo) · [later update](https://youtu.be/Vsp1tDFipHE)

---

## Changelog

_Entries below this point are inherited from the upstream project and describe the original
node._

### July 17, 2026

**New UPSCALE mode (SeedVR2)**

A dedicated Upscale mode powered by SeedVR2. Pick a scale of 2x, 4x, 6x or 8x and let it restore detail. There is also an optional "Scale by longer side" that shrinks the source first, which sounds backwards but SeedVR2 shines on small and pixelated inputs, so downscaling before the upscale often gives cleaner results.

You can also upscale straight from the preview. After any generation a small Upscale button appears in the bottom left corner, so you can upscale the image you just made (or the selected one in a batch) without switching modes.

SeedVR2 needs its own model and VAE, set both in Settings. See the Models section above for the download links.

**Denoise control for Inpaint**

The inpaint editor now has a Change strength slider, same idea as in Image to Image. At 100% it fully repaints the masked area (the default, unchanged behaviour), lower values keep more of the original content under the mask for subtler edits. Thanks @ZeroCool22 for the great idea.

---

### July 4, 2026

**Reference-guided inpainting**

The inpaint editor now has an optional reference image slot in the top right. Drop an image in and the model uses it to fill the masked area, so you can paint an object, an outfit, or a face straight into a specific spot. Everything outside your mask stays untouched. Leave the slot empty and inpaint works exactly as before. You can also paste a reference straight in with Ctrl+V while the editor is open.

**Batch generation**

Generate up to 4 images in a single run. Works in Text to Image, Image to Image, Edit, Faceswap and Pose. Inpaint and Outpaint run one image at a time, because of how the result is merged back into the original.

**Node output and prompt input**

The node now has an image output, so your result can flow into the rest of your graph, like an upscaler or any other node. It also has a prompt input, so you can feed it a prompt from another node.

**Set image as output from the gallery**

Open any image in the gallery and push it to the node's output with the new "Set as output" button.

**Auto-save toggle**

You can now turn off auto-save. When it's off, results show up as a preview first and you hit Save to keep only the ones you want.

**Canvas-like zoom and pan**

Scroll to zoom and middle-mouse drag to pan while hovering over the node, just like the rest of the ComfyUI canvas.

---

### June 26, 2026

**New POSE mode**

Copy the pose from one image onto the character from a reference image. A DWPose skeleton drives the pose while the reference image drives the appearance, through a RefControl pose LoRA. Requires the comfyui_controlnet_aux node and a RefControl pose LoRA, see the Installation and Models sections.

**Bigger preview layout**

A new layout toggle in the top bar (just right of the Settings button) moves the prompt into the sidebar so the preview window gets the full height, which is handy for portrait images. The classic wide-prompt layout stays the default.

**Keep GGUF connected when toggling External Models off**

The External Models toggle is now the single source of truth. Turning it off keeps your external loader wired but uses the internal dropdowns, so you can switch between the built-in models and an external setup without reconnecting anything.

**Per-slot LoRA on/off toggle**

Each LoRA slot now has a switch, so you can deactivate a LoRA while keeping it loaded, without losing its strength value. This replaces the old per-slot clear button. Thanks to @triatomic for the contribution.

**Paint shortcuts and inpaint marquee**

`[` and `]` change the brush size in the Sketch editor (`{` / `}` for bigger steps), and the inpaint mask editor gains a rectangle marquee tool (`R`) for masking a rectangular area. Thanks to @triatomic.

**Outpaint seam feather**

A Seam feather slider in the outpaint editor controls how far the mask fades into the original, so you can soften visible seams. Defaults to Auto (the previous behaviour).

**More reliable LoRA strength drag**

The drag-to-scrub on LoRA strength now works consistently, including fast flicks and drags started near the edge of the field. Thanks to @triatomic.

---

### June 23, 2026

**More LoRA slots**

The LoRA panel now starts with 3 slots and you can add up to 6 with the "+ Add slot" button (and remove extras with "Remove last slot"). The panel was also redesigned to be cleaner, with collapsible trigger words and a scrollable list.

**Downscale reference images (new Settings option)**

Added a toggle in Settings to downscale input images before they enter the model, for EDIT and Sketch modes. Lower MP means faster generation and lower VRAM, which helps avoid out-of-memory freezes on large images. On by default at 1 MP (matching the previous behaviour); turn it off for maximum fidelity when your GPU can handle the full resolution.

**Custom prompts and settings now survive reinstalls**

Your custom Discover prompts, LoRA trigger words and T2I templates are now stored in the ComfyUI user folder instead of inside the node folder, so they are no longer lost when you update or reinstall the node.

**Paste in Paint mode**

You can now paste an image from your clipboard while the Sketch canvas is open, and it drops in as a new layer.

**Drag to change LoRA strength**

Click and drag horizontally on a LoRA strength value to scrub it, just like native ComfyUI nodes. Clicking still lets you type a value, and the whole number is selected on focus.

**Symlinked model folders are now detected**

The model scanner now follows symbolic links, so LoRAs and other models stored on another drive via symlinks are correctly picked up.

---

### June 22, 2026

**Paste from clipboard**

You can now paste images directly from your clipboard (Ctrl+V) while hovering over the node. In Edit and Faceswap mode the image goes into the first empty slot, then the second if the first is already taken.

**Sketch improvements**

- Added fullscreen mode - hit the expand button in the Sketch toolbar to go fullscreen.
- Brush size limit increased from 200 to 500px.
- Added aspect ratio lock button next to the canvas size inputs.

**Gallery right-click**

Right-clicking any thumbnail in the gallery grid now shows a quick "Use as..." context menu.

---

### June 20, 2026

**Negative LoRA strength**

LoRA strength now accepts negative values - useful for concept sliders and suppressing specific styles or features.

---

### June 19, 2026

**External loaders (GGUF support)**

The node now has optional model, clip, and VAE input slots. Enable them in Settings under "External model/clip/vae inputs" and connect any loader you want - including GGUF. When a loader is connected, the corresponding dropdown in Settings is automatically dimmed.

![External loaders](assets/support_for_external_loaders.png)

**Refresh models**

Added a "↻ Refresh models" button in Settings and in the Add LoRA panel. No more restarting ComfyUI after adding new models or LoRAs to your ComfyUI directories - just hit the button.

**Tablet and pen support**

The Sketch canvas now supports tablet input. Pen pressure controls brush size automatically.

---

### June 18, 2026

Initial release.
