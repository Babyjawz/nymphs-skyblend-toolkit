# ============================================================
# PBRGen: Skyrim PBR Texture Generator (Presets + Clarified UI)
# Blender 4.5
#
# What you get in this build
# --------------------------
# - Two clearly separated control groups:
#   1) Base Map Generation    -> how height/rough/AO/metal are DERIVED from the diffuse
#   2) Material Look Tweaks   -> how those maps are TONED before packing (RMAOS/Height behavior)
# - Presets that set BOTH groups (Armor, Reflective Metal/Silverware, Wood, Stone, Cloth, Leather,
#   Skin, Leaf, Glass, Default).
# - Optional extra outputs: _m (Complex Mask), _s (Spec/Subsurface), _g (Emissive).
# - Correct RMAOS packing per PBRNifPatcher / ParallaxGen conventions:
#   R=Roughness (linear), G=Metalness, B=AO (mild contrast boost), A=255.
# - Original, trusted save/path logic using Matyalatte DDS exporter first, PNG fallback.
# - Progress bar visible; clean console logs.
#
# Notes:
# - `_s` presets affect ONLY the _s map (spec/subsurface tint + gloss alpha). They do not change RMAOS.
# - Generation sliders drive how the base maps are computed; Tweaks adjust the final balance.
# - Presets set both generation and tweaks so artists get great material-specific defaults instantly.
# ============================================================

# ============================================================
# PBRGen: Skyrim PBR Texture Generator (Presets + Clarified UI)
# Blender 4.5
#
# This build includes:
# - Export Targets: Skyrim (DDS), Authoring (PNG), SpeedTree (PNG)
# - Export Modes: Full / RMAOS+Separates / Separates Only (SpeedTree forces Separates)
# - Collapsible UI for Base Map Generation + Material Look Tweaks (default collapsed)
# - Correct RMAOS packing; optional _m / _s / _g
# - DDS via Matyalatte first, PNG fallback if needed
# - NEW: Universal glow/opacity with Binary toggle; edge bleed for PNG/SpeedTree; Subsurface% output
# - NEW (Oct 2025): Square full-width Glow/Opacity PREVIEW under the glow settings
# ============================================================

bl_info = {
    "name": "PBRGen (Skyrim PBR Texture Generator)",
    "author": "BabyJaws Studios (+ helper merge)",
    "version": (1, 8, 0),
    "blender": (4, 5, 0),
    "location": "Shader/Node Editor or Image Editor > PBRGen",
    "description": "Generate Skyrim PBR textures with presets, clear UI, Complex Mask (_m), Spec/Subsurface (_s), correct RMAOS, and original save/path logic. Adds SpeedTree/Authoring outputs. Now with live Glow/Opacity preview.",
    "category": "Material",
}

import os, sys, traceback
import bpy
import numpy as np
from PIL import Image


# --- UI redraw helper (context-aware) ---
def force_ui_redraw(context=None):
    """Force the PBRGen panel to repaint by nudging the UI region width ±1px."""
    try:
        # Prefer the region that invoked the update (more reliable)
        area = None
        if context is not None and getattr(context, "area", None):
            area = context.area
        if area is None:
            area = next(
                (
                    a
                    for a in bpy.context.screen.areas
                    if a.type in {"NODE_EDITOR", "IMAGE_EDITOR"}
                ),
                None,
            )
        if not area:
            return
        region = next((r for r in area.regions if r.type == "UI"), None)
        if not region:
            return
        region.width += 1
        region.width -= 1
        try:
            region.tag_redraw()
            area.tag_redraw()
        except Exception:
            pass
    except Exception:
        pass


from bpy.types import Operator, Panel, PropertyGroup


def _pbrgen_pick_primary_image(folder_abs_path: str) -> str | None:
    """Return a representative image path from a folder (prefers BaseColor/Albedo/Diffuse)."""
    import glob, os

    if not folder_abs_path or not os.path.isdir(folder_abs_path):
        return None
    exts = ("*.png", "*.jpg", "*.jpeg", "*.dds", "*.tga")
    preferred = (
        "basecolor",
        "base_colour",
        "base_color",
        "albedo",
        "diffuse",
        "color",
        "colour",
    )
    files = []
    for ext in exts:
        files.extend(sorted(glob.glob(os.path.join(folder_abs_path, ext))))
    for fp in files:
        name = os.path.basename(fp).lower()
        if any(k in name for k in preferred):
            return fp
    return files[0] if files else None


from bpy.props import (
    StringProperty,
    BoolProperty,
    FloatProperty,
    EnumProperty,
    PointerProperty,
)

# ============================================================
# Utility helpers
# ============================================================
PREVIEW_IMG_NAME = "PBRGen_GlowPreview"
PREVIEW_SIZE = 256  # square; Blender will scale to panel width


def _to_u8(a: np.ndarray) -> np.ndarray:
    """Clamp to 0..255 and convert to uint8."""
    return np.clip(a, 0, 255).astype(np.uint8)


def _srgb_to_linear_u8(u8: np.ndarray) -> np.ndarray:
    """Approximate sRGB->linear for grayscale uint8 image, returns 0..1 float."""
    x = (u8.astype(np.float32) / 255.0).clip(0.0, 1.0)
    return np.power(x, 2.2)


def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def to_gray_u8(rgb_u8: np.ndarray) -> np.ndarray:
    """Convert RGB uint8 to luminance uint8 using Rec.709 coefficients."""
    if rgb_u8.ndim == 2:
        return _to_u8(rgb_u8)
    r, g, b = rgb_u8[..., 0], rgb_u8[..., 1], rgb_u8[..., 2]
    return _to_u8(0.2126 * r + 0.7152 * g + 0.0722 * b)


