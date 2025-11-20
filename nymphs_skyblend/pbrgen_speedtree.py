# ============================================================
# pbrgen_speedtree.py — SpeedTree → Skyrim PBR (CS/PBR NIF Patcher layout)
# Folder-based version (select directory instead of texture)
# ============================================================

import os
import glob
import traceback
import importlib

import bpy
import numpy as np
from PIL import Image

try:
    import skyrim_mat_patcher.pbrgen as pbrgen
except Exception:
    pbrgen = None


# --- helper: find base stems ---
def _stem_from_name(fname: str) -> str:
    base = os.path.splitext(os.path.basename(fname))[0]
    for pat in [
        "_BaseColor", "_Basecolour", "_BaseColour", "_Base_Color", "_Albedo",
        "_Colour", "_Color", "_Normal", "_NormalMap", "_Roughness", "_Rough",
        "_Gloss", "_Glossiness", "_AO", "_AmbientOcclusion", "_Occlusion",
        "_Metallic", "_Metalness", "_Metal", "_Height", "_Displacement",
        "_ParallaxHeight", "_Parallax", "_Depth", "_Opacity", "_Mask",
        "_Cutout", "_Alpha", "_Subsurface", "_SubSurface", "_SSS",
        "_SubsurfaceColor", "_CoatColor", "_SubsurfaceAmount", "_Translucency",
        "_Transmission", "_Specular"
    ]:
        if base.endswith(pat):
            return base[: -len(pat)]
    return base


# --- find and group maps ---
def _find_maps(folder: str):
    folder = bpy.path.abspath(folder)
    files = sorted(glob.glob(os.path.join(folder, "*.png")))
    groups = {}
    for fp in files:
        stem = _stem_from_name(fp)
        g = groups.setdefault(stem, {})
        name = os.path.splitext(os.path.basename(fp))[0].lower()

        if "subsurfaceamount" in name or name.endswith("_subsurfaceamount"):
            g["subsurface_amount"] = fp; continue
        if "subsurfacecolor" in name or name.endswith("_subsurfacecolor"):
            g["subsurface_color"] = fp; continue
        if "opacity" in name or "cutout" in name or name.endswith("_mask") or name.endswith("_alpha"):
            g["mask"] = fp; continue
        if "normal" in name:
            g["normal"] = fp; continue
        if "roughness" in name or name.endswith("_rough"):
            g["roughness"] = fp; continue
        if "gloss" in name or "glossiness" in name:
            g["gloss"] = fp; continue
        if name.endswith("_ao") or "ambientocclusion" in name or name.endswith("_occlusion"):
            g["ao"] = fp; continue
        if "metallic" in name or "metalness" in name or name.endswith("_metal"):
            g["metal"] = fp; continue
        if "height" in name or "displacement" in name or "parallaxheight" in name or name.endswith("_parallax") or name.endswith("_depth"):
            g["height"] = fp; continue

        # BaseColor (explicit only)
        if any(k in name for k in ["basecolor", "basecolour", "base_colour", "base_color", "albedo", "diffuse"]):
            if "subsurface" not in name:
                g.setdefault("basecolor", fp); continue

        # Fallback (leaf/branch)
        if "leaf" in name or "branch" in name:
            g.setdefault("basecolor", fp)

    return groups


# --- loaders and helpers ---
def _load_L(path, fill=255, match_size=None):
    if not path or not os.path.isfile(path):
        if match_size is None:
            return Image.new("L", (4, 4), fill)
        return Image.new("L", match_size, fill)
    img = Image.open(path).convert("L")
    if match_size and img.size != match_size:
        img = img.resize(match_size, Image.BILINEAR)
    return img


def _np_u8(img: Image.Image) -> np.ndarray:
    return np.array(img, dtype=np.uint8)


def _get_export_dds():
    try:
        import blender_dds_addon.ui.export_dds as export_dds
        return export_dds.export_as_dds
    except Exception:
        try:
            edds = importlib.import_module("blender_dds_addon.ui.export_dds")
            return edds.export_as_dds
        except Exception as e:
            print(f"[PBRGen-ST] ❌ Could not import Matyalatte DDS exporter: {e}")
            return None


def _export_rgba_array_as_dds(rgba_u8: np.ndarray, width: int, height: int, name: str, out_path: str):
    export_as_dds = _get_export_dds()
    if export_as_dds is None:
        print("[PBRGen-ST] ❌ DDS exporter not available.")
        return False
    rgba_f32 = (rgba_u8.astype(np.float32) / 255.0).reshape(height * width * 4)
    img = None
    try:
        img = bpy.data.images.new(name=name, width=width, height=height, alpha=True)
        img.pixels = rgba_f32.tolist()
        export_as_dds(bpy.context, img, bpy.path.abspath(out_path))
        print(f"[PBRGen-ST] ✅ DDS saved: {os.path.basename(out_path)}")
        return True
    finally:
        try:
            if img: bpy.data.images.remove(img)
        except Exception: pass


