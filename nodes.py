import os
import io
import json
import glob
import time
import uuid
import subprocess
import shutil
from pathlib import Path
import folder_paths
from aiohttp import web
from server import PromptServer

NODE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(NODE_DIR, 'config.json')
SUBFOLDER = "one-node-flux2klein-canvas"

# User config lives outside the node folder so it survives reinstalls / git pull.
USER_CONFIG_DIR = os.path.join(folder_paths.get_user_directory(), "default", SUBFOLDER)
USER_CONFIG_PATH = os.path.join(USER_CONFIG_DIR, "config.json")


def _favorites_path():
    return os.path.join(NODE_DIR, "favorites.json")


def _load_favorites():
    path = _favorites_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            return set()
    # First run: build index by scanning existing sidecar JSONs (both locations)
    favs = set()
    try:
        subf_dir = os.path.join(_get_output_dir(), SUBFOLDER)
        if os.path.isdir(subf_dir):
            scan_dirs = [subf_dir, os.path.join(subf_dir, "metadata")]
            for d in scan_dirs:
                if not os.path.isdir(d):
                    continue
                for jf in glob.glob(os.path.join(d, "*.json")):
                    try:
                        with open(jf, "r", encoding="utf-8") as f:
                            md = json.load(f)
                        if md.get("favorite") is True:
                            png = os.path.splitext(os.path.basename(jf))[0] + ".png"
                            favs.add(png)
                    except Exception:
                        pass
        if favs:
            _save_favorites(favs)
    except Exception:
        pass
    return favs


def _save_favorites(favset):
    path = _favorites_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(favset), f, ensure_ascii=False, indent=2)


def _favorites_add(filename):
    favs = _load_favorites()
    favs.add(filename)
    _save_favorites(favs)


def _favorites_remove(filename):
    favs = _load_favorites()
    favs.discard(filename)
    _save_favorites(favs)



def _safe_resolve_output_path(output_dir, subfolder="", filename=""):
    base = Path(output_dir).resolve()
    target = base
    if subfolder:
        target = target / subfolder
    if filename:
        target = target / filename
    target = target.resolve()
    try:
        target.relative_to(base)
    except Exception:
        raise ValueError("invalid path")
    return str(target)


def _safe_resolve_input_path(filename=""):
    base = Path(folder_paths.get_input_directory()).resolve()
    target = (base / filename).resolve()
    try:
        target.relative_to(base)
    except Exception:
        raise ValueError("invalid input path")
    return str(target)


def _file_key(filename, subfolder=""):
    return f"{subfolder}/{filename}" if subfolder else filename