# --- Edge-bleed (alpha dilation) without external deps ---
def _alpha_bleed_under_rgba(src_rgba: np.ndarray, iterations: int = 12) -> np.ndarray:
    """
    Fill RGB under fully transparent pixels by propagating neighboring visible colors.
    This avoids dark halos when alpha-blended (PNG/SpeedTree). No-op if alpha is all 255.
    """
    rgba = src_rgba.copy()
    H, W = rgba.shape[:2]
    if rgba.shape[2] != 4:  # safety
        return rgba

    a = rgba[..., 3]
    if np.all(a == 255):
        return rgba  # nothing to bleed

    rgb = rgba[..., :3].astype(np.float32)
    alpha_mask = a > 0

    # Simple 8-connected neighbor color propagation into transparent pixels
    for _ in range(max(0, int(iterations))):
        # find transparent pixels that border any opaque pixel
        border = np.zeros_like(alpha_mask)
        # shifts
        border[:-1, :] |= alpha_mask[1:, :]
        border[1:, :] |= alpha_mask[:-1, :]
        border[:, :-1] |= alpha_mask[:, 1:]
        border[:, 1:] |= alpha_mask[:, :-1]
        border[:-1, :-1] |= alpha_mask[1:, 1:]
        border[1:, 1:] |= alpha_mask[:-1, :-1]
        border[:-1, 1:] |= alpha_mask[1:, :-1]
        border[1:, :-1] |= alpha_mask[:-1, 1:]

        fill_here = (~alpha_mask) & border
        if not np.any(fill_here):
            break

        # average of available opaque neighbors (up to 8)
        count = np.zeros((H, W), dtype=np.float32)
        acc = np.zeros((H, W, 3), dtype=np.float32)

        def add_neighbor(mask, shift_y, shift_x):
            yy0, yy1 = max(0, shift_y), min(H, H + shift_y)
            xx0, xx1 = max(0, shift_x), min(W, W + shift_x)
            src_y0, src_y1 = max(0, -shift_y), min(H, H - shift_y)
            src_x0, src_x1 = max(0, -shift_x), min(W, W - shift_x)
            m = np.zeros((H, W), dtype=bool)
            m[yy0:yy1, xx0:xx1] = mask[src_y0:src_y1, src_x0:src_x1]
            acc[yy0:yy1, xx0:xx1] += (
                rgb[src_y0:src_y1, src_x0:src_x1] * m[yy0:yy1, xx0:xx1][..., None]
            )
            count[yy0:yy1, xx0:xx1] += m[yy0:yy1, xx0:xx1].astype(np.float32)

        add_neighbor(alpha_mask, -1, 0)
        add_neighbor(alpha_mask, 1, 0)
        add_neighbor(alpha_mask, 0, -1)
        add_neighbor(alpha_mask, 0, 1)
        add_neighbor(alpha_mask, -1, -1)
        add_neighbor(alpha_mask, -1, 1)
        add_neighbor(alpha_mask, 1, -1)
        add_neighbor(alpha_mask, 1, 1)

        avg = np.zeros_like(acc)
        nz = count > 0
        avg[nz] = acc[nz] / count[nz][..., None]
        rgb[fill_here] = avg[fill_here]
        alpha_mask[fill_here] = True  # become part of source for next iteration

    out = np.empty_like(rgba)
    out[..., :3] = _to_u8(rgb)
    out[..., 3] = a
    return out


# ============================================================
# Save helper (Matyalatte DDS first, PNG fallback) — ORIGINAL LOGIC
# ============================================================


def save_output(
    img_array: np.ndarray, src_path: str, out_dir: str, base: str, suffix: str
):
    """
    Save numpy array as DDS (via Matyalatte addon) or PNG fallback.
    This function matches the original trusted behavior you asked to preserve.
    """
    folder = bpy.path.abspath(out_dir)
    _ensure_dir(folder)
    name = f"{base}_{suffix}" if suffix else f"{base}"
    out_dds = os.path.join(folder, f"{name}.dds")
    out_png = os.path.join(folder, f"{name}.png")

    # Convert numpy->PIL for consistent pipeline
    if img_array.ndim == 2:
        im = Image.fromarray(_to_u8(img_array), mode="L")
    elif img_array.ndim == 3 and img_array.shape[2] == 3:
        im = Image.fromarray(_to_u8(img_array), mode="RGB")
    elif img_array.ndim == 3 and img_array.shape[2] == 4:
        im = Image.fromarray(_to_u8(img_array), mode="RGBA")
    else:
        raise ValueError("[PBRGen] Unsupported image array shape for saving.")

    # Respect Export Target for file format
    s = getattr(bpy.context.scene, "pbrgen", None)
    if s and getattr(s, "export_target", "SKYRIM") in {"PNG", "SPEEDTREE"}:
        im.save(out_png)
        print(f"[PBRGen] Saved PNG (target={s.export_target}): {out_png}")
        return out_png

    # Attempt DDS via Matyalatte addon
    try:
        import importlib

        edds = importlib.import_module("blender_dds_addon.ui.export_dds")

        # Create temporary Blender image datablock for exporter
        w, h = im.width, im.height
        bpy_img = bpy.data.images.new(
            name=f"{base}_{suffix}", width=w, height=h, alpha=True, float_buffer=False
        )
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float32) / 255.0
        bpy_img.pixels = rgba.flatten()

        # Pull our PBRGen settings so we can sync options
        s = getattr(bpy.context.scene, "pbrgen", None)
        dopt = getattr(bpy.context.scene, "dds_options", None)

        if dopt is not None:
            if hasattr(s, "dxgi_format") and hasattr(dopt, "dxgi_format"):
                dopt.dxgi_format = s.dxgi_format
            if hasattr(s, "make_mipmaps") and hasattr(dopt, "no_mip"):
                dopt.no_mip = not s.make_mipmaps
            if hasattr(dopt, "texture_type"):
                dopt.texture_type = "2d"

        try:
            if not hasattr(bpy.context.scene, "dds_options"):
                if hasattr(edds, "put_export_options"):
                    edds.put_export_options(bpy.context)
                    print("[PBRGen] Registered DDS export options")

            dds_opts = getattr(bpy.context.scene, "dds_options", None)
            if dds_opts:
                if not getattr(
                    dds_opts, "dxgi_format", None
                ) or dds_opts.dxgi_format in ("", "NONE"):
                    dds_opts.dxgi_format = "BC7_UNORM"
                    print("[PBRGen] Auto-set DXGI format to BC7_UNORM")
                if hasattr(dds_opts, "generate_mipmaps"):
                    dds_opts.generate_mipmaps = True
                if hasattr(dds_opts, "texture_type"):
                    dds_opts.texture_type = "2d"
                if hasattr(dds_opts, "no_mip"):
                    dds_opts.no_mip = False

            edds.export_as_dds(bpy.context, bpy_img, out_dds)
            bpy.data.images.remove(bpy_img)
            print(f"[PBRGen] ✅ Saved DDS via Matyalatte export_as_dds: {out_dds}")
            return out_dds

        except Exception as e:
            print(f"[PBRGen] DDS export failed ({e}); writing PNG instead.")
            print(traceback.format_exc())
            im.save(out_png)
            print(f"[PBRGen] Saved PNG: {out_png}")
            return out_png

    except ModuleNotFoundError:
        print(
            "[PBRGen] Matyalatte DDS Addon not found — please install and enable it for DDS export."
        )
    except Exception as e:
        print(f"[PBRGen] DDS export failed ({e}); writing PNG instead.")
        print(traceback.format_exc())

    im.save(out_png)
    print(f"[PBRGen] Saved PNG: {out_png}")
    return out_png


# ============================================================
# Map derivation helpers (normals / roughness / ao)
# ============================================================