def _save_dds_from_path(src_path: str, dst_path: str, flip_vertical: bool = False):
    export_as_dds = _get_export_dds()
    if export_as_dds is None:
        print("[PBRGen-ST] ❌ DDS exporter not available.")
        return False
    src_path = bpy.path.abspath(src_path)
    dst_path = bpy.path.abspath(dst_path)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    img = None
    try:
        pil = Image.open(src_path).convert("RGBA")
        if flip_vertical: pil = pil.transpose(Image.FLIP_TOP_BOTTOM)
        w, h = pil.size
        rgba = np.array(pil, dtype=np.uint8).reshape(w * h * 4).astype(np.float32) / 255.0
        img = bpy.data.images.new(name=os.path.basename(src_path), width=w, height=h, alpha=True)
        img.pixels = rgba.tolist()
        export_as_dds(bpy.context, img, dst_path)
        print(f"[PBRGen-ST] ✅ DDS saved: {os.path.basename(dst_path)}")
        return True
    finally:
        try:
            if img: bpy.data.images.remove(img)
        except Exception: pass


def _pack_rmaos_arrays(rough, metal, ao, s_amount):
    h, w = rough.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., 0] = rough
    out[..., 1] = metal
    out[..., 2] = ao
    out[..., 3] = s_amount if s_amount is not None else 255
    return out


# --- main packer ---
def pack_speedtree_folder(folder_path: str, output_dir: str | None = None):
    folder_path = bpy.path.abspath(folder_path or "")
    out_dir = bpy.path.abspath(output_dir) if output_dir else os.path.join(folder_path, "New Folder")
    os.makedirs(out_dir, exist_ok=True)
    groups = _find_maps(folder_path)
    if not groups:
        print("[PBRGen-ST] ❌ No SpeedTree maps found.")
        return 0

    done = 0
    for stem, maps in groups.items():
        print(f"[PBRGen-ST] ▶ {stem}: maps={list(maps.keys())}")

        base_path = maps.get("basecolor")
        mask_path = maps.get("mask")
        if base_path:
            base_img = Image.open(base_path).convert("RGBA")
            base_rgba = np.array(base_img, dtype=np.uint8)
            if mask_path and os.path.isfile(mask_path):
                base_rgba[..., 3] = np.array(Image.open(mask_path).convert("L").resize(base_img.size, Image.BILINEAR), dtype=np.uint8)
            base_rgba = np.flipud(base_rgba)
            _export_rgba_array_as_dds(base_rgba, base_img.width, base_img.height, f"{stem}_d", os.path.join(out_dir, f"{stem}.dds"))
        else:
            print(f"[PBRGen-ST] ⚠️ Missing BaseColor for {stem}")

        ref_first = next((maps.get(k) for k in ["roughness", "gloss", "ao", "metal", "basecolor", "normal"] if maps.get(k)), None)
        ref_size = Image.open(ref_first).size if ref_first else (4, 4)
        if "roughness" in maps:
            rough_img = _load_L(maps["roughness"], match_size=ref_size)
        elif "gloss" in maps:
            gloss_img = _load_L(maps["gloss"], match_size=ref_size)
            rough_img = Image.fromarray(255 - _np_u8(gloss_img), "L")
        else:
            rough_img = _load_L(None, fill=128, match_size=ref_size)
        metal_img = _load_L(maps.get("metal"), fill=0, match_size=ref_size)
        ao_img = _load_L(maps.get("ao"), fill=255, match_size=ref_size)
        s_amount_img = _load_L(maps.get("subsurface_amount"), fill=255, match_size=ref_size) if maps.get("subsurface_amount") else None
        rmaos = _pack_rmaos_arrays(_np_u8(rough_img), _np_u8(metal_img), _np_u8(ao_img), _np_u8(s_amount_img) if s_amount_img is not None else None)
        rmaos = np.flipud(rmaos)
        _export_rgba_array_as_dds(rmaos, ref_size[0], ref_size[1], f"{stem}_RMAOS", os.path.join(out_dir, f"{stem}_RMAOS.dds"))

        if maps.get("subsurface_color"):
            _save_dds_from_path(maps["subsurface_color"], os.path.join(out_dir, f"{stem}_s.dds"), flip_vertical=True)
        if maps.get("normal"):
            _save_dds_from_path(maps["normal"], os.path.join(out_dir, f"{stem}_n.dds"), flip_vertical=True)
        if maps.get("height"):
            _save_dds_from_path(maps["height"], os.path.join(out_dir, f"{stem}_p.dds"), flip_vertical=True)

        done += 1
    print(f"[PBRGen-ST] ✅ Finished: {done} set(s) packed to DDS in {out_dir}")
    return done


# --- Blender operator ---
from bpy.types import Operator

class PBRGEN_OT_pack_speedtree(Operator):
    bl_idname = "pbrgen.pack_speedtree"
    bl_label = "Pack SpeedTree Textures → CS PBR DDS set"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            s = getattr(context.scene, "pbrgen", None)
            folder = getattr(s, "source_folder", "")
            if not folder or not os.path.isdir(bpy.path.abspath(folder)):
                self.report({'ERROR'}, "Select a valid source folder.")
                return {'CANCELLED'}
            output_dir = bpy.path.abspath(s.output_dir) if getattr(s, "output_dir", "") else os.path.join(folder, "New Folder")
            count = pack_speedtree_folder(folder, output_dir)
            if count <= 0:
                self.report({'WARNING'}, "No SpeedTree sets found in the Source folder.")
            else:
                self.report({'INFO'}, f"Packed {count} material set(s).")
            return {'FINISHED'}
        except Exception as e:
            print(traceback.format_exc())
            self.report({'ERROR'}, f"SpeedTree packing failed: {e}")
            return {'CANCELLED'}


def register():
    bpy.utils.register_class(PBRGEN_OT_pack_speedtree)

def unregister():
    bpy.utils.unregister_class(PBRGEN_OT_pack_speedtree)

if __name__ == "__main__":
    register()