def _load_builtin_config():
    """Read-only defaults shipped with the node. Never written to."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_user_config():
    """User edits, stored outside the node folder so they survive reinstalls."""
    try:
        with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _merge_discover(builtin, user):
    """Deep-merge discover_prompts so users see BOTH new built-in presets and
    their own. Built-in items first; user items appended/override by label."""
    out = json.loads(json.dumps(builtin or {}))  # deep copy
    for pill, udata in (user or {}).items():
        if not isinstance(udata, dict) or "categories" not in udata:
            out[pill] = udata
            continue
        bcats = (out.get(pill) or {}).get("categories", [])
        by_cat = {c.get("cat"): c for c in bcats}
        for ucat in udata.get("categories", []):
            name = ucat.get("cat")
            if name in by_cat:
                items = by_cat[name].setdefault("items", [])
                labels = {it.get("label") for it in items}
                for uit in ucat.get("items", []):
                    if uit.get("label") in labels:
                        for i, it in enumerate(items):
                            if it.get("label") == uit.get("label"):
                                items[i] = uit
                                break
                    else:
                        items.append(uit)
            else:
                bcats.append(ucat)
        out.setdefault(pill, {})["categories"] = bcats
    return out


def _load_config():
    builtin = _load_builtin_config()
    user = _load_user_config()
    merged = dict(builtin)
    merged.update(user)  # user wins for simple keys
    # discover_prompts gets a deep merge so new built-in presets stay visible
    merged["discover_prompts"] = _merge_discover(
        builtin.get("discover_prompts"), user.get("discover_prompts")
    )
    return merged


def _diff_discover(builtin, incoming):
    """Return only user-added/changed discover items, so the user file does not
    freeze a copy of the built-ins (which would hide future built-in presets)."""
    diff = {}
    for pill, idata in (incoming or {}).items():
        if not isinstance(idata, dict) or "categories" not in idata:
            diff[pill] = idata
            continue
        bcats = {c.get("cat"): {it.get("label"): it for it in c.get("items", [])}
                 for c in (builtin.get(pill) or {}).get("categories", [])}
        out_cats = []
        for icat in idata.get("categories", []):
            name = icat.get("cat")
            bitems = bcats.get(name, {})
            new_items = [it for it in icat.get("items", [])
                         if bitems.get(it.get("label")) != it]
            if name not in bcats or new_items:
                out_cats.append({"cat": name, "items": new_items})
        if out_cats:
            diff[pill] = {"categories": out_cats}
    return diff


def _save_config(patch):
    """Write user edits to the user folder only. Repo config.json is never touched."""
    user = _load_user_config()
    builtin = _load_builtin_config()
    for k, v in patch.items():
        if k == "discover_prompts":
            user[k] = _diff_discover(builtin.get("discover_prompts", {}), v)
        else:
            user[k] = v
    os.makedirs(USER_CONFIG_DIR, exist_ok=True)
    with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(user, f, ensure_ascii=False, indent=2)


def _get_output_dir():
    try:
        return str(Path(folder_paths.get_output_directory()).resolve())
    except Exception:
        return str(Path(os.path.join(os.path.dirname(NODE_DIR), "output")).resolve())


def _find_ffmpeg():
    try:
        from custom_nodes.ComfyUI_VideoHelperSuite.videohelpersuite.utils import ffmpeg_path
        if os.path.isfile(ffmpeg_path):
            return ffmpeg_path
    except Exception:
        pass
    try:
        import custom_nodes.ComfyUI_VideoHelperSuite.videohelpersuite.ffmpeg_path as vhs_fp
        p = vhs_fp.get_ffmpeg_path() if hasattr(vhs_fp, 'get_ffmpeg_path') else getattr(vhs_fp, 'ffmpeg_path', '')
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    root = NODE_DIR
    for _ in range(6):
        if os.path.isdir(os.path.join(root, "custom_nodes")):
            break
        root = os.path.dirname(root)
    for vhs_name in ["ComfyUI-VideoHelperSuite", "ComfyUI_VideoHelperSuite", "comfyui-videohelpersuite"]:
        vhs_dir = os.path.join(root, "custom_nodes", vhs_name)
        if os.path.isdir(vhs_dir):
            for r2, _, files in os.walk(vhs_dir):
                if exe in files:
                    return os.path.join(r2, exe)
    portable = os.path.dirname(root)
    for candidate in [os.path.join(portable, exe), os.path.join(root, exe), os.path.join(portable, "bin", exe)]:
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("ffmpeg")
    if found:
        return found
    return None


_ffmpeg_path = None


def _ff():
    global _ffmpeg_path
    if _ffmpeg_path is None:
        _ffmpeg_path = _find_ffmpeg() or ""
    return _ffmpeg_path or None


def _meta_dir(image_path):
    """Returns the metadata/ subdirectory for the folder containing image_path."""
    return os.path.join(os.path.dirname(image_path), "metadata")


def _meta_path(image_path):
    """New canonical location: <image_dir>/metadata/<basename>.json"""
    fname = os.path.splitext(os.path.basename(image_path))[0] + ".json"
    return os.path.join(_meta_dir(image_path), fname)


def _meta_path_legacy(image_path):
    """Old location: <image_dir>/<basename>.json (sidecar next to image)"""
    base, _ = os.path.splitext(image_path)
    return base + ".json"


def _migrate_meta_sidecars():
    """One-time migration: move *.json sidecars next to PNGs into metadata/ subdir."""
    try:
        subf_dir = os.path.join(_get_output_dir(), SUBFOLDER)
        if not os.path.isdir(subf_dir):
            return
        meta_dir = os.path.join(subf_dir, "metadata")
        os.makedirs(meta_dir, exist_ok=True)
        moved = 0
        for jf in glob.glob(os.path.join(subf_dir, "*.json")):
            basename = os.path.basename(jf)
            dest = os.path.join(meta_dir, basename)
            if not os.path.exists(dest):
                try:
                    shutil.move(jf, dest)
                    moved += 1
                except Exception as e:
                    print(f"[FluxKlein] migrate sidecar {basename}: {e}")
            else:
                try:
                    os.remove(jf)
                except Exception:
                    pass
        if moved:
            print(f"[FluxKlein] Migrated {moved} metadata sidecar(s) to metadata/")
    except Exception as e:
        print(f"[FluxKlein] migrate_meta_sidecars error: {e}")


# â”€â”€ PNG tEXt chunk helpers (no external deps) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _png_embed_meta(png_path, meta_dict):
    """Embed metadata JSON into a PNG file as a tEXt chunk with keyword 'Comment'."""
    import struct, zlib
    try:
        with open(png_path, "rb") as f:
            data = f.read()
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return False
        meta_json = json.dumps(meta_dict, ensure_ascii=False, separators=(',', ':'))
        keyword = b'Comment'
        text_data = keyword + b'\x00' + meta_json.encode('utf-8')
        crc = zlib.crc32(b'tEXt' + text_data) & 0xFFFFFFFF
        chunk = struct.pack('>I', len(text_data)) + b'tEXt' + text_data + struct.pack('>I', crc)
        # Insert after IHDR chunk (first chunk after signature)
        sig = data[:8]
        # Find position after IHDR
        pos = 8
        ihdr_len = struct.unpack('>I', data[8:12])[0]
        pos += 12 + ihdr_len  # skip length(4) + type(4) + data + crc(4)
        # Strip existing tEXt Comment chunks to avoid duplicates
        new_body = bytearray()
        i = 8
        while i < len(data) - 4:
            try:
                clen = struct.unpack('>I', data[i:i+4])[0]
                ctype = data[i+4:i+8]
                if ctype == b'tEXt':
                    chunk_data = data[i+8:i+8+clen]
                    if chunk_data.startswith(b'Comment\x00'):
                        i += 12 + clen
                        continue
                new_body += data[i:i+12+clen]
                if ctype == b'IEND':
                    break
                i += 12 + clen
            except Exception:
                new_body += data[i:]
                break
        # Build final PNG: sig + IHDR + tEXt chunk + rest
        # Re-parse IHDR from new_body
        final = bytearray(sig)
        j = 0
        inserted = False
        while j < len(new_body):
            try:
                clen = struct.unpack('>I', bytes(new_body[j:j+4]))[0]
                ctype = new_body[j+4:j+8]
                final += new_body[j:j+12+clen]
                j += 12 + clen
                if not inserted and ctype == b'IHDR':
                    final += chunk
                    inserted = True
            except Exception:
                final += new_body[j:]
                break
        if not inserted:
            final += chunk
        tmp = png_path + ".fkmeta.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(final)
            # On Windows the PNG is often still held by ComfyUI's SaveImage node (and can
            # be grabbed by an indexer or AV scanner too). The old 5 x 0.3s ceiling of 1.5s
            # was not enough once every canvas render started writing metadata - the log
            # filled with WinError 5. Back off further before giving up.
            import time
            delays = (0.2, 0.4, 0.8, 1.2, 1.6, 2.0)
            for attempt, d in enumerate(delays):
                try:
                    os.replace(tmp, png_path)
                    break
                except OSError:
                    if attempt == len(delays) - 1:
                        raise
                    time.sleep(d)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception as e:
        # Not a failure worth alarming about: the JSON sidecar in metadata/ is the store the
        # gallery actually reads, and it is written separately. Embedding only adds
        # portability if the PNG is moved out of the project.
        print(f"[FluxKlein] note: could not embed metadata into "
              f"{os.path.basename(png_path)} (file locked); sidecar written instead.")
        return False


def _png_read_meta(png_path):
    """Read metadata JSON from PNG tEXt Comment chunk."""
    import struct
    try:
        with open(png_path, "rb") as f:
            data = f.read()
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return None
        i = 8
        while i < len(data) - 4:
            try:
                clen = struct.unpack('>I', data[i:i+4])[0]
                ctype = data[i+4:i+8]
                if ctype == b'tEXt':
                    chunk_data = data[i+8:i+8+clen]
                    if chunk_data.startswith(b'Comment\x00'):
                        raw = chunk_data[8:].decode('utf-8', errors='replace')
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            return parsed
                if ctype == b'IEND':
                    break
                i += 12 + clen
            except Exception:
                break
        return None
    except Exception as e:
        print(f"[FluxKlein] png_read_meta error: {e}")
        return None


def _read_json_meta(image_path):
    """Read metadata: try PNG tEXt chunk first, then metadata/ sidecar, then legacy sidecar."""
    _VALID = ("v", "prompt", "w", "h", "mode", "favorite", "favourite")
    # 1. PNG tEXt chunk
    if image_path.lower().endswith('.png') and os.path.exists(image_path):
        meta = _png_read_meta(image_path)
        if meta and isinstance(meta, dict) and any(k in meta for k in _VALID):
            return meta
    # 2. metadata/ subdir sidecar
    for mp in (_meta_path(image_path), _meta_path_legacy(image_path)):
        if not os.path.exists(mp):
            continue
        try:
            with open(mp, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and any(k in data for k in _VALID):
                return data
        except Exception as e:
            print(f"[FluxKlein] read_json_meta error: {e}")
    return None


def _write_json_meta(image_path, meta_dict):
    """Write metadata: embed into PNG tEXt chunk (primary) + metadata/ sidecar (fallback)."""
    ok_png = False
    if image_path.lower().endswith('.png') and os.path.exists(image_path):
        orig_mtime = os.path.getmtime(image_path)
        ok_png = _png_embed_meta(image_path, meta_dict)
        if ok_png:
            try:
                os.utime(image_path, (orig_mtime, orig_mtime))
            except Exception:
                pass
            pass   # embedded fine; no need to log one line per render
    # Also write JSON sidecar into metadata/ subdir
    mp = _meta_path(image_path)
    tmp = mp + ".tmp"
    try:
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta_dict, f, ensure_ascii=False, indent=2)
        os.replace(tmp, mp)
        return True
    except Exception as e:
        print(f"[FluxKlein] write_json_meta error: {e}")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return ok_png  # return True if at least PNG embed succeeded


def _serve_json(filename):
    async def handler(request):
        path = os.path.join(NODE_DIR, filename)
        if not os.path.exists(path):
            return web.Response(status=404, text=f"{filename} not found")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return web.json_response(data)
    return handler


PromptServer.instance.routes.get("/flux_klein_canvas/workflow_t2i")(_serve_json("workflows/t2i_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_i2i")(_serve_json("workflows/i2i_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_edit")(_serve_json("workflows/edit_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_inpaint")(_serve_json("workflows/inpaint_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_outpaint")(_serve_json("workflows/outpaint_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_faceswap")(_serve_json("workflows/faceswap_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_pose")(_serve_json("workflows/pose_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_upscale")(_serve_json("workflows/upscale_workflow.json"))
PromptServer.instance.routes.get("/flux_klein_canvas/workflow_remove_bg")(_serve_json("workflows/remove_bg_workflow.json"))


@PromptServer.instance.routes.get("/flux_klein_canvas/bgremoval_models")
async def get_bgremoval_models(request):
    """Scan models/background_removal/ for all model files."""
    exts = [".safetensors", ".onnx", ".pt", ".pth"]
    found = []
    # Try via folder_paths first (same mechanism as other model scans)
    try:
        bases = folder_paths.get_folder_paths("background_removal")
        for base in bases:
            if os.path.isdir(base):
                for fn in os.listdir(base):
                    if any(fn.lower().endswith(e) for e in exts):
                        found.append(fn)
    except Exception:
        pass
    # Fallback: scan models/background_removal/ relative to ComfyUI root
    if not found:
        try:
            models_dir = folder_paths.models_dir
        except Exception:
            models_dir = os.path.join(os.path.dirname(os.path.dirname(NODE_DIR)), "models")
        bg_dir = os.path.join(models_dir, "background_removal")
        if os.path.isdir(bg_dir):
            for fn in os.listdir(bg_dir):
                if any(fn.lower().endswith(e) for e in exts):
                    found.append(fn)
    found = sorted(set(found))
    return web.json_response({"models": found})


# ---------------------------------------------------------------------------
# PROJECTS
#
# A canvas board is a piece of work in progress, not a single global scratchpad, so each
# one is stored as its own JSON file under projects/ and addressed by id. Files rather
# than one blob because boards are opened and saved independently, and a corrupt or
# hand-edited file then costs you one project instead of all of them.
#
# Only geometry and server-side file references are kept - never pixels - so a board with
# a hundred images is still a few KB.
# ---------------------------------------------------------------------------
def _safetensors_header(path):
    """Read only the JSON header of a .safetensors file - cheap, no tensors loaded."""
    import struct
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n <= 0 or n > 200_000_000:
            raise ValueError("bad safetensors header")
        return json.loads(f.read(n).decode("utf-8"))


def _arch_fingerprint(path):
    """(single_block_count, linear1_width) - enough to tell klein variants apart."""
    head = _safetensors_header(path)
    blocks = set()
    width = None
    for k, v in head.items():
        if k == "__metadata__":
            continue
        # Checkpoints name these "single_blocks.0...", LoRAs "diffusion_model.single_blocks.0...";
        # match either, or the model side silently fingerprints as zero blocks.
        marker = "single_blocks."
        if marker in k:
            try:
                blocks.add(int(k.split(marker)[1].split(".")[0]))
            except Exception:
                pass
            if "linear1" in k and width is None:
                shape = (v or {}).get("shape") or []
                # LoRA down/up factors carry the wide dimension in one of the two axes
                if shape:
                    width = max(shape)
    return len(blocks), width


@PromptServer.instance.routes.get("/flux_klein_canvas/arch")
async def arch_probe(request):
    """?lora=<name>&model=<name> -> fingerprints, so the UI can warn before submitting."""
    out = {"ok": True}
    try:
        lora = request.rel_url.query.get("lora", "")
        model = request.rel_url.query.get("model", "")
        if lora:
            try:
                lp = folder_paths.get_full_path("loras", lora)
                if lp:
                    b, w = _arch_fingerprint(lp)
                    out["lora"] = {"name": lora, "blocks": b, "width": w}
            except Exception as e:
                out["lora_error"] = str(e)
        if model:
            try:
                mp = folder_paths.get_full_path("diffusion_models", model) or \
                     folder_paths.get_full_path("unet", model)
                if mp:
                    b, w = _arch_fingerprint(mp)
                    out["model"] = {"name": model, "blocks": b, "width": w}
            except Exception as e:
                out["model_error"] = str(e)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)
    return web.json_response(out)


PROJECTS_DIR = os.path.join(NODE_DIR, "projects")


def _projects_dir():
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    return PROJECTS_DIR


# Deleted boards land here rather than being removed. Named with a leading dot so it
# sorts out of the way, and skipped by list_projects because it is not a .json file.
TRASH_DIR = os.path.join(PROJECTS_DIR, ".trash")
TRASH_KEEP_DAYS = 30
TRASH_KEEP_MAX = 50


def _trash_dir():
    os.makedirs(TRASH_DIR, exist_ok=True)
    return TRASH_DIR


def _trash_entries():
    """Newest first. Each entry is (filename, mtime, parsed-or-None)."""
    out = []
    try:
        for fn in os.listdir(_trash_dir()):
            if not fn.endswith(".json"):
                continue
            full = os.path.join(TRASH_DIR, fn)
            try:
                mt = os.path.getmtime(full)
            except Exception:
                mt = 0
            data = None
            try:
                with open(full, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
            out.append((fn, mt, data))
    except Exception as e:
        print(f"[FluxKleinCanvas] trash list error: {e}")
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def _purge_trash():
    """Age out old entries, but never drop the newest TRASH_KEEP_MAX."""
    try:
        entries = _trash_entries()
        cutoff = time.time() - TRASH_KEEP_DAYS * 86400
        for fn, mt, _ in entries[TRASH_KEEP_MAX:]:
            if mt < cutoff:
                try:
                    os.remove(os.path.join(TRASH_DIR, fn))
                except Exception:
                    pass
    except Exception as e:
        print(f"[FluxKleinCanvas] trash purge error: {e}")


def _safe_project_id(pid):
    """Only ever touch a flat, sanitised filename inside projects/."""
    pid = str(pid or "").strip()
    keep = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    pid = "".join(c for c in pid if c in keep)
    return pid[:64]


def _project_path(pid):
    pid = _safe_project_id(pid)
    if not pid:
        return None
    return os.path.join(_projects_dir(), pid + ".json")


def _read_project(pid):
    path = _project_path(pid)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[FluxKleinCanvas] project read error ({pid}): {e}")
        return None


@PromptServer.instance.routes.get("/flux_klein_canvas/projects")
async def list_projects(request):
    """Summaries only - the launcher never needs the full board."""
    out = []
    try:
        for fn in os.listdir(_projects_dir()):
            if not fn.endswith(".json"):
                continue
            data = _read_project(fn[:-5])
            if not isinstance(data, dict):
                continue
            board = data.get("board") or {}
            frames = board.get("canvasFrames") or []
            # first frame with a real file becomes the cover
            thumb = None
            for fr in frames:
                if fr.get("filename"):
                    thumb = {
                        "filename": fr.get("filename"),
                        "subfolder": fr.get("subfolder", ""),
                        "type": fr.get("type", "output"),
                    }
                    break
            out.append({
                "id": data.get("id") or fn[:-5],
                "name": data.get("name") or "Untitled",
                "updated": data.get("updated", 0),
                "frames": len(frames),
                "blocks": len(board.get("canvasBlocks") or []),
                "thumb": thumb,
            })
    except Exception as e:
        print(f"[FluxKleinCanvas] project list error: {e}")
    out.sort(key=lambda p: p.get("updated", 0), reverse=True)
    return web.json_response({"projects": out})


# ── Palettes ──────────────────────────────────────────────────────────────────
# Same shape as projects, one file each. A palette is small enough that the trash
# machinery would be over-engineering, so delete really deletes here.
PALETTES_DIR = os.path.join(NODE_DIR, "palettes")


def _palettes_dir():
    os.makedirs(PALETTES_DIR, exist_ok=True)
    return PALETTES_DIR


def _palette_path(pid):
    pid = _safe_project_id(pid)          # same sanitiser: flat name, no traversal
    if not pid:
        return None
    return os.path.join(_palettes_dir(), pid + ".json")


def _read_palette(pid):
    path = _palette_path(pid)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[FluxKleinCanvas] palette read error ({pid}): {e}")
        return None


@PromptServer.instance.routes.get("/flux_klein_canvas/palettes")
async def list_palettes(request):
    out = []
    try:
        for fn in os.listdir(_palettes_dir()):
            if not fn.endswith(".json"):
                continue
            d = _read_palette(fn[:-5])
            if not isinstance(d, dict):
                continue
            out.append({
                "id": d.get("id") or fn[:-5],
                "name": d.get("name") or "Untitled",
                "updated": d.get("updated", 0),
                "colors": d.get("colors") or [],
            })
    except Exception as e:
        print(f"[FluxKleinCanvas] palette list error: {e}")
    out.sort(key=lambda x: x.get("updated", 0), reverse=True)
    return web.json_response({"palettes": out})


@PromptServer.instance.routes.post("/flux_klein_canvas/palette")
async def save_palette(request):
    try:
        payload = await request.json()
        pid = _safe_project_id(payload.get("id"))
        if not pid:
            return web.json_response({"ok": False, "error": "invalid id"}, status=400)
        colors = payload.get("colors") or []
        if not isinstance(colors, list):
            return web.json_response({"ok": False, "error": "colors must be a list"}, status=400)
        # Only ever store plain #rrggbb, so nothing from the client can reach the DOM as markup.
        clean = []
        for c in colors[:10]:
            c = str(c or "").strip()
            if len(c) == 7 and c[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]):
                clean.append(c.lower())
        existing = _read_palette(pid) or {}
        data = {
            "id": pid,
            "name": (payload.get("name") or existing.get("name") or "Untitled").strip()[:120],
            "created": existing.get("created") or time.time(),
            "updated": time.time(),
            "colors": clean,
        }
        path = _palette_path(pid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return web.json_response({"ok": True, "id": pid, "colors": clean})
    except Exception as e:
        print(f"[FluxKleinCanvas] palette save error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.post("/flux_klein_canvas/palette_delete")
async def delete_palette(request):
    try:
        payload = await request.json()
        path = _palette_path(payload.get("id"))
        if not path or not os.path.exists(path):
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        os.remove(path)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.get("/flux_klein_canvas/project_trash")
async def list_trash(request):
    """Deleted boards still on disk, newest first."""
    out = []
    for fn, mt, data in _trash_entries():
        board = (data or {}).get("board") or {}
        frames = board.get("canvasFrames") or []
        thumb = None
        for fr in frames:
            if fr.get("filename"):
                thumb = {
                    "filename": fr.get("filename"),
                    "subfolder": fr.get("subfolder", ""),
                    "type": fr.get("type", "output"),
                }
                break
        out.append({
            "entry": fn,
            "id": (data or {}).get("id") or fn.split("__")[0],
            "name": (data or {}).get("name") or "Untitled",
            "deleted": mt,
            "frames": len(frames),
            "thumb": thumb,
        })
    return web.json_response({"trash": out})


@PromptServer.instance.routes.post("/flux_klein_canvas/project_restore")
async def restore_project(request):
    try:
        payload = await request.json()
        entry = str(payload.get("entry") or "")
        # Never let a path escape the trash folder.
        if not entry.endswith(".json") or "/" in entry or "\\" in entry or ".." in entry:
            return web.json_response({"ok": False, "error": "bad entry"}, status=400)
        src = os.path.join(_trash_dir(), entry)
        if not os.path.exists(src):
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        pid = _safe_project_id(entry.split("__")[0])
        dest = _project_path(pid)
        # The id may have been reused since; give the restored board a fresh one.
        if not dest or os.path.exists(dest):
            pid = _safe_project_id("%s-r%d" % (pid or "board", int(time.time()) % 100000))
            dest = _project_path(pid)
        shutil.move(src, dest)
        try:
            with open(dest, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["id"] = pid
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
        return web.json_response({"ok": True, "id": pid})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.get("/flux_klein_canvas/project")
async def get_project(request):
    pid = request.rel_url.query.get("id", "")
    data = _read_project(pid)
    if data is None:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    return web.json_response({"ok": True, "project": data})


# ---------------------------------------------------------------------------
# Board-loss protection (Fix 1)
#
# A project file is the only copy of a board - the images live in output/, but the arrangement,
# the blocks and the wiring exist nowhere else. These two mechanisms sit on the write path so
# they cover every caller, including whatever produced the empty save we could not reproduce.
# ---------------------------------------------------------------------------

RECOVERY_DIR = os.path.join(PROJECTS_DIR, ".recovery")
RECOVERY_KEEP = 8


def _recovery_dir():
    os.makedirs(RECOVERY_DIR, exist_ok=True)
    return RECOVERY_DIR


def _board_counts(board):
    b = board or {}
    return (len(b.get("canvasFrames") or []), len(b.get("canvasBlocks") or []))


def _keep_recovery_copy(pid, existing, suffix="prev"):
    """Snapshot the CURRENT file before it is replaced. Best effort: a failure here must never
    block the save the user asked for."""
    try:
        d = _recovery_dir()
        name = "%s.%d.%s.json" % (pid, int(time.time() * 1000), suffix)
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False)
        # rotate: keep the newest RECOVERY_KEEP for this project id
        mine = sorted(
            (fn for fn in os.listdir(d) if fn.startswith(pid + ".") and fn.endswith(".json")),
            key=lambda fn: os.path.getmtime(os.path.join(d, fn)), reverse=True)
        for old in mine[RECOVERY_KEEP:]:
            try:
                os.remove(os.path.join(d, old))
            except Exception:
                pass
        return name
    except Exception as e:
        print("[FluxKlein] recovery copy failed (%s): %s" % (pid, e))
        return None


@PromptServer.instance.routes.post("/flux_klein_canvas/project")
async def save_project(request):
    try:
        payload = await request.json()
        pid = _safe_project_id(payload.get("id"))
        if not pid:
            return web.json_response({"ok": False, "error": "invalid id"}, status=400)
        existing = _read_project(pid) or {}
        new_board = payload.get("board") or {}

        old_frames, old_blocks = _board_counts(existing.get("board"))
        new_frames, new_blocks = _board_counts(new_board)

        # Catastrophic shrink: several frames down to none. This is the shape of a board that
        # was never loaded rather than one the user emptied, so it is refused and the incoming
        # payload is kept rather than thrown away.
        if old_frames >= 3 and new_frames == 0 and not payload.get("allowEmpty"):
            _keep_recovery_copy(pid, {"refusedPayload": payload, "existingAtRefusal": existing},
                                suffix="rescue")
            print("[FluxKlein] REFUSED empty save for %s (%d frames -> 0). "
                  "Existing board kept; payload saved to .recovery/" % (pid, old_frames))
            return web.json_response({
                "ok": False, "refused": "emptyBoard",
                "error": "Refused to overwrite a %d-frame board with an empty one. "
                         "The existing board is unchanged." % old_frames,
                "existingFrames": old_frames,
            }, status=409)

        # Any other reduction gets a rolling copy of what is about to be replaced.
        if existing and new_frames < old_frames:
            _keep_recovery_copy(pid, existing)

        data = {
            "id": pid,
            "name": payload.get("name") or existing.get("name") or "Untitled",
            "created": existing.get("created") or time.time(),
            "updated": time.time(),
            "board": new_board,
        }
        # Write to a temp file then replace, so an interrupted save cannot leave a
        # half-written board where a working one used to be.
        path = _project_path(pid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return web.json_response({"ok": True, "id": pid, "updated": data["updated"]})
    except Exception as e:
        print(f"[FluxKleinCanvas] project save error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.post("/flux_klein_canvas/project_delete")
async def delete_project(request):
    try:
        payload = await request.json()
        pid = _safe_project_id(payload.get("id"))
        path = _project_path(pid)
        if not path or not os.path.exists(path):
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        # Move, do not remove. The board is recoverable until the trash ages out.
        entry = "%s__%d.json" % (pid, int(time.time()))
        shutil.move(path, os.path.join(_trash_dir(), entry))
        _purge_trash()
        return web.json_response({"ok": True, "trashed": entry})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.post("/flux_klein_canvas/project_rename")
async def rename_project(request):
    try:
        payload = await request.json()
        pid = _safe_project_id(payload.get("id"))
        data = _read_project(pid)
        if data is None:
            return web.json_response({"ok": False, "error": "not found"}, status=404)
        data["name"] = (payload.get("name") or "Untitled").strip()[:120]
        data["updated"] = time.time()
        path = _project_path(pid)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.get("/flux_klein_canvas/config")
async def get_config(request):
    cfg = _load_config()
    return web.json_response({
        "dummy": cfg.get("dummy", ""),
        "lora_triggers_custom": cfg.get("lora_triggers_custom", {}),
        "t2i_templates": cfg.get("t2i_templates", []),
        "discover_prompts": cfg.get("discover_prompts", {}),
        "autofill_prompts": cfg.get("autofill_prompts", {}),
    })


@PromptServer.instance.routes.post("/flux_klein_canvas/config")
async def save_config_route(request):
    try:
        patch = await request.json()
        if not isinstance(patch, dict):
            return web.json_response({"ok": False, "error": "invalid payload"}, status=400)
        _save_config(patch)
        return web.json_response({"ok": True})
    except Exception as e:
        print(f"[FluxKlein] config save error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.get("/flux_klein_canvas/gallery")
async def get_gallery(request):
    output_dir = _get_output_dir()
    try:
        offset = max(0, int(request.query.get("offset", 0)))
    except Exception:
        offset = 0
    try:
        limit = min(max(1, int(request.query.get("limit", 20))), 200)
    except Exception:
        limit = 20
    subf = request.query.get("subfolder", "")
    favonly = request.query.get("favonly", "0") == "1"
    try:
        search = _safe_resolve_output_path(output_dir, subf) if subf else output_dir
    except ValueError:
        return web.json_response({"images": [], "total": 0, "offset": offset, "limit": limit, "error": "invalid subfolder"}, status=400)

    assets_dir = os.path.normpath(_safe_resolve_output_path(output_dir, os.path.join(SUBFOLDER, "assets")))

    if favonly:
        # Fast path: read favorites index, resolve to existing files sorted by mtime
        fav_names = _load_favorites()
        subf_dir = os.path.normpath(_safe_resolve_output_path(output_dir, SUBFOLDER))
        unique = []
        missing = set()
        for name in fav_names:
            p = os.path.join(subf_dir, name)
            if os.path.isfile(p):
                unique.append(p)
            else:
                missing.add(name)
        if missing:
            _save_favorites(fav_names - missing)
        unique.sort(key=os.path.getmtime, reverse=True)
    else:
        search_norm = os.path.normpath(search)
        exclude_assets = not search_norm.startswith(assets_dir + os.sep) and search_norm != assets_dir
        unique = []
        if os.path.isdir(search):
            pngs = glob.glob(os.path.join(search, "**", "*.png"), recursive=True)
            filtered = [p for p in pngs if not exclude_assets or not os.path.normpath(p).startswith(assets_dir + os.sep)]
            unique = sorted(set(filtered), key=os.path.getmtime, reverse=True)

    fav_set = _load_favorites() if not favonly else fav_names
    images = []
    for f in unique[offset:offset + limit]:
        rel = os.path.relpath(os.path.dirname(f), output_dir)
        fname = os.path.basename(f)
        images.append({
            "filename": fname,
            "subfolder": "" if rel == "." else rel,
            "mtime": os.path.getmtime(f),
            "key": _file_key(fname, "" if rel == "." else rel),
            "has_meta": os.path.exists(_meta_path(f)) or os.path.exists(_meta_path_legacy(f)),
            "favorite": fname in fav_set,
        })
    return web.json_response({"images": images, "total": len(unique), "offset": offset, "limit": limit})


# ---------------------------------------------------------------------------
# PSD export (Phase 8)
#
# psd_tools is a reader first, but it can author: PSDImage.new() for the document, then one
# PixelLayer per layer. Verified in this environment that names, opacity and blend modes
# survive a save/reopen round trip.
# ---------------------------------------------------------------------------

# CSS mixBlendMode -> PSD blend. Only the modes the editor's layer menu actually offers;
# anything else falls back to normal rather than refusing to export.
_PSD_BLEND_NAMES = {
    "normal": "NORMAL",
    "multiply": "MULTIPLY",
    "screen": "SCREEN",
    "overlay": "OVERLAY",
    "darken": "DARKEN",
    "lighten": "LIGHTEN",
    "color-dodge": "COLOR_DODGE",
    "color-burn": "COLOR_BURN",
    "hard-light": "HARD_LIGHT",
    "soft-light": "SOFT_LIGHT",
    "difference": "DIFFERENCE",
    "exclusion": "EXCLUSION",
    "hue": "HUE",
    "saturation": "SATURATION",
    "color": "COLOR",
    "luminosity": "LUMINOSITY",
}


@PromptServer.instance.routes.post("/flux_klein_canvas/export_psd")
async def export_psd(request):
    """Build a layered PSD from the editor's layers.

    Body: {"width":int,"height":int,"layers":[{"name":str,"opacity":0..1,
           "blend":"normal","png":"data:image/png;base64,..."}]}
    Bottom layer first, matching the editor's own stacking order.
    """
    try:
        try:
            from psd_tools import PSDImage
            from psd_tools.api.layers import PixelLayer
            from psd_tools.constants import BlendMode
        except Exception as e:
            return web.json_response(
                {"ok": False, "error": "psd-tools is not installed in this Python "
                                       "environment (%s)." % type(e).__name__}, status=200)
        from PIL import Image
        import base64

        data = await request.json()
        layers = data.get("layers") or []
        if not layers:
            return web.json_response({"ok": False, "error": "no layers"}, status=200)
        W = int(data.get("width") or 0)
        H = int(data.get("height") or 0)
        if W <= 0 or H <= 0 or W > 16384 or H > 16384:
            return web.json_response({"ok": False, "error": "bad canvas size"}, status=200)

        # RGBA, not RGB. Measured: an RGB document silently flattens every layer to
        # fully opaque (0 transparent pixels on a near-empty layer), which in Photoshop
        # means each layer hides everything beneath it. RGBA keeps per-layer alpha.
        psd = PSDImage.new("RGBA", (W, H))
        for spec in layers:
            b64 = (spec.get("png") or "").split(",", 1)[-1]
            if not b64:
                continue
            img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
            if img.size != (W, H):
                img = img.resize((W, H))
            name = str(spec.get("name") or "Layer")[:255]
            layer = PixelLayer.frompil(img, psd, name, 0, 0)
            try:
                op = float(spec.get("opacity", 1.0))
                layer.opacity = max(0, min(255, int(round(op * 255))))
            except Exception:
                pass
            try:
                bname = _PSD_BLEND_NAMES.get(str(spec.get("blend") or "normal").lower(), "NORMAL")
                layer.blend_mode = getattr(BlendMode, bname, BlendMode.NORMAL)
            except Exception:
                pass
            psd.append(layer)

        out_dir = _get_output_dir()
        target_dir = os.path.join(out_dir, SUBFOLDER, "psd")
        os.makedirs(target_dir, exist_ok=True)
        fname = "canvas_%s.psd" % time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(target_dir, fname)
        psd.save(path)
        return web.json_response({"ok": True, "filename": fname,
                                  "subfolder": os.path.join(SUBFOLDER, "psd").replace("\\", "/"),
                                  "bytes": os.path.getsize(path), "layers": len(layers)})
    except Exception as e:
        print("[FluxKlein] export_psd error: %s" % e)
        return web.json_response({"ok": False, "error": str(e)}, status=200)


# ---------------------------------------------------------------------------
# SVG export - trace line / vector artwork to paths
# ---------------------------------------------------------------------------

# Neighbour-constancy below this reads as a photograph rather than drawn artwork.
# Measured across this gallery: photographs peak at 77.4, the lowest genuine flat
# illustration sits at 83.0. 80 sits in that gap; 85 was tried first and wrongly
# rejected real flat artwork.
_SVG_PHOTO_CONSTANCY = 80.0


def _svg_constancy(arr):
    """Percentage of neighbouring pixels that are near-identical, at FULL resolution.

    Drawn artwork holds large constant regions; photographs carry gradient or sensor noise
    almost everywhere. Must not be measured on a thumbnail - downsampling averages the noise
    away and the separation disappears.
    """
    import numpy as np
    a = arr.astype("int16")
    dx = np.abs(a[:, 1:, :] - a[:, :-1, :]).max(axis=2)
    dy = np.abs(a[1:, :, :] - a[:-1, :, :]).max(axis=2)
    return 100.0 * (float((dx <= 2).mean()) + float((dy <= 2).mean())) / 2


def _svg_detect(im):
    """(kind, engine, note) for a PIL RGB image."""
    import numpy as np
    arr = np.asarray(im.convert("RGB"))
    con = _svg_constancy(arr)
    if con < _SVG_PHOTO_CONSTANCY:
        return "photo", "vtracer", (
            "This looks like a photograph, not line or vector artwork. Tracing it produces "
            "a very heavy SVG. Re-render it with the Line or Vector stage first.")
    small = im.convert("RGB").resize((160, 160))
    px = list(small.getdata())
    tot = float(len(px))
    white = mid = grey = 0
    for r, g, b in px:
        if r > 240 and g > 240 and b > 240:
            white += 1
        luma = (r * 299 + g * 587 + b * 114) / 1000.0
        if 60 < luma < 200:
            mid += 1
        if abs(r - g) < 14 and abs(g - b) < 14 and abs(r - b) < 14:
            grey += 1
    if 100.0 * white / tot > 60 and 100.0 * mid / tot < 30 and 100.0 * grey / tot > 90:
        return "line", "potrace", ""
    return "flat", "vtracer", ""


def _svg_from_potrace(im, blacklevel=0.5, turdsize=2, alphamax=1.0, opttolerance=0.2):
    """Bitonal outline trace. Returns (svg_text, path_count)."""
    import numpy as np
    import potrace
    g = im.convert("L")
    w, h = g.size
    # potracer thresholds a non-bool array against 255*blacklevel and then INVERTS, so the
    # raw uint8 array is what it wants. Passing a boolean "is dark" mask traces the
    # background instead and renders near-solid black.
    bmp = potrace.Bitmap(np.array(g), blacklevel=blacklevel)
    path = bmp.trace(turdsize=turdsize, alphamax=alphamax, opticurve=True,
                     opttolerance=opttolerance)
    parts = []
    for curve in path:
        sp = curve.start_point
        d = ["M %.2f %.2f" % (sp.x, sp.y)]
        for seg in curve.segments:
            e = seg.end_point
            if seg.is_corner:
                c = seg.c
                d.append("L %.2f %.2f L %.2f %.2f" % (c.x, c.y, e.x, e.y))
            else:
                c1, c2 = seg.c1, seg.c2
                d.append("C %.2f %.2f %.2f %.2f %.2f %.2f"
                         % (c1.x, c1.y, c2.x, c2.y, e.x, e.y))
        d.append("Z")
        parts.append(" ".join(d))
    body = ('<path fill="#000000" fill-rule="evenodd" d="%s"/>' % " ".join(parts)) if parts else ""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
           'width="%d" height="%d">%s</svg>' % (w, h, w, h, body))
    return svg, len(parts)


def _svg_from_vtracer(src_path, out_path, simplify=1.0):
    """Colour-region trace. vtracer only works file-to-file.

    These settings were swept across every flat illustration in this gallery, not tuned on one
    image. vtracer's own defaults are unusable on detailed artwork - the flat sneaker
    (CANVAS_00438) came out at 2.5MB / 1931 paths. A first tuning pass, fitted to a simple
    two-tone kettle, still left it at 575KB. These values bring it to 221KB with no visible
    loss, and are smaller on every other flat image in the set as well:

        kettle 00261    9KB ->  10KB      sneaker 00390   79KB -> 61KB
        sneaker 00314  52KB ->  44KB      sneaker 00391   81KB -> 62KB
        sneaker 00315  43KB ->  37KB      sneaker 00438  575KB -> 221KB

    simplify scales the speckle filter for the caller's Simplify control; 1.0 is the
    swept default.
    """
    import vtracer
    speckle = max(1, int(round(16 * max(0.25, min(4.0, simplify)))))
    vtracer.convert_image_to_svg_py(
        src_path, out_path, colormode="color", filter_speckle=speckle, color_precision=5,
        layer_difference=32, corner_threshold=60, length_threshold=4.0,
        splice_threshold=45, path_precision=2)
    txt = io.open(out_path, encoding="utf-8").read()
    return txt, txt.count("<path")


@PromptServer.instance.routes.post("/flux_klein_canvas/export_svg")
async def export_svg(request):
    """Trace one or more images to SVG.

    Body: {"items":[{"filename","subfolder","type"} | {"png":"data:image/png;base64,..."}],
           "engine":"auto"|"line"|"flat", "threshold":0.0..1.0, "preview":bool}
    """
    import base64
    import tempfile
    try:
        from PIL import Image
    except Exception as e:
        return web.json_response({"ok": False, "error": "Pillow missing (%s)" % e}, status=200)

    # Named up front rather than surfacing a raw ImportError per image. Checked
    # independently: line art still traces with only potracer present, and flat art with
    # only vtracer, instead of the whole route failing because one is missing.
    _have = {}
    for _mod, _pkg in (("potrace", "potracer"), ("vtracer", "vtracer")):
        try:
            __import__(_mod)
            _have[_mod] = True
        except Exception:
            _have[_mod] = False
    if not any(_have.values()):
        return web.json_response(
            {"ok": False, "error": "SVG tracing needs potracer and vtracer. Install them with:"
                                   "  pip install potracer vtracer"}, status=200)

    try:
        data = await request.json()
        items = data.get("items") or []
        if not items:
            return web.json_response({"ok": False, "error": "no items"}, status=200)
        if len(items) > 24:
            return web.json_response({"ok": False, "error": "too many items (max 24)"}, status=200)
        engine_req = (data.get("engine") or "auto").lower()
        try:
            threshold = float(data.get("threshold", 0.5))
        except Exception:
            threshold = 0.5
        threshold = max(0.05, min(0.95, threshold))
        try:
            simplify = float(data.get("simplify", 1.0))
        except Exception:
            simplify = 1.0
        simplify = max(0.25, min(4.0, simplify))
        want_preview = bool(data.get("preview"))

        out_dir = _get_output_dir()
        target_dir = os.path.join(out_dir, SUBFOLDER, "svg")
        os.makedirs(target_dir, exist_ok=True)

        results = []
        for spec in items:
            src_path = None
            tmp_png = None
            try:
                if spec.get("png"):
                    raw = spec["png"].split(",", 1)[-1]
                    fd, tmp_png = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    with open(tmp_png, "wb") as f:
                        f.write(base64.b64decode(raw))
                    src_path = tmp_png
                    label = spec.get("name") or "layer"
                else:
                    fn = spec.get("filename") or ""
                    if not fn:
                        results.append({"ok": False, "error": "item has no filename"})
                        continue
                    if (spec.get("type") or "output") == "input":
                        src_path = _safe_resolve_input_path(fn)
                    else:
                        src_path = _safe_resolve_output_path(out_dir, spec.get("subfolder") or "", fn)
                    label = os.path.splitext(os.path.basename(fn))[0]

                if not src_path or not os.path.exists(src_path):
                    results.append({"ok": False, "name": spec.get("filename") or "?",
                                    "error": "image not found"})
                    continue

                im = Image.open(src_path)
                kind, engine, note = _svg_detect(im)
                forced = engine_req in ("line", "flat")
                if forced:
                    engine = "potrace" if engine_req == "line" else "vtracer"
                    kind = engine_req
                    note = ""
                elif kind == "photo":
                    # Reported, not traced: the caller decides whether to force it.
                    results.append({"ok": False, "name": label, "kind": "photo",
                                    "refused": "photo", "error": note})
                    continue

                _need = "potrace" if engine == "potrace" else "vtracer"
                if not _have.get(_need):
                    results.append({"ok": False, "name": label, "kind": kind,
                                    "error": "%s artwork needs the '%s' package. "
                                             "Install it with: pip install %s"
                                             % (kind, _need,
                                                "potracer" if _need == "potrace" else "vtracer")})
                    continue

                stamp = time.strftime("%Y%m%d_%H%M%S")
                fname = "%s_%s.svg" % (label[:48], stamp)
                dest = os.path.join(target_dir, fname)

                if engine == "potrace":
                    # Simplify drives speckle removal AND curve fitting. Speckle removal
                    # alone is inert on clean artwork - there is nothing to remove - so the
                    # control would appear broken on exactly the tidiest line art.
                    svg, npaths = _svg_from_potrace(
                        im, blacklevel=threshold,
                        turdsize=max(1, int(round(2 * simplify))),
                        alphamax=min(1.334, 0.75 + 0.25 * simplify),
                        opttolerance=min(1.0, 0.1 + 0.15 * simplify))
                    io.open(dest, "w", encoding="utf-8").write(svg)
                else:
                    svg, npaths = _svg_from_vtracer(src_path, dest, simplify=simplify)

                row = {"ok": True, "name": label, "kind": kind, "engine": engine,
                       "filename": fname,
                       "subfolder": os.path.join(SUBFOLDER, "svg").replace("\\", "/"),
                       "bytes": os.path.getsize(dest), "paths": npaths,
                       "width": im.size[0], "height": im.size[1]}
                if note:
                    row["note"] = note
                if want_preview:
                    row["svg"] = svg
                results.append(row)
            except ValueError:
                results.append({"ok": False, "name": spec.get("filename") or "?",
                                "error": "invalid path"})
            except Exception as e:
                results.append({"ok": False, "name": spec.get("filename") or "?",
                                "error": "%s: %s" % (type(e).__name__, e)})
            finally:
                if tmp_png and os.path.exists(tmp_png):
                    try:
                        os.remove(tmp_png)
                    except Exception:
                        pass

        return web.json_response({"ok": any(r.get("ok") for r in results), "results": results})
    except Exception as e:
        print("[FluxKlein] export_svg error: %s" % e)
        return web.json_response({"ok": False, "error": str(e)}, status=200)


@PromptServer.instance.routes.post("/flux_klein_canvas/save_meta")
async def save_meta(request):
    try:
        data = await request.json()
        filename = data.get("filename", "")
        subfolder = data.get("subfolder", "")
        meta = data.get("meta", {})
        if not filename:
            return web.json_response({"ok": False, "error": "no filename"})
        output_dir = _get_output_dir()
        try:
            vpath = _safe_resolve_output_path(output_dir, subfolder, filename)
        except ValueError:
            return web.json_response({"ok": False, "error": "invalid path"}, status=400)
        if not os.path.exists(vpath):
            return web.json_response({"ok": False, "error": f"not found: {vpath}"})
        ok = _write_json_meta(vpath, meta)
        return web.json_response({"ok": ok, "filename": filename})
    except Exception as e:
        print(f"[FluxKlein] save_meta error: {e}")
        return web.json_response({"ok": False, "error": str(e)})


@PromptServer.instance.routes.post("/flux_klein_canvas/save_temp")
async def save_temp(request):
    """Move a temp (PreviewImage) result into the gallery output folder and write
    its metadata. Used when auto-save is off and the user clicks Save on a result."""
    try:
        data = await request.json()
        temp_filename = data.get("filename", "")
        temp_subfolder = data.get("subfolder", "")
        meta = data.get("meta", {})
        if not temp_filename:
            return web.json_response({"ok": False, "error": "no filename"})

        # Resolve the source temp file safely inside the temp directory.
        temp_base = Path(folder_paths.get_temp_directory()).resolve()
        src = (temp_base / temp_subfolder / temp_filename).resolve()
        try:
            src.relative_to(temp_base)
        except Exception:
            return web.json_response({"ok": False, "error": "invalid temp path"}, status=400)
        if not src.exists():
            return web.json_response({"ok": False, "error": f"temp not found: {temp_filename}"})

        # Destination: output/one-node-flux2klein-canvas/<unique f2k name>.png
        output_dir = _get_output_dir()
        dest_dir = os.path.join(output_dir, SUBFOLDER)
        os.makedirs(dest_dir, exist_ok=True)
        # Build a unique f2k_NNNNN_.png name so it matches the SaveImage convention.
        idx = 1
        existing = glob.glob(os.path.join(dest_dir, "f2k_*_.png"))
        for f in existing:
            m = os.path.basename(f)
            try:
                n = int(m.split("_")[1])
                if n >= idx:
                    idx = n + 1
            except Exception:
                pass
        dest_name = f"f2k_{idx:05d}_.png"
        dest_path = os.path.join(dest_dir, dest_name)
        while os.path.exists(dest_path):
            idx += 1
            dest_name = f"f2k_{idx:05d}_.png"
            dest_path = os.path.join(dest_dir, dest_name)

        shutil.copy2(str(src), dest_path)
        if meta:
            _write_json_meta(dest_path, meta)
        return web.json_response({"ok": True, "filename": dest_name, "subfolder": SUBFOLDER})
    except Exception as e:
        print(f"[FluxKlein] save_temp error: {e}")
        return web.json_response({"ok": False, "error": str(e)})


@PromptServer.instance.routes.post("/flux_klein_canvas/stage_input")
async def stage_input(request):
    """Copy an existing result (output or temp) into the ComfyUI input folder so a
    workflow's LoadImage can read it. Used by quick-upscale, which re-feeds the image
    currently shown in the preview back into the upscale workflow.
    Returns the input-folder filename to put into LoadImage."""
    try:
        data = await request.json()
        filename = data.get("filename", "")
        subfolder = data.get("subfolder", "") or ""
        ftype = data.get("type", "output") or "output"
        if not filename:
            return web.json_response({"ok": False, "error": "no filename"}, status=400)

        src = _resolve_image_file(filename, subfolder, ftype)
        if not src:
            return web.json_response({"ok": False, "error": f"not found: {filename}"}, status=404)

        input_dir = Path(folder_paths.get_input_directory()).resolve()
        os.makedirs(str(input_dir), exist_ok=True)
        ext = os.path.splitext(filename)[1] or ".png"
        dest_name = f"fk_upscale_src_{uuid.uuid4().hex[:10]}{ext}"
        dest_path = input_dir / dest_name
        shutil.copy2(str(src), str(dest_path))
        return web.json_response({"ok": True, "name": dest_name})
    except Exception as e:
        print(f"[FluxKlein] stage_input error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500)


@PromptServer.instance.routes.post("/flux_klein_canvas/update_meta")
async def update_meta(request):
    try:
        data = await request.json()
        filename = data.get("filename", "")
        subfolder = data.get("subfolder", "")
        patch = data.get("patch", {})
        if not filename or not isinstance(patch, dict):
            return web.json_response({"ok": False, "error": "bad request"})
        output_dir = _get_output_dir()
        try:
            vpath = _safe_resolve_output_path(output_dir, subfolder, filename)
        except ValueError:
            return web.json_response({"ok": False, "error": "invalid path"}, status=400)
        existing = _read_json_meta(vpath) or {}
        existing.update(patch)
        ok = _write_json_meta(vpath, existing)
        if "favorite" in patch:
            if patch["favorite"] is True:
                _favorites_add(filename)
            else:
                _favorites_remove(filename)
        return web.json_response({"ok": ok})
    except Exception as e:
        print(f"[FluxKlein] update_meta error: {e}")
        return web.json_response({"ok": False, "error": str(e)})


@PromptServer.instance.routes.get("/flux_klein_canvas/meta")
async def get_meta(request):
    filename = request.query.get("filename", "")
    subfolder = request.query.get("subfolder", "")
    if not filename:
        return web.json_response({"ok": False, "error": "no filename"})
    output_dir = _get_output_dir()
    try:
        vpath = _safe_resolve_output_path(output_dir, subfolder, filename)
    except ValueError:
        return web.json_response({"ok": False, "error": "invalid path"}, status=400)
    if not os.path.exists(vpath):
        return web.json_response({"ok": False, "error": "image not found"})
    meta = _read_json_meta(vpath)
    if meta is None:
        return web.json_response({"ok": False, "error": "no metadata"})
    return web.json_response({"ok": True, "meta": meta})


@PromptServer.instance.routes.post("/flux_klein_canvas/open_folder")
async def open_folder(request):
    try:
        data = await request.json()
        filename = data.get("filename", "")
        subfolder = data.get("subfolder", "")
        if not filename:
            return web.json_response({"ok": False, "error": "no filename"})
        output_dir = _get_output_dir()
        try:
            vpath = _safe_resolve_output_path(output_dir, subfolder, filename)
        except ValueError:
            return web.json_response({"ok": False, "error": "invalid path"}, status=400)
        if not os.path.exists(vpath):
            return web.json_response({"ok": False, "error": "file not found"})
        import platform
        import subprocess as _sp
        system = platform.system()
        if system == "Windows":
            _sp.Popen(["explorer", "/select,", vpath.replace("/", "\\")])
        elif system == "Darwin":
            _sp.Popen(["open", "-R", vpath])
        else:
            _sp.Popen(["xdg-open", os.path.dirname(vpath)])
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})


@PromptServer.instance.routes.post("/flux_klein_canvas/delete")
async def delete_image(request):
    try:
        data = await request.json()
        filename = data.get("filename", "")
        subfolder = data.get("subfolder", "")
        if not filename:
            return web.json_response({"ok": False, "error": "filename required"}, status=400)
        output_dir = _get_output_dir()
        try:
            img_path = _safe_resolve_output_path(output_dir, subfolder, filename)
        except ValueError:
            return web.json_response({"ok": False, "error": "invalid path"}, status=400)
        # The traversal guard above only keeps this inside output/. Confine it further to
        # this node's own folder: a client-supplied subfolder could otherwise delete any
        # file under the output root, including other nodes' work.
        try:
            own = Path(output_dir).resolve() / SUBFOLDER
            Path(img_path).resolve().relative_to(own)
        except Exception:
            return web.json_response(
                {"ok": False, "error": "refused: outside this node's gallery folder"},
                status=400)
        if not os.path.exists(img_path):
            return web.json_response({"ok": False, "error": "file not found"}, status=404)
        os.remove(img_path)
        for json_path in (_meta_path(img_path), _meta_path_legacy(img_path)):
            if os.path.exists(json_path):
                try:
                    os.remove(json_path)
                except Exception:
                    pass
        _favorites_remove(filename)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)})


def _scan(folder_key, extensions=None):
    exts = extensions or [".safetensors", ".ckpt", ".pt", ".pth"]
    try:
        bases = folder_paths.get_folder_paths(folder_key)
    except Exception:
        return ["none"]
    found = []
    for base in bases:
        if not os.path.isdir(base):
            continue
        # followlinks=True so symlinked LoRA folders (e.g. on another drive) are scanned
        for root, _, files in os.walk(base, followlinks=True):
            for fn in files:
                if any(fn.lower().endswith(e) for e in exts):
                    found.append(os.path.relpath(os.path.join(root, fn), base))
    return sorted(found) if found else ["none"]


def _scan_path(path, extensions=None):
    exts = extensions or [".safetensors", ".ckpt", ".pt", ".pth"]
    if not os.path.isdir(path):
        return ["none"]
    found = []
    # followlinks=True so symlinked folders (e.g. on another drive) are scanned
    for root, _, files in os.walk(path, followlinks=True):
        for fn in files:
            if any(fn.lower().endswith(e) for e in exts):
                found.append(os.path.relpath(os.path.join(root, fn), path))
    return sorted(found) if found else ["none"]


@PromptServer.instance.routes.get("/flux_klein_canvas/models")
async def get_models(request):
    # Diffusion models (unet) â€” flux-2-klein variants
    try:
        diff = _scan("diffusion_models")
    except Exception:
        try:
            import folder_paths as fp
            diff = _scan_path(os.path.join(os.path.dirname(getattr(fp, "models_dir", "")), "models", "diffusion_models"))
        except Exception:
            diff = ["none"]

    # Text encoders
    try:
        te = _scan("text_encoders")
    except Exception:
        te = ["none"]

    # VAEs
    try:
        vaes = _scan("vae")
    except Exception:
        vaes = ["none"]

    # LoRAs
    try:
        loras = _scan("loras")
    except Exception:
        loras = ["none"]

    return web.json_response({
        "diffusion_models": diff,
        "text_encoders": te,
        "vaes": vaes,
        "loras": loras,
    })


def _read_safetensors_header(path):
    """Read only the JSON header from a .safetensors file (no weight loading)."""
    try:
        with open(path, "rb") as f:
            length_bytes = f.read(8)
            if len(length_bytes) < 8:
                return None
            import struct
            header_len = struct.unpack("<Q", length_bytes)[0]
            if header_len > 100 * 1024 * 1024:  # sanity: skip if >100MB header
                return None
            header_bytes = f.read(header_len)
        return json.loads(header_bytes.decode("utf-8"))
    except Exception:
        return None


def _extract_trigger_words(header):
    """Extract trigger words from safetensors metadata dict."""
    if not header:
        return []
    meta = header.get("__metadata__", {})
    if not isinstance(meta, dict):
        return []

    triggers = []

    # 1. modelspec.trigger_phrase (single string)
    v = meta.get("modelspec.trigger_phrase") or meta.get("trigger_phrase") or meta.get("trigger_word")
    if v and isinstance(v, str) and v.strip():
        triggers.extend([t.strip() for t in v.split(",") if t.strip()])

    # 2. ss_trigger_words (JSON array or plain string)
    raw = meta.get("ss_trigger_words")
    if raw:
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    triggers.extend([str(t).strip() for t in parsed if str(t).strip()])
                elif isinstance(parsed, str) and parsed.strip():
                    triggers.extend([t.strip() for t in parsed.split(",") if t.strip()])
            except Exception:
                triggers.extend([t.strip() for t in raw.split(",") if t.strip()])
        elif isinstance(raw, list):
            triggers.extend([str(t).strip() for t in raw if str(t).strip()])

    # 3. ss_tag_frequency â€” pick top-level keys that look like trigger words
    #    (skip generic tags like quality/style boilerplates)
    tag_freq_raw = meta.get("ss_tag_frequency")
    if tag_freq_raw and not triggers:
        try:
            tag_freq = json.loads(tag_freq_raw) if isinstance(tag_freq_raw, str) else tag_freq_raw
            if isinstance(tag_freq, dict):
                # tag_freq is {dataset_name: {tag: count, ...}, ...}
                all_tags = {}
                for ds_tags in tag_freq.values():
                    if isinstance(ds_tags, dict):
                        for tag, count in ds_tags.items():
                            all_tags[tag] = all_tags.get(tag, 0) + (count if isinstance(count, int) else 0)
                if all_tags:
                    # Return the top 5 most frequent tags as hints
                    top = sorted(all_tags.items(), key=lambda x: x[1], reverse=True)[:5]
                    triggers.extend([t for t, _ in top])
        except Exception:
            pass

    # Deduplicate preserving order
    seen = set()
    result = []
    for t in triggers:
        if t.lower() not in seen:
            seen.add(t.lower())
            result.append(t)
    return result


@PromptServer.instance.routes.get("/flux_klein_canvas/lora_triggers")
async def lora_triggers(request):
    lora_name = request.query.get("name", "")
    if not lora_name:
        return web.json_response({"ok": False, "error": "no name"}, status=400)
    try:
        bases = folder_paths.get_folder_paths("loras")
    except Exception:
        return web.json_response({"ok": False, "error": "cannot resolve loras folder"}, status=500)
    for base in bases:
        candidate = os.path.normpath(os.path.join(base, lora_name))
        # Path traversal guard
        try:
            Path(candidate).resolve().relative_to(Path(base).resolve())
        except Exception:
            continue
        if os.path.isfile(candidate) and candidate.lower().endswith(".safetensors"):
            header = _read_safetensors_header(candidate)
            triggers = _extract_trigger_words(header)
            return web.json_response({"ok": True, "triggers": triggers, "name": lora_name})
    return web.json_response({"ok": False, "error": "file not found", "triggers": []})


# Stores the currently-shown output image per node instance (keyed by the node's
# graph id). JS posts here after every generation and whenever the user clicks
# through a batch, so noop() can hand the visible image to downstream nodes on the
# next graph run. Value: {"filename","subfolder","type"} or None.
_last_output_by_node = {}


def _resolve_image_file(filename, subfolder="", ftype="output"):
    """Safely resolve a generated image to an absolute path. Handles the output
    folder and ComfyUI's temp folder (used for unsaved auto-save-off results)."""
    if not filename:
        return None
    if ftype == "temp":
        base = Path(folder_paths.get_temp_directory()).resolve()
    elif ftype == "input":
        base = Path(folder_paths.get_input_directory()).resolve()
    else:
        base = Path(_get_output_dir()).resolve()
    target = base
    if subfolder:
        target = target / subfolder
    target = (target / filename).resolve()
    try:
        target.relative_to(base)  # path-traversal guard
    except Exception:
        return None
    return str(target) if os.path.isfile(target) else None