def sobel_normals_from_height(
    height_u8: np.ndarray, strength: float = 2.0
) -> np.ndarray:
    """Very basic Sobel-based normal derivation from a height/gray map."""
    h = height_u8.astype(np.float32) / 255.0

    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)

    def conv(img, k):
        pad = 1
        imgp = np.pad(img, ((pad, pad), (pad, pad)), mode="edge")
        out = np.zeros_like(img, dtype=np.float32)
        H, W = img.shape
        for y in range(H):
            for x in range(W):
                region = imgp[y : y + 3, x : x + 3]
                out[y, x] = float(np.sum(region * k))
        return out

    gx = conv(h, kx)
    gy = conv(h, ky)
    nx = -gx * strength
    ny = -gy * strength
    nz = np.ones_like(h)

    length = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-8
    nx /= length
    ny /= length
    nz /= length

    rgb = np.stack(
        [(nx * 0.5 + 0.5) * 255, (ny * 0.5 + 0.5) * 255, (nz * 0.5 + 0.5) * 255],
        axis=-1,
    )
    return _to_u8(rgb)


def box_blur(gray_u8: np.ndarray, radius_px: int = 3) -> np.ndarray:
    """Naive box blur for illustrative purposes (no external deps)."""
    if radius_px <= 0:
        return gray_u8
    src = gray_u8.astype(np.float32)
    r = int(radius_px)
    tmp = np.copy(src)
    out = np.copy(src)
    H, W = src.shape

    for y in range(H):
        for x in range(W):
            left = max(0, x - r)
            right = min(W - 1, x + r)
            out[y, x] = np.mean(src[y, left : right + 1])
    tmp[:] = out

    for x in range(W):
        for y in range(H):
            top = max(0, y - r)
            bot = min(H - 1, y + r)
            out[y, x] = np.mean(tmp[top : bot + 1, x])

    return _to_u8(out)


def roughness_from_variance(
    gray_u8: np.ndarray, radius_px: float = 3.0, gain: float = 1.2
) -> np.ndarray:
    """Variance-based roughness: higher texture variance -> higher roughness."""
    g = gray_u8.astype(np.float32) / 255.0
    g2 = g * g
    mean = box_blur(_to_u8(g * 255.0), int(round(radius_px))).astype(np.float32) / 255.0
    mean2 = (
        box_blur(_to_u8(g2 * 255.0), int(round(radius_px))).astype(np.float32) / 255.0
    )
    var = np.clip(mean2 - mean * mean, 0.0, 1.0)

    lo, hi = np.percentile(var, 5.0), np.percentile(var, 95.0)
    hi = max(hi, lo + 1e-6)
    norm = np.clip((var - lo) / (hi - lo), 0.0, 1.0)

    rough = np.clip(norm * gain, 0.0, 1.0)
    return _to_u8(rough * 255.0)


def ao_from_convexity(
    height_u8: np.ndarray, radius_px: int = 8, intensity: float = 1.0
) -> np.ndarray:
    """Simple 'convexity AO' using blurred height delta."""
    h = height_u8.astype(np.float32) / 255.0
    blur = box_blur(height_u8, radius_px).astype(np.float32) / 255.0
    cv = blur - h
    lo, hi = np.percentile(cv, 5.0), np.percentile(cv, 95.0)
    hi = max(hi, lo + 1e-6)
    occl = np.clip((cv - lo) / (hi - lo), 0.0, 1.0)
    ao = 1.0 - np.clip(occl * intensity, 0.0, 1.0)
    return _to_u8(ao * 255.0)


# ============================================================
# Additional Builders: Emissive, Complex Mask, S-map, RMAOS
# ============================================================


def make_emissive(
    src_rgb_u8: np.ndarray,
    threshold: float = 200.0,
    strength: float = 1.0,
    binary: bool = False,
) -> np.ndarray:
    """
    Universal emissive / opacity generator (rebuilt for clean SpeedTree cutouts and smooth Skyrim glow):
    - Uses perceptual luminance (Rec.709)
    - binary=True: strict 0/255 mask (SpeedTree Opacity)
    - binary=False: soft ramp above threshold; normalized to 0..255 then *strength
    """
    rgb = src_rgb_u8.astype(np.float32) / 255.0
    gray = (0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]) * 255.0

    if binary:
        glow = np.where(gray >= threshold, 255.0, 0.0)
        return _to_u8(glow)

    # Smooth: map [threshold..255] -> [0..255], then scale by strength
    denom = max(1.0, 255.0 - float(threshold))
    norm = np.clip((gray - threshold) / denom, 0.0, 1.0)
    glow = np.clip(norm * 255.0 * float(strength), 0.0, 255.0)
    return _to_u8(glow)


def make_complex_mask_m(
    metal_u8: np.ndarray, ao_u8: np.ndarray, height_u8: np.ndarray
) -> np.ndarray:
    """
    Complex/Env Mask (_m):
      R: Metalness
      G: Reflection/Spec Mask from height & (1-AO)
      B: Ambient Occlusion
      A: 255
    """
    h = height_u8.astype(np.float32) / 255.0
    ao = ao_u8.astype(np.float32) / 255.0
    metal = metal_u8.astype(np.uint8)
    g = np.clip(0.8 * h + 0.2 * (1.0 - ao), 0.0, 1.0)
    g_u8 = _to_u8(g * 255.0)
    a = np.full_like(metal, 255, np.uint8)
    return np.dstack([metal, g_u8, ao_u8.astype(np.uint8), a])


def make_s_map(
    src_rgb_u8: np.ndarray, rough_u8: np.ndarray, mode: str = "DEFAULT"
) -> np.ndarray:
    """
    Spec/Subsurface (_s) map:
      RGB: spec/subsurface tint (preset-tuned)
      A  : gloss = (1 - roughness)^exp
    """
    tint = src_rgb_u8.astype(np.float32) / 255.0
    rough = rough_u8.astype(np.float32) / 255.0
    m = (mode or "").upper()
    gloss_exp = 0.75

    if m in ("LEAF", "FOLIAGE"):
        tint[..., 1] = np.clip(tint[..., 1] * 1.15, 0.0, 1.0)
        tint = np.clip(tint * 1.03, 0.0, 1.0)
        gloss_exp = 0.60
    elif m == "SKIN":
        tint[..., 0] = np.clip(tint[..., 0] * 1.06, 0.0, 1.0)
        tint[..., 1] = np.clip(tint[..., 1] * 1.03, 0.0, 1.0)
        gloss_exp = 0.50
    elif m in ("METAL", "ARMOR", "ARMOUR"):
        l = (0.2126 * tint[..., 0] + 0.7152 * tint[..., 1] + 0.0722 * tint[..., 2])[
            ..., None
        ]
        tint = 0.65 * tint + 0.35 * np.repeat(l, 3, axis=2)
        tint[..., 2] = np.clip(tint[..., 2] * 1.03, 0.0, 1.0)
        gloss_exp = 1.20

    gloss = np.power(np.clip(1.0 - rough, 0.0, 1.0), gloss_exp)
    rgb_u8 = _to_u8(tint * 255.0)
    a_u8 = _to_u8(gloss * 255.0).squeeze()
    return np.dstack([rgb_u8, a_u8])


def pack_rmaos(
    rough_u8: np.ndarray, metal_u8: np.ndarray, ao_u8: np.ndarray
) -> np.ndarray:
    """
    RMAOS:
      R = Roughness (linear)
      G = Metalness
      B = Ambient Occlusion (mild boost: ^0.75)
      A = 255
    """
    ao = ao_u8.astype(np.float32) / 255.0
    ao_boost = np.power(ao, 0.75)
    ao_u8_boost = _to_u8(ao_boost * 255.0)
    a = np.full_like(rough_u8, 255, np.uint8)
    return np.dstack(
        [
            rough_u8.astype(np.uint8),
            metal_u8.astype(np.uint8),
            ao_u8_boost.astype(np.uint8),
            a,
        ]
    )


# ============================================================
# Preview helpers (new)
# ============================================================


def _get_or_create_preview_image() -> bpy.types.Image:
    img = bpy.data.images.get(PREVIEW_IMG_NAME)
    if img is None:
        img = bpy.data.images.new(
            PREVIEW_IMG_NAME,
            width=PREVIEW_SIZE,
            height=PREVIEW_SIZE,
            alpha=False,
            float_buffer=False,
        )
        img.generated_type = "BLANK"
        # img.type = 'IMAGE'
        img.use_fake_user = True
    else:
        if img.size[0] != PREVIEW_SIZE or img.size[1] != PREVIEW_SIZE:
            img.scale(PREVIEW_SIZE, PREVIEW_SIZE)
    # ensure we also have a texture datablock using it
    tex = bpy.data.textures.get(PREVIEW_IMG_NAME)
    if tex is None:
        tex = bpy.data.textures.new(PREVIEW_IMG_NAME, type="IMAGE")
        tex.image = img
    elif tex.image != img:
        tex.image = img
    return tex  # <---- return texture, not image


def _update_preview_from_array(gray_u8: np.ndarray):
    """Write a grayscale uint8 image into the preview datablock as RGB."""
    tex = _get_or_create_preview_image()  # returns Texture now
    img = tex.image  # get the underlying Image datablock
    h, w = gray_u8.shape

    # resize to preview size with PIL for quality
    pil = Image.fromarray(gray_u8, mode="L").resize(
        (PREVIEW_SIZE, PREVIEW_SIZE), resample=Image.BILINEAR
    )
    rgb = np.asarray(pil.convert("RGB"), dtype=np.float32) / 255.0

    # convert to RGBA
    rgba = np.concatenate(
        [rgb, np.ones((PREVIEW_SIZE, PREVIEW_SIZE, 1), dtype=np.float32)], axis=2
    )
    img.pixels = rgba.flatten()
    img.update()


def update_glow_preview(context=None):
    """Regenerate the square glow/opacity preview based on current settings and source image."""
    try:
        if hasattr(bpy.context, "scene") and hasattr(context, "scene"):
            s = bpy.context.scene.pbrgen if context is None else context.scene.pbrgen
        else:
            return

        src_path = bpy.path.abspath(s.source_image) if s and s.source_image else None
        if not src_path or not os.path.isfile(src_path):
            # fallback neutral gradient if no source
            grad = np.tile(
                np.linspace(0, 255, PREVIEW_SIZE, dtype=np.uint8), (PREVIEW_SIZE, 1)
            )
            _update_preview_from_array(grad)
            return

        # --- Load with alpha preserved ---
        img = Image.open(src_path).convert("RGBA")
        img.thumbnail((512, 512), resample=Image.BILINEAR)
        src_rgba = np.asarray(img, dtype=np.uint8)

        # --- Split channels ---
        src_rgb = src_rgba[..., :3]
        alpha = src_rgba[..., 3].astype(np.float32) / 255.0

        # --- Compute emissive/opacity mask ---
        mask = (
            make_emissive(
                src_rgb,
                threshold=float(s.glow_threshold),
                strength=float(s.glow_intensity),
                binary=bool(s.binary_glow),
            ).astype(np.float32)
            / 255.0
        )

        # --- Respect alpha transparency ---
        mask *= np.power(
            alpha, 0.9
        )  # 0.9 gives smoother soft edges; use 1.0 for strict cutout
        mask = _to_u8(mask * 255.0)

        # --- Ensure grayscale before updating preview ---
        if mask.ndim == 3:
            mask = to_gray_u8(mask)

        _update_preview_from_array(mask)
        force_ui_redraw(context)
    except Exception:
        print("[PBRGen] WARN: update_glow_preview failed:\n", traceback.format_exc())


# ============================================================
# Properties (UI)
# ============================================================


def on_source_changed(self, context):
    # Do nothing — prevent auto-picking the first basecolor
    pass