@PromptServer.instance.routes.post("/flux_klein_canvas/set_output")
async def set_output(request):
    try:
        data = await request.json()
        node_id = str(data.get("node_id", ""))
        if not node_id:
            return web.json_response({"ok": False, "error": "no node_id"}, status=400)
        fn = data.get("filename")
        if fn:
            _last_output_by_node[node_id] = {
                "filename": fn,
                "subfolder": data.get("subfolder", "") or "",
                "type": data.get("type", "output") or "output",
            }
        else:
            _last_output_by_node.pop(node_id, None)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


def _empty_image_tensor():
    import torch
    return torch.zeros((1, 64, 64, 3), dtype=torch.float32)


def _load_image_tensor(info):
    """Load a stored output image into a ComfyUI IMAGE tensor [1,H,W,3] float32."""
    try:
        import torch
        import numpy as np
        from PIL import Image, ImageOps
    except Exception:
        return _empty_image_tensor()
    if not info:
        return _empty_image_tensor()
    path = _resolve_image_file(info.get("filename", ""), info.get("subfolder", ""), info.get("type", "output"))
    if not path:
        return _empty_image_tensor()
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        arr = np.array(img).astype(np.float32) / 255.0
        return torch.from_numpy(arr)[None, ]
    except Exception:
        return _empty_image_tensor()


class FluxKleinCanvasNode:
    @classmethod
    def INPUT_TYPES(cls):
        # `prompt` is an optional STRING input; when connected, JS reads its value at
        # generate time and uses it in place of the prompt box (per mode).
        return {
            "required": {},
            "optional": {"prompt": ("STRING", {"forceInput": True})},
            "hidden": {"unique_id": "UNIQUE_ID"},
        }
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "noop"
    CATEGORY = "One Node"
    OUTPUT_NODE = True

    def noop(self, unique_id=None, **kwargs):
        # Return the image currently shown in this node's preview (set by JS via
        # POST /flux_klein_canvas/set_output after each generation / batch step).
        info = _last_output_by_node.get(str(unique_id))
        return {"result": (_load_image_tensor(info),)}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")


NODE_CLASS_MAPPINGS = {"FluxKleinCanvasNode": FluxKleinCanvasNode}
NODE_DISPLAY_NAME_MAPPINGS = {"FluxKleinCanvasNode": "One Node Canvas · FLUX.2 [klein]"}

_migrate_meta_sidecars()