class PBRGEN_Props(PropertyGroup):

    # Folder-based input for all generators
    source_folder: StringProperty(
        name="Source Folder",
        subtype="DIR_PATH",
        description="Select the folder containing your textures",
        update=on_source_changed,
    )

    # ---------------- I/O ----------------
    def _on_source_changed(self, context):
        update_glow_preview(context)

    source_image: StringProperty(
        name="Source (JPG/PNG/DDS)",
        subtype="FILE_PATH",
        description="Choose a base/diffuse texture to derive PBR maps from",
        update=_on_source_changed,
    )
    output_dir: StringProperty(
        name="Output Folder (optional)",
        subtype="DIR_PATH",
        description="If blank, outputs are saved next to the source image",
        default="",
    )

    # ------------- Base Map Generation -------------
    height_contrast: FloatProperty(
        name="Height Contrast",
        min=0.0,
        max=5.0,
        default=1.0,
        description="Depth extraction from the diffuse; affects parallax (_p) and normal detail",
    )
    normal_strength: FloatProperty(
        name="Normal Strength",
        min=0.0,
        max=10.0,
        default=2.0,
        description="Scales the resulting normal map intensity derived from height",
    )
    roughness_mode: EnumProperty(
        name="Roughness Mode",
        items=[
            (
                "VAR",
                "Variance (recommended)",
                "Analyze local variation for believable micro-gloss",
            ),
            (
                "INV",
                "Invert Luminance",
                "Use inverted brightness as roughness (simple)",
            ),
        ],
        default="VAR",
    )
    roughness_gain: FloatProperty(
        name="Roughness Gain",
        min=0.0,
        max=5.0,
        default=1.2,
        description="Amplifies roughness contrast",
    )
    roughness_radius: FloatProperty(
        name="Roughness Radius (px)",
        min=1.0,
        max=16.0,
        default=3.0,
        description="Neighborhood size for roughness analysis / variance",
    )
    ao_radius: FloatProperty(
        name="AO Radius (px)",
        min=1.0,
        max=32.0,
        default=8.0,
        description="Sample spread for ambient occlusion; larger = broader shadows",
    )
    ao_intensity: FloatProperty(
        name="AO Intensity",
        min=0.0,
        max=5.0,
        default=1.0,
        description="Strength of occlusion darkening during AO derivation",
    )
    metallic_mode: EnumProperty(
        name="Metallic Mode",
        items=[
            ("CONSTANT", "Constant", "Use a single metalness value"),
            ("AUTO", "Auto (threshold)", "Detect bright pixels as metal via threshold"),
        ],
        default="CONSTANT",
    )
    metallic_constant: FloatProperty(
        name="Metallic Value", min=0.0, max=1.0, default=0.0
    )
    metallic_threshold: FloatProperty(
        name="Metal Threshold", min=0.0, max=255.0, default=220.0
    )

    # ------------- Extra Outputs -------------
    include_glow: BoolProperty(
        name="Generate Glow / Opacity",
        default=False,
        description="Create emissive/glow map (Skyrim/PNG) or opacity mask (SpeedTree)",
        update=lambda self, ctx: update_glow_preview(ctx),
    )
    glow_threshold: FloatProperty(
        name="Glow Threshold",
        min=0.0,
        max=255.0,
        default=100.0,
        description="Luma threshold (0-255) for emissive/opacity selection",
        update=lambda self, ctx: update_glow_preview(ctx),
    )
    glow_intensity: FloatProperty(
        name="Glow Intensity",
        min=0.0,
        max=5.0,
        default=1.0,
        description="Multiplier for smooth emissive strength (ignored in binary mode)",
        update=lambda self, ctx: update_glow_preview(ctx),
    )
    binary_glow: BoolProperty(
        name="Binary Opacity / Glowmap",
        description="If enabled, applies a hard threshold for cutout-style glow/opacity; if off, retains smooth, variated glow",
        default=False,
        update=lambda self, ctx: update_glow_preview(ctx),
    )

    include_complex_mask: BoolProperty(
        name="Complex Mask (_m)",
        default=False,
        description="R=metal, G=spec/env mask (height & 1-AO), B=AO, A=1",
    )
    include_spec_map_s: BoolProperty(
        name="Spec/Subsurface Map (_s)",
        default=False,
        description="_s: RGB tint (preset-tuned), A: gloss",
    )
    s_mode: EnumProperty(
        name="`_s` Preset",
        items=[
            ("DEFAULT", "Default", "Neutral tint; gloss from roughness"),
            ("LEAF", "Leaf/Foliage", "Greener tint, softer gloss"),
            ("SKIN", "Skin", "Warm tint, soft gloss"),
            ("ARMOR", "Armor/Metal", "Neutral/cool tint, sharper gloss"),
        ],
        default="DEFAULT",
    )

    # ------------- Material Look Tweaks -------------
    rough_intensity: FloatProperty(
        name="Rough Strength", min=0.0, max=2.0, default=1.0, subtype="FACTOR"
    )
    metal_strength: FloatProperty(
        name="Metal Strength", min=0.0, max=2.0, default=1.0, subtype="FACTOR"
    )
    ao_contrast: FloatProperty(
        name="AO Contrast", min=0.5, max=2.0, default=1.0, subtype="FACTOR"
    )
    height_strength: FloatProperty(
        name="Height Strength", min=0.0, max=2.0, default=1.0, subtype="FACTOR"
    )

    # ------------- Export Targets / Modes & UI Toggles -------------
    export_target: EnumProperty(
        name="Export Target",
        items=[
            (
                "SKYRIM",
                "Skyrim (DDS)",
                "Export full PBR set with packed RMAOS maps (DDS)",
            ),
            (
                "PNG",
                "Authoring (PNG)",
                "Export authoring-friendly PNGs using modern PBR naming",
            ),
            (
                "SPEEDTREE",
                "SpeedTree (PNG)",
                "Export PNGs renamed and filtered for SpeedTree authoring",
            ),
        ],
        default="SKYRIM",
        update=lambda self, ctx: update_glow_preview(ctx),
    )
    export_mode: EnumProperty(
        name="Export Mode",
        items=[
            ("FULL", "Full PBR", "Generate RMAOS (and optional extras)"),
            (
                "BOTH",
                "RMAOS + Separates",
                "Generate both packed RMAOS and separate Rough/Metal/AO maps",
            ),
            (
                "SEPARATES",
                "Separates Only",
                "Skip RMAOS and export only Rough/Metal/AO maps",
            ),
        ],
        default="FULL",
    )
    show_base_gen: BoolProperty(name="Show Base Map Generation", default=False)
    show_tweaks: BoolProperty(name="Show Material Tweaks", default=False)

    # ------------- DDS export -------------
    dxgi_format: EnumProperty(
        name="DDS Format",
        items=[
            ("BC7_UNORM", "BC7 (best)", "High quality (recommended)"),
            ("BC3_UNORM", "BC3/DXT5", "Good quality + alpha"),
            ("BC1_UNORM", "BC1/DXT1", "Smallest, no alpha"),
        ],
        default="BC7_UNORM",
    )
    make_mipmaps: BoolProperty(name="Generate Mipmaps", default=True)

    # ------------- Presets -------------
    material_preset: EnumProperty(
        name="Material Preset",
        items=[
            ("DEFAULT", "Neutral Default", "General-purpose"),
            ("ARMOR", "Armor / Metal", "Semi-matte metals"),
            (
                "REFLECTIVE_METAL",
                "Reflective Metal / Silverware",
                "Highly polished metals",
            ),
            ("WOOD", "Wood", "Boards, planks, beams"),
            ("STONE", "Stone / Brick", "Masonry, rocks"),
            ("CLOTH", "Cloth / Fabric", "Wool, linen, canvas"),
            ("LEATHER", "Leather", "Tanned leather goods"),
            ("SKIN", "Skin / Organic", "Face, body, organic surfaces"),
            ("LEAF", "Leaf / Foliage", "Leaves, plants"),
            ("GLASS", "Glass / Ice", "Shiny non-metals"),
        ],
        default="DEFAULT",
    )

    save_separate_rma: BoolProperty(
        name="Also Save Separate Rough/Metal/AO", default=False
    )


# ============================================================
# Operators
# ============================================================
class PBRGEN_OT_apply_preset(Operator):
    bl_idname = "pbrgen.apply_preset"
    bl_label = "Apply Preset"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.pbrgen
        p = s.material_preset

        # defaults
        s.height_contrast = 1.0
        s.normal_strength = 2.0
        s.roughness_mode = "VAR"
        s.roughness_gain = 1.2
        s.roughness_radius = 3.0
        s.ao_radius = 8.0
        s.ao_intensity = 1.0
        s.metallic_mode = "CONSTANT"
        s.metallic_constant = 0.0
        s.metallic_threshold = 220.0
        s.rough_intensity = 1.0
        s.metal_strength = 1.0
        s.ao_contrast = 1.0
        s.height_strength = 1.0
        s.s_mode = "DEFAULT"

        if p == "ARMOR":
            s.rough_intensity = 0.4
            s.metal_strength = 1.8
            s.ao_contrast = 1.2
            s.s_mode = "ARMOR"
        elif p == "REFLECTIVE_METAL":
            s.normal_strength = 1.2
            s.roughness_gain = 0.8
            s.roughness_radius = 2.0
            s.ao_radius = 3.0
            s.ao_intensity = 0.9
            s.metallic_constant = 1.0
            s.rough_intensity = 0.3
            s.metal_strength = 2.0
            s.ao_contrast = 1.1
            s.height_strength = 0.7
            s.s_mode = "ARMOR"
        elif p == "WOOD":
            s.height_contrast = 1.2
            s.roughness_gain = 1.4
            s.roughness_radius = 4.0
            s.ao_radius = 8.0
            s.ao_intensity = 1.2
            s.rough_intensity = 1.3
        elif p == "STONE":
            s.height_contrast = 1.4
            s.normal_strength = 2.5
            s.ao_radius = 10.0
            s.ao_intensity = 1.4
            s.ao_contrast = 1.25
            s.height_strength = 1.2
        elif p == "CLOTH":
            s.normal_strength = 1.5
            s.roughness_gain = 1.5
            s.roughness_radius = 5.0
            s.ao_radius = 6.0
            s.ao_intensity = 0.9
            s.rough_intensity = 1.6
            s.ao_contrast = 0.9
        elif p == "LEATHER":
            s.roughness_gain = 1.3
            s.ao_radius = 6.0
            s.ao_intensity = 1.1
            s.rough_intensity = 1.2
        elif p == "SKIN":
            s.height_contrast = 0.8
            s.normal_strength = 1.8
            s.ao_radius = 4.0
            s.ao_intensity = 0.8
            s.rough_intensity = 0.8
            s.ao_contrast = 0.9
            s.height_strength = 0.9
            s.s_mode = "SKIN"
        elif p == "LEAF":
            s.height_contrast = 1.2
            s.normal_strength = 1.8
            s.roughness_gain = 0.9
            s.roughness_radius = 3.0
            s.ao_radius = 12.0
            s.ao_intensity = 0.8
            s.rough_intensity = 1.2
            s.ao_contrast = 0.8
            s.height_strength = 1.2
            s.s_mode = "LEAF"
        elif p == "GLASS":
            s.height_contrast = 0.8
            s.normal_strength = 2.0
            s.roughness_gain = 0.8
            s.roughness_radius = 2.0
            s.ao_radius = 2.0
            s.ao_intensity = 0.8
            s.rough_intensity = 0.5
            s.height_strength = 0.9
            s.s_mode = "ARMOR"

        # refresh preview to reflect preset changes
        update_glow_preview(context)
        self.report({"INFO"}, f"Applied preset: {p}")
        return {"FINISHED"}


class PBRGEN_OT_info(Operator):
    bl_idname = "pbrgen.show_info"
    bl_label = "Skyrim PBR Map Guide"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=False)
        col.label(text="Skyrim PBR Map Quick Guide")
        col.separator()
        col.label(text="_rmaos: R=Roughness, G=Metalness, B=Ambient Occlusion, A=1")
        col.label(text="_m    : R=Metal, G=Spec/Env Mask (height & 1-AO), B=AO, A=1")
        col.label(text="_s    : RGB=Spec/Subsurface Tint, A=Gloss (1-Rough)^exp")
        col.label(text="_g    : Emissive/Glow mask (optional)")
        col.separator()
        col.label(text="Presets set BOTH generation and look-tweak defaults.")

    def execute(self, context):
        return {"FINISHED"}


class PBRGEN_OT_generate(Operator):
    bl_idname = "pbrgen.generate_skyrim"
    bl_label = "Generate Skyrim PBR Set"

    export_folder: bpy.props.StringProperty(
        name="Export Folder",
        description="Where to save the generated PBR textures",
        subtype="DIR_PATH",
    )

    def execute(self, context):
        s = context.scene.pbrgen
        wm = context.window_manager

        # progress
        total = (
            10
            + (1 if s.include_complex_mask else 0)
            + (1 if s.include_spec_map_s else 0)
            + (1 if s.include_glow else 0)
            + (3 if s.save_separate_rma else 0)
        )
        step = 0

        def tick():
            nonlocal step
            step += 1
            wm.progress_update(step / max(total, 1))

        wm.progress_begin(0, 1)
        try:
            # paths
            src_path = bpy.path.abspath(s.source_image)
            if not os.path.isfile(src_path):
                self.report({"ERROR"}, "Source image not found.")
                wm.progress_end()
                return {"CANCELLED"}
            out_dir = bpy.path.abspath(s.output_dir).strip() if s.output_dir else ""
            base = os.path.splitext(os.path.basename(src_path))[0]

            # load source
            img = Image.open(src_path).convert("RGBA")
            src_rgba = np.array(img, dtype=np.uint8)
            src_rgb = src_rgba[..., :3]
            gray = to_gray_u8(src_rgb)
            tick()

            # generation
            gray_lin = _srgb_to_linear_u8(gray)  # 0..1
            height_lin = np.clip(gray_lin * s.height_contrast, 0.0, 1.0)
            height_lin = np.clip(height_lin * s.height_strength, 0.0, 1.0)
            height_u8 = _to_u8(height_lin * 255.0)
            tick()

            normal_u8 = sobel_normals_from_height(height_u8, strength=s.normal_strength)
            tick()

            if s.roughness_mode == "VAR":
                rough_base = roughness_from_variance(
                    gray, radius_px=s.roughness_radius, gain=s.roughness_gain
                )
            else:
                rough_base = _to_u8(
                    (255.0 - gray.astype(np.float32)) * s.roughness_gain
                )
            rough_u8 = _to_u8(
                np.clip(
                    (rough_base.astype(np.float32) / 255.0) * s.rough_intensity,
                    0.0,
                    1.0,
                )
                * 255.0
            )
            tick()

            ao_base = ao_from_convexity(
                height_u8, radius_px=int(round(s.ao_radius)), intensity=s.ao_intensity
            )
            ao_lin = np.clip(
                (ao_base.astype(np.float32) / 255.0) ** s.ao_contrast, 0.0, 1.0
            )
            ao_u8 = _to_u8(ao_lin * 255.0)
            tick()

            if s.metallic_mode == "CONSTANT":
                metal_base = _to_u8(
                    np.full_like(gray, int(round(s.metallic_constant * 255.0)))
                )
            else:
                thr = int(round(s.metallic_threshold))
                metal_base = np.where(gray >= thr, 255, 0).astype(np.uint8)
            metal_lin = np.clip(
                (metal_base.astype(np.float32) / 255.0) * s.metal_strength, 0.0, 1.0
            )
            metal_u8 = _to_u8(metal_lin * 255.0)
            tick()

            rmaos = pack_rmaos(rough_u8, metal_u8, ao_u8)
            tick()

            # optional _m
            if s.include_complex_mask:
                try:
                    m_mask = make_complex_mask_m(metal_u8, ao_u8, height_u8)
                except Exception:
                    print("[PBRGen] WARN: _m build failed:\n", traceback.format_exc())
                    m_mask = None
            else:
                m_mask = None
                tick()

            # optional _s
            if s.include_spec_map_s:
                try:
                    s_map = make_s_map(src_rgb, rough_u8, mode=s.s_mode)
                except Exception:
                    print("[PBRGen] WARN: _s build failed:\n", traceback.format_exc())
                    s_map = None
            else:
                s_map = None
                tick()

            # optional glow/opacity — UNIVERSAL (binary toggle)
            glow_u8 = None
            if s.include_glow:
                try:
                    glow_u8 = make_emissive(
                        src_rgb,
                        threshold=s.glow_threshold,
                        strength=s.glow_intensity,
                        binary=s.binary_glow,
                    )
                    # Apply source alpha to match preview
                    try:
                        if src_rgba.shape[-1] == 4:
                            _a = src_rgba[..., 3].astype(np.float32) / 255.0
                            _m = glow_u8.astype(np.float32) / 255.0
                            _m *= np.power(_a, 0.9)
                            glow_u8 = (_m * 255.0).astype(np.uint8)
                    except Exception:
                        pass
                except Exception:
                    print(
                        "[PBRGen] WARN: glow/opacity build failed:\n",
                        traceback.format_exc(),
                    )
                    glow_u8 = None
            tick()

            # Edge bleed for PNG/SpeedTree (use alpha from source if present; else from glow if available)
            target = getattr(s, "export_target", "SKYRIM")
            if target in {"PNG", "SPEEDTREE"}:
                # choose alpha for bleeding
                bleed_rgba = src_rgba.copy()
                alpha = bleed_rgba[..., 3]
                if (alpha == 255).all() and glow_u8 is not None:
                    # synthesize alpha from glow
                    a = glow_u8
                    if a.ndim == 2:
                        a = a
                    else:
                        a = to_gray_u8(a)
                    bleed_rgba[..., 3] = a
                src_rgba = _alpha_bleed_under_rgba(bleed_rgba, iterations=12)

            # Subsurface% grayscale (from s_map RGB if available)
            subsurface_percent = None
            if s.include_spec_map_s and s_map is not None:
                subsurface_percent = to_gray_u8(s_map[..., :3])

            # --- save outputs (profile-aware) ---
            mode = getattr(s, "export_mode", "FULL")

            def save_named(
                img_arr,
                suffix_default,
                suffix_png=None,
                suffix_st=None,
                allow_skyrim_empty=False,
            ):
                if target == "SKYRIM":
                    suffix = (
                        ""
                        if (
                            allow_skyrim_empty and (suffix_default in ("", "basecolor"))
                        )
                        else suffix_default
                    )
                elif target == "PNG":
                    suffix = suffix_png if suffix_png is not None else suffix_default
                elif target == "SPEEDTREE":
                    suffix = suffix_st if suffix_st is not None else suffix_default
                else:
                    suffix = suffix_default
                return save_output(img_arr, src_path, out_dir, base, suffix)

            export_rmaos = (target in {"SKYRIM", "PNG"}) and (mode in {"FULL", "BOTH"})
            export_separates = (mode in {"BOTH", "SEPARATES"}) or (
                target == "SPEEDTREE"
            )

            # Base color
            if target == "SKYRIM":
                save_named(src_rgba, "", None, None, allow_skyrim_empty=True)
            elif target == "PNG":
                save_named(src_rgba, "", "BaseColor", None)
            else:
                save_named(src_rgba, "", None, "Color")

            # Normal
            if target == "SPEEDTREE":
                save_named(normal_u8, "n", None, "Normal")
            elif target == "PNG":
                save_named(normal_u8, "n", "Normal", None)
            else:
                save_named(normal_u8, "n", None, None)

            # Height / Parallax
            if target == "SPEEDTREE":
                save_named(height_u8, "p", None, "Height")
            elif target == "PNG":
                save_named(height_u8, "p", "Height", None)
            else:
                save_named(height_u8, "p", None, None)

            # RMAOS (skip for SpeedTree)
            if export_rmaos:
                if target == "PNG":
                    save_named(rmaos, "rmaos", "RMAOS", None)
                else:
                    save_named(rmaos, "rmaos", None, None)

            # Separates
            if export_separates:
                if target == "SPEEDTREE":
                    gloss = _to_u8(
                        255 - rough_u8.astype(np.uint8)
                    )  # invert roughness -> gloss
                    save_named(gloss, "rough", None, "Gloss")
                    save_named(metal_u8, "metal", None, "Metallic")
                    save_named(ao_u8, "ao", None, "AO")
                elif target == "PNG":
                    save_named(rough_u8, "rough", "Roughness", None)
                    save_named(metal_u8, "metal", "Metallic", None)
                    save_named(ao_u8, "ao", "AO", None)
                else:
                    save_named(rough_u8, "rough", None, None)
                    save_named(metal_u8, "metal", None, None)
                    save_named(ao_u8, "ao", None, None)

            # _m (skip for SpeedTree)
            if s.include_complex_mask and m_mask is not None and target != "SPEEDTREE":
                save_named(m_mask, "m", "M", None)

            # _s + Subsurface%
            if s_map is not None:
                if target == "SPEEDTREE":
                    save_named(s_map, "s", None, "Subsurface")
                elif target == "PNG":
                    save_named(s_map, "s", "Subsurface", None)
                else:
                    save_named(s_map, "s", None, None)

            if subsurface_percent is not None:
                # Always write Subsurface% companion map
                if target == "SPEEDTREE":
                    save_named(subsurface_percent, "s_pct", None, "SubsurfacePercent")
                elif target == "PNG":
                    save_named(subsurface_percent, "s_pct", "SubsurfacePercent", None)
                else:
                    save_named(subsurface_percent, "s_pct", None, None)

            # Glow / Emissive / Opacity (universal logic w/ Binary toggle)
            if glow_u8 is not None:
                if target == "SPEEDTREE":
                    # Use as Opacity (binary recommended)
                    save_named(glow_u8, "g", None, "Opacity")
                elif target == "PNG":
                    save_named(glow_u8, "g", "Emissive", None)
                else:
                    save_named(glow_u8, "g", None, None)

            # Legacy debug separates (not in SpeedTree)
            if s.save_separate_rma and target != "SPEEDTREE":
                save_named(
                    rough_u8, "rough", "Roughness" if target == "PNG" else None, None
                )
                save_named(
                    metal_u8, "metal", "Metallic" if target == "PNG" else None, None
                )
                save_named(ao_u8, "ao", "AO" if target == "PNG" else None, None)

            wm.progress_end()
            # final refresh of preview so user sees the last tuned mask without needing to tweak again
            update_glow_preview(context)
            self.report({"INFO"}, "PBR set generated.")
            return {"FINISHED"}

        except Exception as e:
            wm.progress_end()
            print("[PBRGen] ERROR:\n", traceback.format_exc())
            self.report({"ERROR"}, f"Generation failed: {e}")
            return {"CANCELLED"}


# ============================================================
# UI Panel
# ============================================================
class PBRGEN_PT_panel(Panel):
    bl_label = "PBR Generator (Skyrim)"
    bl_idname = "PBRGEN_PT_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "PBRGen"

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type in {"NODE_EDITOR", "IMAGE_EDITOR"}

    def draw(self, context):
        s = context.scene.pbrgen
        layout = self.layout

        # Header row with preset + info
        row = layout.row(align=True)
        row.prop(s, "material_preset", text="Preset")
        row.operator("pbrgen.apply_preset", text="Apply")
        row.operator("pbrgen.show_info", text="")

        layout.separator()

        # Show file picker for Skyrim/PNG, folder picker for SpeedTree
        col = layout.column(align=False)

        if s.export_target == "SPEEDTREE":
            col.prop(s, "source_folder", text="Source Folder")
        else:
            col.prop(s, "source_image", text="Source Image")

        col.prop(s, "output_dir", text="Output Folder")

        layout.separator()

        # Base Map Generation (collapsible)
        box = layout.box()
        row = box.row(align=True)
        icon = "TRIA_DOWN" if s.show_base_gen else "TRIA_RIGHT"
        row.prop(s, "show_base_gen", text="", icon=icon, emboss=False)
        row.label(text="Base Map Generation")
        if s.show_base_gen:
            col = box.column(align=False)
            col.prop(s, "height_contrast")
            col.prop(s, "normal_strength")
            col.separator()
            col.prop(s, "roughness_mode", expand=True)
            col.prop(s, "roughness_gain")
            col.prop(s, "roughness_radius")
            col.separator()
            col.prop(s, "ao_radius")
            col.prop(s, "ao_intensity")
            col.separator()
            col.prop(s, "metallic_mode", expand=True)
            if s.metallic_mode == "CONSTANT":
                col.prop(s, "metallic_constant")
            else:
                col.prop(s, "metallic_threshold")

        layout.separator()

        # Material Look Tweaks (collapsible)
        box = layout.box()
        row = box.row(align=True)
        icon = "TRIA_DOWN" if s.show_tweaks else "TRIA_RIGHT"
        row.prop(s, "show_tweaks", text="", icon=icon, emboss=False)
        row.label(text="Material Look Tweaks (RMAOS / Height)")
        if s.show_tweaks:
            col = box.column(align=False)
            col.prop(s, "rough_intensity")
            col.prop(s, "metal_strength")
            col.prop(s, "ao_contrast")
            col.prop(s, "height_strength")

        layout.separator()

        # Extra Outputs
        box = layout.box()
        box.label(text="Extra Outputs")
        col = box.column(align=False)
        col.prop(s, "include_complex_mask")
        col.prop(s, "include_spec_map_s")
        col.prop(s, "s_mode")
        col.separator()
        col.prop(s, "include_glow")
        if s.include_glow:
            col.prop(s, "glow_threshold")
            col.prop(s, "glow_intensity")
            col.prop(s, "binary_glow")
            # --- NEW: Square full-width preview right under the controls ---
            try:
                tex = _get_or_create_preview_image()
                col.template_preview(tex, show_buttons=False)
                col.label(text="(Resize panel to refresh preview)")

            except Exception:
                col.label(text="(preview unavailable)")
        layout.separator()
        layout.prop(s, "save_separate_rma")
        layout.separator()

        # Export Options
        box = layout.box()
        box.label(text="Export Options")
        col = box.column(align=False)
        col.prop(s, "export_target", text="Target")
        row = box.row(align=True)
        if s.export_target == "SPEEDTREE":
            row.enabled = False
        row.prop(s, "export_mode", text="Mode")

        layout.separator()

        # DDS Export Settings (greyed when not Skyrim)
        box = layout.box()
        box.label(text="DDS Export Settings")
        row = box.row(align=True)
        row.prop(s, "dxgi_format", text="")
        row.prop(s, "make_mipmaps")
        if s.export_target != "SKYRIM":
            box.enabled = False

        layout.operator("pbrgen.generate_skyrim")
        layout.operator("pbrgen.pack_speedtree", text="Pack SpeedTree Textures → RMAOS")


# ============================================================
# Registration

# --- Import SpeedTree operator module safely ---
import importlib

try:
    from . import pbrgen_speedtree

    importlib.reload(pbrgen_speedtree)
    print("[PBRGen] SpeedTree packer module loaded.")
except Exception as e:
    pbrgen_speedtree = None
    print(f"[PBRGen] ⚠️ (SpeedTree packer not loaded): {e}")

# ============================================================
classes = (
    PBRGEN_Props,
    PBRGEN_OT_apply_preset,
    PBRGEN_OT_info,
    PBRGEN_OT_generate,
    pbrgen_speedtree.PBRGEN_OT_pack_speedtree,
    PBRGEN_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.pbrgen = PointerProperty(type=PBRGEN_Props)
    print("[PBRGen] Registered (DDS preferred; PNG fallback if exporter missing).")
    # create initial preview so UI has something to show immediately
    try:
        update_glow_preview(bpy.context)
    except Exception:
        pass


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    if hasattr(bpy.types.Scene, "pbrgen"):
        del bpy.types.Scene.pbrgen
    # remove preview image to avoid stale data between reloads
    img = bpy.data.images.get(PREVIEW_IMG_NAME)
    if img is not None:
        bpy.data.images.remove(img)
    print("[PBRGen] Unregistered.")


if __name__ == "__main__":
    register()
