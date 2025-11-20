# ============================================================
# Skyrim Material Patcher Unified v2.0 (PyNifly + PBR Preservation)
# ============================================================
# Author: babyjaws
# Build Date: 2025-10-08
#
# Description:
#   - Fully integrated PyNifly emissive reader (NIF → Blender)
#   - Preserves emissive values (color & strength) across rebuilds
#   - Automatic NIF linking via texture path substitution
#   - Unified PBR & Vanilla builder logic (auto-detects texture set)
#   - Supports global and per-material parallax strength
#   - _m.dds fallback preserved for displacement/specular masking
#   - Force Build → Force PBR mode for incomplete texture sets
#   - Emission toggle auto-enables when strength > 0
#   - JSON export/import retains emissive + parallax data
#   - Blender 4.5+ compatible (TLS-safe, internal Python 3.11)
#


bl_info = {
    "name": "Nymphs SkyBlend Toolkit",
    "author": "Babyjawz / Nymph Nerds",
    "version": (1, 0, 0),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Sidebar > SkyBlend",
    "description": "Tools for building and patching Skyrim materials, PBR workflows, and emissive fixes.",
    "category": "Material",
}


__version__ = "1.0.0"
SMP_BUILD_TAG = "SMP_v200_unified"


import sys, os

# === Safe PyFFI import (force bundled copy) ===
from .safe_import import local_import
import importlib.util

if "pyffi" in sys.modules:
    del sys.modules["pyffi"]

# Absolute path to your bundled PyFFI
import os

_pyffi_path = os.path.join(
    os.path.dirname(__file__), "thirdparty", "pyffi", "__init__.py"
)

spec = importlib.util.spec_from_file_location("pyffi", _pyffi_path)
pyffi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pyffi)
print(f"[SMP init] Forced import of bundled PyFFI from {pyffi.__file__}")


import bpy, json, shutil, os
from pathlib import Path
from typing import Optional, Dict, Tuple, List

# --- NIF emissive parser (PyFFI-based) ---
from . import nifparser  # skyrim_mat_patcher/nifparser.py
from .pbrnifpatcher_ops import SKPBR_OT_RunPBRNifPatcher

from bpy.props import (
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    BoolProperty,
    EnumProperty,
    PointerProperty,
    CollectionProperty,
)
from bpy_extras.io_utils import ImportHelper

# ----------------------------------------------------------------
#  MO2 Virtual-Filesystem helpers  (used by emissive parser)
# ----------------------------------------------------------------
try:
    from .vfs import vfs_exists as _smp_vfs_exists
    from .vfs import vfs_open as _smp_open
except Exception:
    # Fallbacks so the addon still runs if VFS module not found
    def _smp_vfs_exists(path: str) -> bool:
        import os

        return os.path.exists(path)

    def _smp_open(path: str, mode: str = "rb"):
        return open(path, mode)


# ---------------------------------------------------------------------------
# Multi-edit helpers (alpha/emission sync guard)
# ---------------------------------------------------------------------------
_SMP_PROP_SYNC_GUARD = False


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
def _log(msg: str):
    print(f"[Skyrim PBR] {msg}")


def _get_active_material(ctx):
    ob = getattr(ctx, "active_object", None)
    return getattr(ob, "active_material", None) if ob else None


# ============================================================
# ANCHORING SYSTEM (DO NOT MODIFY!)
# ============================================================
KEY_STICKY_MANUAL = "skpbr_sticky_manual"
KEY_LAST_VFS = "skpbr_last_vfs"
KEY_LAST_MANUAL = "skpbr_last_manual"
KEY_LAST_SIBLING = "skpbr_last_sibling"


def _store_anchor(mat, key: str, path: Path):
    try:
        mat[key] = str(path)
    except Exception as e:
        _log(f"Store anchor fail: {e}")


def _load_anchor(mat, key: str):
    v = mat.get(key)
    try:
        return Path(v) if v else None
    except Exception:
        return None


def _first_image_path_from_material(mat):
    if not mat or not mat.node_tree:
        return None
    for n in mat.node_tree.nodes:
        if isinstance(n, bpy.types.ShaderNodeTexImage) and n.image:
            try:
                return Path(bpy.path.abspath(n.image.filepath))
            except Exception:
                pass
    return None


class SKPBR_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__
    search_mode: EnumProperty(
        name="Texture Search Mode",
        items=[
            ("NIFPATH", "VFS (NIF Path)", "Use MO2’s VFS winning texture (vanilla)."),
            ("MANUAL", "Manual", "Pick a base DDS manually."),
            ("SIBLING", "Mod Folder", "Use siblings of the current image."),
        ],
        default="NIFPATH",
    )
    manual_root: StringProperty(name="Manual Root", subtype="DIR_PATH", default="//")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "search_mode", expand=True)
        if self.search_mode == "MANUAL":
            col.prop(self, "manual_root")


def _apply_nif_emissive_to_bsdf_if_flag(mat):
    """Apply emissive color and multiple from NIF data to the BSDF node."""
    try:
        nt = mat.node_tree
        n_bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if not n_bsdf:
            return

        # Find emissive color and strength from NIF data
        emissive_color = getattr(mat, "emissive_color", None)
        emissive_mult = getattr(mat, "emissive_multiple", None)

        if (
            emissive_color
            and isinstance(emissive_color, (list, tuple))
            and len(emissive_color) >= 3
        ):
            n_bsdf.inputs["Emission"].default_value = (
                emissive_color[0],
                emissive_color[1],
                emissive_color[2],
                1.0,
            )

        # If emissive multiple (strength) exists and > 0
        if emissive_mult is not None:
            try:
                n_bsdf.inputs["Emission Strength"].default_value = emissive_mult
            except Exception:
                pass

        _log(
            f"[NIF] Applied emissive color {emissive_color} × {emissive_mult} to {mat.name}"
        )

    except Exception as e:
        _log(f"[NIF] Failed to apply emissive to {mat.name}: {e}")


def _remember_mode_anchor(mat, prefs, basepath: Path):
    print(
        f"DEBUG: remember_anchor mode={getattr(prefs, 'search_mode', None)} base={basepath}"
    )
    if prefs.search_mode == "MANUAL":
        _store_anchor(mat, KEY_LAST_MANUAL, basepath)
        _store_anchor(mat, KEY_STICKY_MANUAL, basepath)
    elif prefs.search_mode == "NIFPATH":
        _store_anchor(mat, KEY_LAST_VFS, basepath)
    else:
        _store_anchor(mat, KEY_LAST_SIBLING, basepath)


def _choose_anchor_for_mode(mat, prefs):
    mode = prefs.search_mode
    if mode == "MANUAL":
        anchor = (
            _load_anchor(mat, KEY_STICKY_MANUAL)
            or _load_anchor(mat, KEY_LAST_MANUAL)
            or _first_image_path_from_material(mat)
        )
        return anchor
    if mode == "NIFPATH":
        anchor = _load_anchor(mat, KEY_LAST_VFS) or _first_image_path_from_material(mat)
        return anchor
    anchor = _first_image_path_from_material(mat) or _load_anchor(mat, KEY_LAST_SIBLING)
    return anchor


# ============================================================


# ---------------------------------------------------------------------------
# Extra robustness helpers (added without touching the DO-NOT-MODIFY block)
# ---------------------------------------------------------------------------
def _clear_non_vfs_anchors(mat: bpy.types.Material):
    """When switching to VFS, clear manual/sibling anchors to avoid sticky overrides."""
    for k in (KEY_STICKY_MANUAL, KEY_LAST_MANUAL, KEY_LAST_SIBLING):
        if k in mat:
            try:
                del mat[k]
            except Exception:
                pass


def _set_build_status(mat: bpy.types.Material, status: str):
    """Store last build mode for UI, e.g., 'PBR', 'FORCED_PBR', 'NONPBR'."""
    try:
        mat["skpbr_last_build"] = status
    except Exception:
        pass


def _get_build_status(mat: bpy.types.Material) -> str:
    v = mat.get("skpbr_last_build")
    if isinstance(v, str):
        return v
    return ""


# ---------------------------------------------------------------------------
# Labels for nodes
# ---------------------------------------------------------------------------
LBL_BASE, LBL_NORMAL, LBL_RMAOS, LBL_PARALLAX, LBL_EMISSIVE = (
    "Base",
    "Normal",
    "RMAOS",
    "Parallax",
    "Emissive",
)
LBL_SEP, LBL_NORM_MAP, LBL_ROUGH_CTL, LBL_METAL_CTL = (
    "RMAOS Separate",
    "Normal Map",
    "Roughness Control",
    "Metallic Control",
)
LBL_ROUGH_INV, LBL_BSDF, LBL_DISP, LBL_OUT, LBL_EM_COLOR = (
    "Roughness Invert",
    "Principled BSDF",
    "Displacement",
    "Material Output",
    "Emissive Color (fallback)",
)
LBL_AO_MIX, LBL_AO_STRENGTH = "AO Mix (Multiply)", "AO Strength (fac)"
LBL_SPEC, LBL_PARALLAX_M = "Specular", "Parallax (_m)"
LBL_EM_TINT = "Emissive Tint (Multiply)"
LBL_EM_STRENGTH = "Emission Strength"  # ← add this line


# ---------------------------------------------------------------------------
# Property Group (live controls)
# ---------------------------------------------------------------------------
def _update_math_input(mat, label, idx, value):
    if not mat or not mat.node_tree:
        return
    for n in mat.node_tree.nodes:
        if getattr(n, "label", "") == label:
            try:
                n.inputs[idx].default_value = value
            except Exception:
                pass
            break


def _update_socket(mat, label, name, value):
    if not mat or not mat.node_tree:
        return
    for n in mat.node_tree.nodes:
        if getattr(n, "label", "") == label and name in n.inputs:
            try:
                n.inputs[name].default_value = value
            except Exception:
                pass
            break


def _update_rgb(mat, label, value):
    if not mat or not mat.node_tree:
        return
    for n in mat.node_tree.nodes:
        if getattr(n, "label", "") == label:
            try:
                n.outputs[0].default_value = value
            except Exception:
                pass
            break


def _update_flip_norm(self, ctx):
    mat = self.id_data
    if not mat or not mat.node_tree:
        return
    for n in mat.node_tree.nodes:
        if getattr(n, "label", "") == "Normal Flip Switch":
            n.inputs["Fac"].default_value = 1.0 if self.flip_norm_y else 0.0


class SKPBR_PG_Settings(bpy.types.PropertyGroup):
    def _sync_pg_parallax(self, context):
        if hasattr(self, "pg_use_parallax_m"):
            self.pg_use_parallax_m = self.use_parallax_m
        return None

    def _v205_update_emission(self, context):
        mat = getattr(self, "id_data", None)
        if not mat:
            return
        try:
            _v205_emission_ensure_chain(mat, force_on=self.emission_on)
            _v205_emission_live(mat)
        except Exception as e:
            print("[SMP v205] live emission update failed:", e)

    def _smp_update_emission_v202(self, context):
        mat = getattr(self, "id_data", None)
        if not mat:
            return
        try:
            _smp_ensure_emission_chain_optionA(mat, force_on=self.emission_on)
            _smp_apply_emission_live_optionA(mat)
        except Exception as e:
            print("[SMP_v202] emission live update error:", e)

    def _smp_update_emission(self, context):
        mat = getattr(self, "id_data", None)
        if not mat:
            return
        try:
            _ensure_emissive_chain(mat)
        except Exception:
            pass
        try:
            _emissive_apply_to_nodes(mat)
        except Exception:
            pass

    # --- Emissive controls (smart toggle) ---
    def _update_emission_strength(self, context):
        try:
            _emissive_apply_to_nodes(self.id_data)
        except Exception:
            pass
        try:
            self.emission_on = bool(self.emission_strength > 0.0)
        except Exception:
            pass

    def _update_emission_on(self, context):
        try:
            _emissive_apply_to_nodes(self.id_data)
        except Exception:
            pass
        try:
            if self.emission_on and self.emission_strength <= 0.0:
                self.emission_strength = 1.0
        except Exception:
            pass

    emission_on: bpy.props.BoolProperty(
        name="Emission",
        description="Enable emissive glow for this material",
        default=False,
        update=_update_emission_on,
    )

    emission_color: bpy.props.FloatVectorProperty(
        name="Emission Color",
        subtype="COLOR",
        default=(1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        description="Emissive color tint (from NIF or manual)",
        update=_smp_update_emission,
    )

    emission_strength: bpy.props.FloatProperty(
        name="Emission Strength",
        default=0.0,
        min=0.0,
        max=10.0,
        update=_update_emission_strength,
        description="Brightness of emissive glow; >0 auto-enables emission",
    )
    rough_mult: FloatProperty(
        name="Roughness Mult",
        default=1.0,
        min=0.0,
        soft_max=2.0,
        update=lambda s, c: _update_math_input(
            s.id_data, LBL_ROUGH_CTL, 1, s.rough_mult
        ),
    )
    invert_roughness: BoolProperty(
        name="Invert Roughness (Gloss Mode)",
        default=False,
        description="If your red channel stores gloss (smoothness), turn this ON to invert into roughness (1-R).",
        update=lambda s, c: _update_socket(
            s.id_data, LBL_ROUGH_INV, "Value", 1.0 if s.invert_roughness else 0.0
        ),
    )
    metal_mult: FloatProperty(
        name="Metallic Mult",
        default=1.0,
        min=0.0,
        soft_max=2.0,
        update=lambda s, c: _update_math_input(
            s.id_data, LBL_METAL_CTL, 1, s.metal_mult
        ),
    )
    normal_strength: FloatProperty(
        name="Normal Strength",
        default=1.0,
        min=0.0,
        soft_max=5.0,
        update=lambda s, c: _update_socket(
            s.id_data, LBL_NORM_MAP, "Strength", s.normal_strength
        ),
    )
    disp_scale: FloatProperty(
        name="Displacement Scale",
        default=0.05,
        min=0.0,
        soft_max=1.0,
        update=lambda s, c: _update_socket(s.id_data, LBL_DISP, "Scale", s.disp_scale),
    )
    disp_mid: FloatProperty(
        name="Displacement Midlevel",
        default=0.5,
        min=0.0,
        max=1.0,
        update=lambda s, c: _update_socket(s.id_data, LBL_DISP, "Midlevel", s.disp_mid),
    )

    alpha_strength: bpy.props.FloatProperty(
        name="Alpha Strength",
        description="Transparency intensity for export and preview (0 = invisible, 1 = opaque)",
        default=1.0,
        min=0.0,
        max=1.0,
        update=lambda s, c: _on_alpha_strength_changed(s),
    )

    parallax_default_strength: FloatProperty(
        name="Default Parallax Strength",
        default=0.02,
        min=0.0,
        soft_max=0.1,
        description="Used when NIF value is missing; Blender preview uses ×10 of this value.",
    )

    use_parallax_m: BoolProperty(
        name="Use _m as Parallax (ParallaxGen)",
        default=False,
        description=(
            "Auto-detects _m: dummy/height/mask. "
            "If ON and _m is classified as height (and _p missing), it drives displacement. "
            "If OFF, _m alpha may modulate Specular as env mask. "
            "Black dummy _m is ignored."
        ),
    )
    emissive_strength: FloatProperty(
        name="Emission Strength",
        default=0.0,
        min=0.0,
        soft_max=10.0,
        update=lambda s, c: _update_socket(
            s.id_data, LBL_BSDF, "Emission Strength", s.emissive_strength
        ),
    )
    emissive_color: FloatVectorProperty(
        name="Emission Color",
        subtype="COLOR",
        size=4,
        default=(1, 1, 1, 1),
        update=lambda s, c: _update_rgb(s.id_data, LBL_EM_COLOR, s.emissive_color),
    )
    ao_strength: FloatProperty(
        name=LBL_AO_STRENGTH,
        default=1.0,
        min=0.0,
        max=1.0,
        update=lambda s, c: _update_socket(s.id_data, LBL_AO_MIX, "Fac", s.ao_strength),
    )
    flip_norm_y: BoolProperty(
        name="Preview Normal Fix (Flip Y)",
        default=False,
        description="ON = Flipped for Blender viewport (looks correct). OFF = Skyrim DirectX format (export).",
        update=_update_flip_norm,
    )
    force_build: BoolProperty(
        name="Force PBR Build (for incomplete sets)",
        default=False,
        description="Build full PBR even if RMAOS/Emissive are missing. When OFF and VFS is non-PBR, builds vanilla nodes.",
    )


# ---------------------------------------------------------------------------
# Texture resolution helpers
# ---------------------------------------------------------------------------
KNOWN_SUFFIXES = [
    "_d",
    "_n",
    "_rmaos",
    "_p",
    "_e",
    "_em",
    "_g",
    "_orm",
    "_m",
    "_s",
    "_spec",
    "_specular",
]


def _is_diffuse_like(path: Path) -> bool:
    """Heuristic: treat files without known suffix OR ending in _d as diffuse/base."""
    st = path.stem.lower()
    # Accept "name.dds" and "name_d.dds"
    if any(
        st.endswith(suf) for suf in ["_n", "_rmaos", "_orm", "_p", "_e", "_em", "_g"]
    ):
        return False
    return True


def _determine_base_diffuse(anchor: Path, prefs: "SKPBR_Prefs") -> Optional[Path]:
    """Pick the base diffuse to anchor the set. Prefer explicit diffuse files."""
    stem = _strip_known_suffixes(anchor.stem)
    base_dir = anchor.parent
    # If manual root is set in MANUAL mode, search there first
    search_dirs = []
    if prefs.search_mode == "MANUAL" and prefs.manual_root:
        mr = Path(prefs.manual_root)
        if mr.is_dir():
            search_dirs.append(mr)
    # Always include the anchor's directory as a fallback
    if base_dir not in search_dirs:
        search_dirs.append(base_dir)

    candidates = [f"{stem}.dds", f"{stem}_d.dds"]

    for d in search_dirs:
        for nm in candidates:
            p = d / nm
            if p.is_file():
                return p

    # If nothing found, accept the anchor itself if it looks like a diffuse/base
    try:
        if _is_diffuse_like(anchor):
            return anchor
    except Exception:
        pass
    return None


def _gather_strict_set(
    base_diffuse: Path, prefs: "SKPBR_Prefs"
) -> Dict[str, Optional[Path]]:
    """Gather only maps with the same base stem as the diffuse. Search manual root first if set."""
    stem = _strip_known_suffixes(base_diffuse.stem)
    base_dir = base_diffuse.parent

    search_dirs = []
    if prefs.search_mode == "MANUAL" and prefs.manual_root:
        mr = Path(prefs.manual_root)
        if mr.is_dir():
            search_dirs.append(mr)
    if base_dir not in search_dirs:
        search_dirs.append(base_dir)

    wants = {
        "BASE": [f"{stem}.dds", f"{stem}_d.dds"],
        "NORMAL": [f"{stem}_n.dds"],
        "RMAOS": [f"{stem}_rmaos.dds", f"{stem}_orm.dds"],
        "PARALLAX": [f"{stem}_p.dds"],
        "EMISSIVE": [f"{stem}_em.dds", f"{stem}_e.dds", f"{stem}_g.dds"],
    }
    out = {k: None for k in wants}

    # BASE is the provided diffuse, ensure it is set
    out["BASE"] = base_diffuse if base_diffuse.is_file() else None

    for k, names in wants.items():
        if k == "BASE":
            continue
        found = None
        for d in search_dirs:
            for nm in names:
                p = d / nm
                if p.is_file():
                    found = p
                    break
            if found:
                break
        out[k] = found
    return out


def _strip_known_suffixes(stem: str) -> str:
    low = stem.lower()
    for s in KNOWN_SUFFIXES:
        if low.endswith(s):
            return stem[: -len(s)]
    return stem


def _gather_from_dir(base_dir: Path, stem: str) -> Dict[str, Optional[Path]]:
    candidates = {
        "BASE": [f"{stem}.dds", f"{stem}_d.dds"],
        "NORMAL": [f"{stem}_n.dds"],
        "RMAOS": [f"{stem}_rmaos.dds", f"{stem}_orm.dds"],
        "PARALLAX": [f"{stem}_p.dds"],
        "EMISSIVE": [f"{stem}_em.dds", f"{stem}_e.dds", f"{stem}_g.dds"],
    }
    out = {k: None for k in candidates}
    for k, names in candidates.items():
        for nm in names:
            p = base_dir / nm
            if p.is_file():
                out[k] = p
                break
    return out


def _resolve_textures_for_anchor(
    anchor: Path, prefs: "SKPBR_Prefs"
) -> Dict[str, Optional[Path]]:
    """Resolve textures by first determining the base diffuse, then gathering only matching stems."""
    base = _determine_base_diffuse(anchor, prefs)
    if not base:
        # Fallback: use original behavior for BASE only
        # (keeps compatibility if user anchors to a non-diffuse image with no real base on disk)
        base = anchor
    return _gather_strict_set(base, prefs)
    return _gather_from_dir(base_dir, stem)


def _classify_m_map(path: Path) -> str:
    """
    Classify _m.dds as 'dummy' (black), 'mask' (low variance), or 'height' (high detail).
    """
    try:
        import bpy

        img = bpy.data.images.load(str(path), check_existing=True)
    except Exception:
        return "mask"
    try:
        if not getattr(img, "has_data", True):
            img.reload()
        px = img.pixels
        if not px or len(px) < 4:
            return "mask"
        step = max(1, int((len(px) // 4) // 2048))
        samps = [px[i * 4] for i in range(0, (len(px) // 4), step)]
        avg = sum(samps) / len(samps)
        var = sum((v - avg) ** 2 for v in samps) / len(samps)
        if avg < 0.02 and var < 1e-6:
            return "dummy"
        if var < 0.005:
            return "mask"
        return "height"
    except Exception:
        return "mask"
    finally:
        try:
            if img.users == 0:
                bpy.data.images.remove(img)
        except Exception:
            pass


def _detect_pbr(textures: Dict[str, Optional[Path]]) -> bool:
    """Only treat as PBR if a valid RMAOS/ORM map exists with the same stem as the base diffuse."""
    base = textures.get("BASE")
    if not base or not base.is_file():
        return False
    stem = _strip_known_suffixes(base.stem).lower()

    p = textures.get("RMAOS")
    if p and p.is_file() and _strip_known_suffixes(p.stem).lower() == stem:
        return True
    return False
    stem = _strip_known_suffixes(base.stem).lower()

    def valid_map(key):
        p = textures.get(key)
        if not p or not p.is_file():
            return False
        pstem = _strip_known_suffixes(p.stem).lower()
        # Ignore legacy "_m" envmaps
        if p.stem.lower().endswith("_m"):
            return False
        return pstem == stem

    # Require RMAOS or PARALLAX specifically
    if valid_map("RMAOS") or valid_map("PARALLAX"):
        return True
    # Emissive alone shouldn't force PBR
    return False
    stem = _strip_known_suffixes(base.stem).lower()
    for key in ("RMAOS", "PARALLAX", "EMISSIVE"):
        p = textures.get(key)
        if p and _strip_known_suffixes(p.stem).lower() == stem:
            return True
    return False
    return bool(
        textures.get("RMAOS") or textures.get("PARALLAX") or textures.get("EMISSIVE")
    )


# ---------------------------------------------------------------------------
# JSON helpers (overrides + settings import)
# ---------------------------------------------------------------------------
def _apply_json_overrides(
    textures: Dict[str, Optional[Path]], entry: Optional[dict], json_dir: Path
):
    if not entry:
        return
    mapping = {
        "BASE": ["base", "diffuse", "albedo"],
        "NORMAL": ["normal"],
        "RMAOS": ["rmaos", "orm"],
        "PARALLAX": ["parallax", "height"],
        "EMISSIVE": ["emissive", "emission"],
    }
    for K, keys in mapping.items():
        for key in keys:
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                p = Path(v.replace("\\", "/"))
                if not p.is_absolute():
                    p = (json_dir / p).resolve()
                textures[K] = p
                break


def _apply_json_settings(mat: bpy.types.Material, entry: dict):
    if not mat or not hasattr(mat, "skpbr") or not entry:
        return
    s = mat.skpbr
    scalar = (
        "rough_mult",
        "metal_mult",
        "normal_strength",
        "disp_scale",
        "disp_mid",
        "emissive_strength",
        "ao_strength",
        "alpha_strength",
    )
    flags = ("flip_norm_y", "invert_roughness", "force_build")
    for k in scalar:
        if k in entry and isinstance(entry[k], (int, float)):
            try:
                setattr(s, k, float(entry[k]))
            except Exception as e:
                _log(f"JSON setting '{k}' failed: {e}")
    for k in flags:
        if k in entry and isinstance(entry[k], bool):
            try:
                setattr(s, k, bool(entry[k]))
            except Exception as e:
                _log(f"JSON setting '{k}' failed: {e}")
    if (
        "emissive_color" in entry
        and isinstance(entry["emissive_color"], (list, tuple))
        and len(entry["emissive_color"]) >= 3
    ):
        rgba = list(entry["emissive_color"])[:4] + [1.0]
        try:
            s.emissive_color = (
                float(rgba[0]),
                float(rgba[1]),
                float(rgba[2]),
                float(rgba[3]),
            )
        except Exception as e:
            _log(f"JSON emissive_color failed: {e}")


def _extract_base_from_json(entry: Optional[dict], json_dir: Path) -> Optional[Path]:
    if not entry:
        return None
    for key in ("base", "diffuse", "albedo"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            p = Path(v.replace("\\", "/"))
            if not p.is_absolute():
                p = (json_dir / p).resolve()
            return p
    return None


# ============================================================
# Emissive reading + initialization (Skyrim SE/AE)
# ============================================================


def _pynifly_suggests_emissive(obj: bpy.types.Object) -> bool:
    """
    Best-effort fallback when numeric emissives aren't found:
    if the imported NIF (via PyNifly) shows any glow/emissive texture slot,
    we'll enable emission with safe defaults.
    """
    try:
        data = getattr(obj.data, "nif_blocks", None)
        if not data:
            return False
        for blk in data:
            # look for texture arrays commonly exposed by PyNifly
            for attr in ("textures", "tex", "texture_paths"):
                texs = getattr(blk, attr, None)
                if not texs:
                    continue
                for t in texs:
                    if not isinstance(t, str):
                        continue
                    low = t.lower()
                    if (
                        low.endswith("_g.dds")
                        or low.endswith("_em.dds")
                        or low.endswith("_e.dds")
                    ):
                        return True
        return False
    except Exception:
        return False


def _extract_emissive_from_nif_for_object(obj: bpy.types.Object) -> Optional[dict]:
    """
    Resolve a NIF path for this object using:
      1. obj["nif_path"] if present
      2. the SMP 'remember_anchor' path (skpbr_last_vfs, skpbr_last_manual, etc.)
    and load emissive data using the VFS layer if available.
    """
    try:
        nif_path = getattr(obj, "nif_path", None)

        # --- Fallback 1: look in custom props for a .nif path
        if not nif_path:
            for k in getattr(obj, "keys", lambda: [])():
                try:
                    v = str(obj[k])
                except Exception:
                    continue
                if v.lower().endswith(".nif"):
                    nif_path = v
                    break

        # --- Fallback 2: use remembered anchors (VFS-aware)
        if not nif_path:
            mat = None
            if hasattr(obj, "active_material") and obj.active_material:
                mat = obj.active_material
            elif obj.material_slots and obj.material_slots[0].material:
                mat = obj.material_slots[0].material

            if mat:
                for key in (
                    "skpbr_last_vfs",
                    "skpbr_last_manual",
                    "skpbr_last_sibling",
                ):
                    if key in mat:
                        anchor = Path(mat[key])
                        base_dir = anchor.parent

                        # Look for matching nif beside the texture under VFS
                        for guess in [
                            base_dir / f"{obj.name.split(':')[0]}.nif",
                            base_dir.with_name("meshes")
                            / f"{obj.name.split(':')[0]}.nif",
                        ]:
                            if _smp_vfs_exists(str(guess)):
                                nif_path = str(guess)
                                break
                        if nif_path:
                            break

        if not nif_path:
            return None

        # --- Use VFS to read file content
        if _smp_vfs_exists(nif_path):
            with _smp_open(nif_path, "rb") as f:
                data = f.read()
            info = nifparser.parse_nif_emissives(
                data, match_name=getattr(obj, "name", None)
            )
        elif os.path.exists(nif_path):
            info = nifparser.parse_nif_emissives(
                nif_path, match_name=getattr(obj, "name", None)
            )
        else:
            return None

        if not info:
            return None

        col = tuple(info.get("em_color", (0.0, 0.0, 0.0)))
        mul = float(info.get("em_strength", 0.0))
        return {"color": col, "strength": mul}

    except Exception as e:
        _log(f"[SkyrimPatcher DEBUG] VFS emissive extract failed for {obj.name}: {e}")
        return None


def _init_emissive_from_nif(mat: bpy.types.Material):
    """
    Called at the start of build_nodes_unified().
    Finds the first object that uses this material, attempts to read emissive
    color/strength from its source NIF (per-part match by object name), and
    writes values to both the UI (mat.skpbr.*) and the BSDF node.
    Fallback: if no numeric emissive found but a glow map is detected via
    PyNifly's imported data, enable emissive with (1,1,1) × 1.0.
    """
    try:
        # locate a mesh object that uses this material
        user_obj = None
        for ob in bpy.data.objects:
            if ob.type != "MESH":
                continue
            for slot in ob.material_slots:
                if slot.material is mat:
                    user_obj = ob
                    break
            if user_obj:
                break

        if not user_obj or not hasattr(mat, "skpbr"):
            return

        # 1) primary: numeric emissive via PyFFI
        info = _extract_emissive_from_nif_for_object(user_obj)
        if info:
            mat.skpbr.emission_color = info["color"]
            mat.skpbr.emission_strength = info["strength"]
            mat.skpbr.emission_on = info["strength"] > 0.0
            _log(
                f"[SkyrimPatcher DEBUG] Emissive applied from NIF for {mat.name}: "
                f"color={info['color']} × {info['strength']}"
            )
            _apply_nif_emissive_to_bsdf_if_flag(mat)
            # Force panel redraw so UI sliders reflect new NIF emissive
            try:
                import bpy

                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == "PROPERTIES":
                            area.tag_redraw()
            except Exception as e:
                _log(f"[SMP] emissive UI redraw failed: {e}")

            return

        # 2) fallback: flags/texture hint via PyNifly import → white × 1.0
        if _pynifly_suggests_emissive(user_obj):
            mat.skpbr.emission_color = (1.0, 1.0, 1.0)
            mat.skpbr.emission_strength = 1.0
            mat.skpbr.emission_on = True
            _log(
                f"[SkyrimPatcher DEBUG] Emissive fallback (flags/texture hint) for {mat.name}: "
                f"color=(1,1,1) × 1.0"
            )
            _apply_nif_emissive_to_bsdf_if_flag(mat)
            return

        # 3) nothing found → keep emission OFF/0.0
        _log(
            f"[SkyrimPatcher DEBUG] No emissive found for {mat.name}; leaving emission off."
        )

    except Exception as e:
        _log(
            f"[SkyrimPatcher DEBUG] _init_emissive_from_nif failed for {mat.name}: {e}"
        )


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


# --- Emissive chain helpers (Skyrim-accurate) ---
def _find_node_by_label(nt, label):
    for n in nt.nodes:
        if getattr(n, "label", "") == label:
            return n
    return None


def _ensure_emissive_chain(mat):
    # Deprecated — actual emissive chain built inline in build_nodes_unified()
    return


# ============================================================
# Unified Node Builder (PBR + Vanilla)
# ============================================================


def build_nodes_unified(
    mat: bpy.types.Material, textures: Dict[str, Optional[Path]], is_pbr: bool = True
):
    """
    Unified builder combining PBR and Vanilla layouts.
    - Keeps PBR layout and slider hookups as base.
    - In Vanilla mode, skips RMAOS/Metallic/Roughness links.
    - All other behavior identical (alpha, emissive, parallax, UI sync).
    """

    try:
        _init_emissive_from_nif(mat)
    except Exception as e:
        _log(f"[UnifiedBuilder] emissive init failed for {mat.name}: {e}")

    # Preserve emissive values from old PyNifly or Skyrim Shader nodes before clearing
    prev_em_color, prev_em_strength = None, None
    try:
        if mat.node_tree:
            for n in mat.node_tree.nodes:
                if "Skyrim Shader" in n.name:
                    prev_em_color = (
                        tuple(n.inputs.get("Emission Color", None).default_value[:3])
                        if "Emission Color" in n.inputs
                        else None
                    )
                    prev_em_strength = (
                        n.inputs.get("Emission Strength", None).default_value
                        if "Emission Strength" in n.inputs
                        else None
                    )
                    break
                elif n.type == "EMISSION":
                    prev_em_color = tuple(n.inputs["Color"].default_value[:3])
                    prev_em_strength = n.inputs["Strength"].default_value
                    break
    except Exception as e:
        _log(f"[UnifiedBuilder] emissive preservation check failed: {e}")

    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    nodes, links = nt.nodes, nt.links

    _log(f"[UnifiedBuilder] Starting unified build for {mat.name} (is_pbr={is_pbr})")

    n_base = nodes.new("ShaderNodeTexImage")
    n_base.label = LBL_BASE
    n_rma = nodes.new("ShaderNodeTexImage")
    n_rma.label = LBL_RMAOS
    n_norm = nodes.new("ShaderNodeTexImage")
    n_norm.label = LBL_NORMAL
    n_em = nodes.new("ShaderNodeTexImage")
    n_em.label = LBL_EMISSIVE
    n_para = nodes.new("ShaderNodeTexImage")
    n_para.label = LBL_PARALLAX

    _load_image(n_base, textures.get("BASE"), "sRGB")
    _load_image(n_rma, textures.get("RMAOS"), "Non-Color")
    _load_image(n_norm, textures.get("NORMAL"), "Non-Color")
    _load_image(n_em, textures.get("EMISSIVE"), "sRGB")
    _load_image(n_para, textures.get("PARALLAX"), "Non-Color")

    if is_pbr and textures.get("RMAOS"):
        n_sep = nodes.new("ShaderNodeSeparateColor")
        n_sep.label = LBL_SEP
        links.new(n_rma.outputs["Color"], n_sep.inputs["Color"])
        n_inv = nodes.new("ShaderNodeMath")
        n_inv.operation = "SUBTRACT"
        n_inv.inputs[0].default_value = 1.0
        n_inv.label = "Roughness Invert"
        links.new(n_sep.outputs["Red"], n_inv.inputs[1])
        n_rough_switch = nodes.new("ShaderNodeMixRGB")
        n_rough_switch.label = "Mix"
        n_rough_switch.label = "Mix"
        n_rough_inv_val = nodes.new("ShaderNodeValue")
        n_rough_inv_val.label = LBL_ROUGH_INV
        links.new(n_sep.outputs["Red"], n_rough_switch.inputs["Color1"])
        links.new(n_inv.outputs[0], n_rough_switch.inputs["Color2"])
        links.new(n_rough_inv_val.outputs[0], n_rough_switch.inputs["Fac"])
        n_mul_r = nodes.new("ShaderNodeMath")
        n_mul_r.label = LBL_ROUGH_CTL
        n_mul_r.operation = "MULTIPLY"
        links.new(n_rough_switch.outputs["Color"], n_mul_r.inputs[0])
        n_mul_m = nodes.new("ShaderNodeMath")
        n_mul_m.label = LBL_METAL_CTL
        n_mul_m.operation = "MULTIPLY"
        links.new(n_sep.outputs["Green"], n_mul_m.inputs[0])
    else:
        _log(
            f"[VanillaLayout] Using unified PBR node layout for {mat.name} (skipping RMAOS chain)"
        )
        n_mul_r = nodes.new("ShaderNodeMath")
        n_mul_r.label = LBL_ROUGH_CTL
        n_mul_r.operation = "MULTIPLY"
        n_mul_m = nodes.new("ShaderNodeMath")
        n_mul_m.label = LBL_METAL_CTL
        n_mul_m.operation = "MULTIPLY"
        n_rough_inv_val = nodes.new("ShaderNodeValue")
        n_rough_inv_val.label = LBL_ROUGH_INV

    n_ao_mix = nodes.new("ShaderNodeMixRGB")
    n_ao_mix.label = LBL_AO_MIX
    n_ao_mix.blend_type = "MULTIPLY"
    links.new(n_base.outputs["Color"], n_ao_mix.inputs["Color1"])

    n_sep_norm = nodes.new("ShaderNodeSeparateRGB")
    n_sep_norm.label = "Separate RGB (Legacy)"
    links.new(n_norm.outputs["Color"], n_sep_norm.inputs["Image"])
    n_inv_g = nodes.new("ShaderNodeMath")
    n_inv_g.operation = "SUBTRACT"
    n_inv_g.inputs[0].default_value = 1.0
    n_inv_g.label = "Subtract (Normal)"
    links.new(n_sep_norm.outputs["G"], n_inv_g.inputs[1])
    n_comb_norm = nodes.new("ShaderNodeCombineRGB")
    n_comb_norm.label = "Combine RGB (Legacy)"
    links.new(n_sep_norm.outputs["R"], n_comb_norm.inputs["R"])
    links.new(n_inv_g.outputs[0], n_comb_norm.inputs["G"])
    links.new(n_sep_norm.outputs["B"], n_comb_norm.inputs["B"])
    n_mix_norm = nodes.new("ShaderNodeMixRGB")
    n_mix_norm.label = "Normal Flip Switch"
    links.new(n_norm.outputs["Color"], n_mix_norm.inputs["Color1"])
    links.new(n_comb_norm.outputs["Image"], n_mix_norm.inputs["Color2"])
    n_nmap = nodes.new("ShaderNodeNormalMap")
    n_nmap.label = LBL_NORM_MAP
    links.new(n_mix_norm.outputs["Color"], n_nmap.inputs["Color"])

    n_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    n_bsdf.label = LBL_BSDF
    n_disp = nodes.new("ShaderNodeDisplacement")
    n_disp.label = LBL_DISP
    n_out = nodes.new("ShaderNodeOutputMaterial")
    n_out.label = LBL_OUT

    links.new(n_ao_mix.outputs["Color"], n_bsdf.inputs["Base Color"])
    if is_pbr:
        links.new(n_mul_r.outputs[0], n_bsdf.inputs["Roughness"])
        links.new(n_mul_m.outputs[0], n_bsdf.inputs["Metallic"])
    links.new(n_nmap.outputs["Normal"], n_bsdf.inputs["Normal"])

    if n_em.image:
        links.new(n_em.outputs["Color"], n_bsdf.inputs["Emission Color"])
    else:
        n_em_color = nodes.new("ShaderNodeRGB")
        n_em_color.label = LBL_EM_COLOR
        links.new(n_em_color.outputs[0], n_bsdf.inputs["Emission Color"])

    if n_para.image:
        links.new(n_para.outputs["Color"], n_disp.inputs["Height"])
        links.new(n_disp.outputs["Displacement"], n_out.inputs["Displacement"])

    links.new(n_bsdf.outputs["BSDF"], n_out.inputs["Surface"])
    _ensure_emissive_chain(mat)

    # --- Alpha connection ---
    try:
        if n_base.image:
            links.new(n_base.outputs["Alpha"], n_bsdf.inputs["Alpha"])
    except Exception as e:
        _log(f"[UnifiedBuilder] Alpha connect failed: {e}")

    """Ensures: (GlowTex? × EmissionColorRGB) -> BSDF.Emission Color; Strength from UI."""
    if not mat or not mat.node_tree:
        return
    nt, links = mat.node_tree, mat.node_tree.links

    n_bsdf = _find_node_by_label(nt, LBL_BSDF)
    if not n_bsdf:
        return

    # Existing nodes if present
    n_em_tex = _find_node_by_label(nt, LBL_EMISSIVE)
    n_em_rgb = _find_node_by_label(nt, LBL_EM_COLOR)
    n_em_tint = _find_node_by_label(nt, LBL_EM_TINT)

    # Ensure color wheel node exists
    if not n_em_rgb:
        try:
            n_em_rgb = nt.nodes.new("ShaderNodeRGB")
            n_em_rgb.label = LBL_EM_COLOR
        except Exception:
            return

    # If glow texture exists, create multiply tint; else link color directly
    if n_em_tex and getattr(n_em_tex, "image", None):
        if not n_em_tint:
            n_em_tint = nt.nodes.new("ShaderNodeMixRGB")
            n_em_tint.blend_type = "MULTIPLY"
            n_em_tint.inputs["Fac"].default_value = 1.0
            n_em_tint.label = LBL_EM_TINT
        try:
            # Wire GlowTex × Color → Emission Color (overwrites previous link)
            if not n_em_tex.outputs["Color"].is_linked or all(
                l.to_node != n_em_tint for l in n_em_tex.outputs["Color"].links
            ):
                links.new(n_em_tex.outputs["Color"], n_em_tint.inputs["Color1"])
            if not n_em_rgb.outputs[0].is_linked or all(
                l.to_node != n_em_tint for l in n_em_rgb.outputs[0].links
            ):
                links.new(n_em_rgb.outputs[0], n_em_tint.inputs["Color2"])
            # Ensure BSDF emission input uses the tint output
            links.new(n_em_tint.outputs["Color"], n_bsdf.inputs["Emission Color"])
        except Exception:
            pass
    else:
        try:
            links.new(n_em_rgb.outputs[0], n_bsdf.inputs["Emission Color"])
        except Exception:
            pass

    # --- Skyrim Unified Layout (pixel-matched to your reference) ---
    layout_positions = {
        # Left column (textures)
        "Base": (-1700, 650),
        "RMAOS": (-1700, 350),
        "Normal": (-1700, 50),
        "Emissive": (-1700, -250),
        "Parallax": (-1700, -550),
        # Top band (AO / roughness / metal)
        "AO Mix (Multiply)": (-1400, 600),
        "Roughness Invert": (-1200, 600),
        "Mix": (-1000, 600),
        "Roughness Control": (-800, 600),
        "Metallic Control": (-800, 500),
        # Middle band (RMAOS logic)
        "RMAOS Separate": (-1400, 350),
        "Subtract": (-1200, 320),
        "Combine RGB (Legacy)": (-1000, 350),
        # Middle-lower band (normal logic)
        "Separate RGB (Legacy)": (-1400, 50),
        "Subtract (Normal)": (-1200, 20),
        "Combine RGB (Legacy)": (-1000, 30),
        "Normal Flip Switch": (-800, 40),
        "Normal Map": (-600, 40),
        # Bottom band (emissive)
        "Emissive Color (fallback)": (-1100, -150),
        "Emissive Tint (Multiply)": (-800, -150),
        # Right side (shader/output)
        "Principled BSDF": (-200, 180),
        "Displacement": (-200, -220),
        "Material Output": (200, 180),
    }

    # --- Apply node layout positions ---
    for n in nt.nodes:
        key = getattr(n, "label", None)
        if key in layout_positions:
            n.location = layout_positions[key]

    print(f"[UnifiedBuilder] Skyrim-style node layout applied for {mat.name}")

    _log(
        f"[UnifiedBuilder] Build complete for {mat.name} ({'PBR' if is_pbr else 'Vanilla'})"
    )

    # Restore or retain emissive values to new BSDF
    n_bsdf = _find_node_by_label(nt, LBL_BSDF)
    if n_bsdf:
        # Prefer freshly preserved values
        use_color = prev_em_color or getattr(mat.skpbr, "emission_color", (1, 1, 1))
        use_strength = (
            prev_em_strength
            if prev_em_strength is not None
            else getattr(mat.skpbr, "emission_strength", 0.0)
        )

        # Apply to nodes
        n_bsdf.inputs["Emission Color"].default_value = (*use_color, 1.0)
        n_bsdf.inputs["Emission Strength"].default_value = use_strength

        # Persist to skpbr props
        mat.skpbr.emission_color = use_color
        mat.skpbr.emission_strength = use_strength
        mat.skpbr.emission_on = use_strength > 0.0

        _log(
            f"[UnifiedBuilder] Retained emissive color={use_color}, strength={use_strength}"
        )

        try:
            _init_alpha_from_nif(mat)
        except Exception:
            pass


def _emissive_apply_to_nodes(mat):
    """Live-sync UI props → nodes (color & strength + smart on/off)."""
    if not mat or not hasattr(mat, "skpbr") or not mat.node_tree:
        return
    s = mat.skpbr
    nt = mat.node_tree

    # Ensure chain exists before updates
    _ensure_emissive_chain(mat)

    # Update color wheel
    n_em_rgb = _find_node_by_label(nt, LBL_EM_COLOR)
    if n_em_rgb:
        try:
            col = s.emission_color if hasattr(s, "emission_color") else (1.0, 1.0, 1.0)
            r, g, b = float(col[0]), float(col[1]), float(col[2])
            n_em_rgb.outputs[0].default_value = (r, g, b, 1.0)
        except Exception:
            pass

    # Smart toggle: strength > 0 → on; toggle on at 0 → set to 1.0 handled in props
    n_bsdf = _find_node_by_label(nt, LBL_BSDF)
    if n_bsdf:
        try:
            n_bsdf.inputs["Emission Strength"].default_value = (
                float(s.emission_strength) if bool(s.emission_on) else 0.0
            )
        except Exception:
            pass

    # Force UI + viewport redraw so emissive values update everywhere
    try:
        import bpy

        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {"VIEW_3D", "PROPERTIES"}:
                    area.tag_redraw()
    except Exception:
        pass


# ============================================================
# Alpha Mix Update Helper — Keeps transparency slider in sync
# ============================================================

# [AlphaMix removed]


def _load_image(
    node: bpy.types.ShaderNodeTexImage, path: Optional[Path], colorspace: str
):
    if not path:
        return
    try:
        node.image = bpy.data.images.load(str(path), check_existing=True)
        node.image.colorspace_settings.name = colorspace
    except Exception as e:
        _log(f"Load fail {node.label}: {e}")


def _is_valid_pbr_set(textures: Dict[str, Optional[Path]]) -> bool:
    return bool(
        textures.get("BASE") and textures.get("NORMAL") and textures.get("RMAOS")
    )


class SKPBR_OT_BuildAuto(bpy.types.Operator):
    bl_idname = "skpbr.build_auto"
    bl_label = "Build Skyrim PBR"
    bl_options = {"REGISTER", "UNDO"}

    def build_for_material(self, mat: bpy.types.Material, prefs) -> bool:
        """Core builder logic with PyNifly emissive preservation."""
        try:
            import os

            # --- Preserve emissive from current node tree (PyNifly import) ---
            em_color, em_strength = None, None
            if mat.node_tree:
                for n in mat.node_tree.nodes:
                    if isinstance(n, bpy.types.ShaderNodeEmission):
                        try:
                            em_color = tuple(n.inputs["Color"].default_value[:3])
                            em_strength = n.inputs["Strength"].default_value
                            _log(
                                f"[SMP] Preserved emissive before rebuild: color={em_color}, strength={em_strength}"
                            )
                        except Exception:
                            pass
                        break

            # --- Build process ---
            if prefs.search_mode == "NIFPATH":
                _clear_non_vfs_anchors(mat)

            anchor = _choose_anchor_for_mode(mat, prefs)
            if not anchor:
                _log("No anchor found.")
                return False

            textures = _resolve_textures_for_anchor(anchor, prefs)
            has_pbr = _detect_pbr(textures)
            force_pbr = bool(getattr(mat, "skpbr", None) and mat.skpbr.force_build)

            if has_pbr or force_pbr:
                # Build PBR graph
                build_nodes_unified(mat, textures)
                _alpha_preview_refresh(mat)
                _set_build_status(
                    mat, "FORCED_PBR" if (not has_pbr and force_pbr) else "PBR"
                )
                _apply_nif_emissive_to_bsdf_if_flag(mat)

            else:
                # Build vanilla graph
                build_nodes_unified(mat, textures)
                _alpha_preview_refresh(mat)
                _set_build_status(mat, "NONPBR")

            # --- Add transparency nodes for alpha control ---
            try:
                nt = mat.node_tree
                nodes, links = nt.nodes, nt.links

                bsdf = _find_node_by_label(nt, LBL_BSDF)
                base_tex = _find_node_by_label(nt, LBL_BASE)
                output_node = next(
                    (n for n in nodes if n.type == "OUTPUT_MATERIAL"), None
                )

                if bsdf and base_tex and output_node:
                    # Ensure Base Color is linked (safe no-op if already)
                    if not bsdf.inputs["Base Color"].is_linked:
                        links.new(base_tex.outputs["Color"], bsdf.inputs["Base Color"])

                    #
                    # --- Alpha from NIF (Skyrim-accurate) ---
                    try:
                        _init_alpha_from_nif(
                            mat
                        )  # read mode/threshold from NIF if available
                        _apply_alpha_logic(
                            mat, base_tex
                        )  # set blend/shadow + wire Base Alpha → BSDF Alpha
                    except Exception as e:
                        _log(f"[UnifiedBuilder] Alpha setup failed: {e}")

            except Exception as e:
                _log(f"[SMP] Failed to build Alpha transparency for {mat.name}: {e}")

            _remember_mode_anchor(mat, prefs, anchor)

            # --- Reapply emissive after rebuild ---
            if em_color and em_strength is not None:
                s = getattr(mat, "skpbr", None)
                if s:
                    s.emission_color = em_color
                    s.emission_strength = em_strength
                    s.emission_on = em_strength > 0.0
                _emissive_apply_to_nodes(mat)
                _log(
                    f"[SMP] Reapplied emissive after rebuild: color={em_color}, strength={em_strength}"
                )

            return True

        except Exception as e:
            import traceback

            _log(f"[SMP] build_for_material failed for {mat.name}: {e}")
            traceback.print_exc()
            return False

    def execute(self, ctx):
        mat = _get_active_material(ctx)
        if not mat:
            self.report({"ERROR"}, "No active material")
            return {"CANCELLED"}

        prefs = bpy.context.preferences.addons[__name__].preferences
        ok = self.build_for_material(mat, prefs)
        if not ok:
            self.report({"ERROR"}, "Build failed (no anchor).")
            return {"CANCELLED"}

        self.report({"INFO"}, "Skyrim PBR: Build complete.")
        return {"FINISHED"}


class SKPBR_OT_BuildAutoSelected(bpy.types.Operator):
    """Apply Build Skyrim PBR across all materials on selected mesh objects."""

    bl_idname = "skpbr.build_auto_selected"
    bl_label = "Build (All Selected Mats)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, ctx):
        prefs = bpy.context.preferences.addons[__name__].preferences
        objs = [o for o in ctx.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "No mesh objects selected")
            return {"CANCELLED"}
        count = 0
        op = SKPBR_OT_BuildAuto
        for obj in objs:
            for slot in obj.material_slots:
                mat = slot.material
                if not mat:
                    continue
                if op.build_for_material(self, mat, prefs):
                    count += 1
        self.report({"INFO"}, f"Built {count} material(s).")
        return {"FINISHED"}


class SKPBR_OT_LoadJSONAndBuild(bpy.types.Operator, ImportHelper):
    bl_idname = "skpbr.load_json_and_build"
    bl_label = "Load JSON & Build"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, ctx):
        mat = _get_active_material(ctx)
        if not mat:
            self.report({"ERROR"}, "No active material")
            return {"CANCELLED"}
        prefs = bpy.context.preferences.addons[__name__].preferences
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                js = json.load(f)
        except Exception as e:
            self.report({"ERROR"}, f"JSON load failed: {e}")
            return {"CANCELLED"}
        entry = (
            js
            if isinstance(js, dict)
            else (js[0] if isinstance(js, list) and js else None)
        )
        json_dir = Path(self.filepath).parent
        anchor = _extract_base_from_json(entry, json_dir) or _choose_anchor_for_mode(
            mat, prefs
        )
        if not anchor:
            self.report({"ERROR"}, "No anchor found (JSON had no 'base').")
            return {"CANCELLED"}
        textures = _resolve_textures_for_anchor(anchor, prefs)
        _apply_json_overrides(textures, entry, json_dir)
        _apply_json_settings(mat, entry if isinstance(entry, dict) else {})

        has_pbr = _detect_pbr(textures)
        force_pbr = bool(getattr(mat, "skpbr", None) and mat.skpbr.force_build)

        if has_pbr or force_pbr:
            build_nodes_unified(mat, textures)
            _alpha_preview_refresh(mat)
            _alpha_preview_refresh(mat)
            _emissive_apply_to_nodes(mat)
            _set_build_status(
                mat, "FORCED_PBR" if (not has_pbr and force_pbr) else "PBR"
            )
            _apply_nif_emissive_to_bsdf_if_flag(mat)
        else:
            build_nodes_unified(mat, textures)
            _alpha_preview_refresh(mat)
            _alpha_preview_refresh(mat)
            _emissive_apply_to_nodes(mat)
            _set_build_status(mat, "NONPBR")

        _remember_mode_anchor(mat, prefs, anchor)
        self.report({"INFO"}, "Skyrim PBR: JSON build complete.")
        return {"FINISHED"}


class SKPBR_OT_RebuildFromNIF(bpy.types.Operator):
    bl_idname = "skpbr.rebuild_from_nif"
    bl_label = "Rebuild from NIF Textures"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, ctx):
        mat = _get_active_material(ctx)
        if not mat or not mat.node_tree:
            self.report({"ERROR"}, "No active material")
            return {"CANCELLED"}
        textures = {
            "BASE": None,
            "NORMAL": None,
            "RMAOS": None,
            "PARALLAX": None,
            "EMISSIVE": None,
        }
        for n in mat.node_tree.nodes:
            if isinstance(n, bpy.types.ShaderNodeTexImage) and n.image:
                try:
                    p = Path(bpy.path.abspath(n.image.filepath))
                    low = p.as_posix().lower()
                except Exception:
                    continue
                if "_n.dds" in low:
                    textures["NORMAL"] = p
                elif "_rmaos.dds" in low or "_orm.dds" in low:
                    textures["RMAOS"] = p
                elif "_p.dds" in low:
                    textures["PARALLAX"] = p
                elif (
                    low.endswith("_em.dds")
                    or low.endswith("_g.dds")
                    or low.endswith("_e.dds")
                ):
                    textures["EMISSIVE"] = p
                elif low.endswith(".dds"):
                    textures["BASE"] = p

        has_pbr = _detect_pbr(textures)
        force_pbr = bool(getattr(mat, "skpbr", None) and mat.skpbr.force_build)

        if has_pbr or force_pbr:
            build_nodes_unified(mat, textures)
            _alpha_preview_refresh(mat)
            _alpha_preview_refresh(mat)
            _emissive_apply_to_nodes(mat)
            _set_build_status(
                mat, "FORCED_PBR" if (not has_pbr and force_pbr) else "PBR"
            )
            _apply_nif_emissive_to_bsdf_if_flag(mat)
        else:
            build_nodes_unified(mat, textures)
            _alpha_preview_refresh(mat)
            _alpha_preview_refresh(mat)
            _emissive_apply_to_nodes(mat)
            _set_build_status(mat, "NONPBR")

        self.report({"INFO"}, "Skyrim PBR: Rebuilt from NIF textures.")
        return {"FINISHED"}


class SKPBR_OT_SelectBaseDDS(bpy.types.Operator, ImportHelper):
    bl_idname = "skpbr.select_base_dds"
    bl_label = "Select Base DDS"
    bl_options = {"REGISTER", "UNDO"}
    filename_ext = ".dds"
    filter_glob: StringProperty(default="*.dds", options={"HIDDEN"})

    def execute(self, ctx):
        mat = _get_active_material(ctx)
        if not mat:
            self.report({"ERROR"}, "No active material")
            return {"CANCELLED"}
        p = Path(self.filepath)
        _store_anchor(mat, KEY_STICKY_MANUAL, p)
        _store_anchor(mat, KEY_LAST_MANUAL, p)
        self.report({"INFO"}, f"Manual Base DDS set: {p.name}")
        return {"FINISHED"}


class SKPBR_OT_ResetParams(bpy.types.Operator):
    bl_idname = "skpbr.reset_params"
    bl_label = "Reset Parameters"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, ctx):
        mat = _get_active_material(ctx)
        if not mat or not hasattr(mat, "skpbr"):
            self.report({"WARNING"}, "No active Skyrim PBR material")
            return {"CANCELLED"}
        s = mat.skpbr
        s.rough_mult = 1.0
        s.invert_roughness = False
        s.metal_mult = 1.0
        s.normal_strength = 1.0
        s.disp_scale = 0.05
        s.disp_mid = 0.5
        s.emissive_strength = 0.0
        s.emissive_color = (1, 1, 1, 1)
        s.alpha_strength = 1.0
        s.ao_strength = 1.0
        s.flip_norm_y = False
        # Keep s.force_build as user chose
        self.report({"INFO"}, "Parameters reset to defaults (Force PBR unchanged)")
        return {"FINISHED"}


class SKPBR_OT_ReturnToVanilla(bpy.types.Operator):
    """Reset selected materials to VFS default (PBR if RMAOS exists, else vanilla)."""

    bl_idname = "skpbr.return_to_vanilla"
    bl_label = "Return to Vanilla"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, ctx):
        prefs = bpy.context.preferences.addons[__name__].preferences
        objs = [o for o in ctx.selected_objects if o.type == "MESH"]
        if not objs:
            # fallback: just active object
            mat = _get_active_material(ctx)
            if not mat:
                self.report({"ERROR"}, "No mesh or material selected")
                return {"CANCELLED"}
            objs = [ctx.active_object]

        count = 0
        for obj in objs:
            for slot in obj.material_slots:
                mat = slot.material
                if not mat:
                    continue
                prefs.search_mode = "NIFPATH"
                _clear_non_vfs_anchors(mat)
                ok = SKPBR_OT_BuildAuto.build_for_material(self, mat, prefs)
                if ok:
                    count += 1

        self.report({"INFO"}, f"Reset {count} material(s) to VFS default.")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Export Selected PBR Patch (textures + PBRNifPatcher JSON)
# ---------------------------------------------------------------------------
class SKPBR_OT_ExportPatch(bpy.types.Operator, ImportHelper):
    """Export selected meshes' material textures + PBRNifPatcher JSON into a mod folder."""

    bl_idname = "skpbr.export_patch"
    bl_label = "Export Selected PBR Patch"
    bl_options = {"REGISTER", "UNDO"}
    use_filter_folder = True
    files: CollectionProperty(type=bpy.types.PropertyGroup)  # directory picker
    filename_ext = ""

    export_diffuse: BoolProperty(name="Export Diffuse (_d)", default=True)
    export_normal: BoolProperty(name="Export Normal (_n)", default=True)
    export_rmaos: BoolProperty(name="Export RMAOS (_rmaos)", default=True)
    export_parallax: BoolProperty(name="Export Parallax (_p)", default=True)
    export_emissive: BoolProperty(name="Export Emissive (_g/_em)", default=False)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def draw(self, context):
        col = self.layout.column(align=True)
        col.label(text="Select which maps to include in the export:")
        col.prop(self, "export_diffuse")
        col.prop(self, "export_normal")
        col.prop(self, "export_rmaos")
        col.prop(self, "export_parallax")
        col.prop(self, "export_emissive")
        col.label(
            text="Destination will be used as mod root (textures/... and PBRNifPatcher/).",
            icon="INFO",
        )

    def execute(self, context):
        dest_root = Path(self.filepath) if self.filepath else None
        if not dest_root:
            self.report({"ERROR"}, "No destination selected")
            return {"CANCELLED"}
        if dest_root.suffix:  # if user clicked a file, use its parent
            dest_root = dest_root.parent

        # Textures must go into textures/pbr/ for PBRNifPatcher compatibility
        textures_root = dest_root / "textures" / "pbr"
        patcher_root = dest_root / "PBRNifPatcher"

        textures_root.mkdir(parents=True, exist_ok=True)
        patcher_root.mkdir(parents=True, exist_ok=True)

        exported_any = False
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "Select at least one mesh object to export")
            return {"CANCELLED"}

        for obj in objs:
            for slot in obj.material_slots:
                mat = slot.material
                if not mat or not mat.node_tree:
                    continue

                # Collect texture paths by node label
                src = {}
                for n in mat.node_tree.nodes:
                    if isinstance(n, bpy.types.ShaderNodeTexImage) and n.image:
                        label = getattr(n, "label", "")
                        try:
                            path = Path(bpy.path.abspath(n.image.filepath))
                        except Exception:
                            continue
                        src[label] = path

                base_path = src.get(LBL_BASE)
                if not base_path:
                    continue

                # Derive Skyrim-like relative path under textures/
                parts_low = [p.lower() for p in base_path.parts]
                if "textures" in parts_low:
                    idx = parts_low.index("textures")
                    skyrim_rel = Path(*base_path.parts[idx + 1 :])
                else:
                    skyrim_rel = Path(base_path.name)

                out_dir = textures_root / skyrim_rel.parent
                out_dir.mkdir(parents=True, exist_ok=True)

                # Decide which maps to copy
                to_copy: List[Tuple[str, Optional[Path]]] = []
                if self.export_diffuse:
                    to_copy.append(("d", src.get(LBL_BASE)))
                if self.export_normal:
                    to_copy.append(("n", src.get(LBL_NORMAL)))
                if self.export_rmaos:
                    to_copy.append(("rmaos", src.get(LBL_RMAOS)))
                if self.export_parallax:
                    to_copy.append(("p", src.get(LBL_PARALLAX)))
                if self.export_emissive:
                    to_copy.append(("g", src.get(LBL_EMISSIVE)))

                # Copy
                for tag, p in to_copy:
                    if not p or not p.is_file():
                        continue
                    dst = out_dir / p.name
                    try:
                        if os.path.abspath(p) != os.path.abspath(dst):
                            shutil.copy2(p, dst)
                        exported_any = True
                    except Exception as e:
                        _log(f"Copy failed: {p} -> {dst}: {e}")

                # Write PBRNifPatcher JSON for this material
                basename = base_path.stem
                for suf in ("_d", "_n", "_rmaos", "_p", "_em", "_e", "_g", "_orm"):
                    if basename.lower().endswith(suf):
                        basename = basename[: -len(suf)]
                        break

                # Emissive export rules:
                emissive_strength_val = (
                    float(getattr(mat.skpbr, "emissive_strength", 0.0))
                    if hasattr(mat, "skpbr")
                    else 0.0
                )
                try:
                    ec_src = (
                        list(getattr(mat.skpbr, "emissive_color", (1, 1, 1, 1)))
                        if hasattr(mat, "skpbr")
                        else [1, 1, 1, 1]
                    )
                    emission_color = [
                        float(ec_src[0]),
                        float(ec_src[1]),
                        float(ec_src[2]),
                    ]
                except Exception:
                    emission_color = [1.0, 1.0, 1.0]
                emissive_flag = (
                    bool(src.get(LBL_EMISSIVE)) and self.export_emissive
                ) or (emissive_strength_val > 0.0)

                # Compute parallax_strength (÷10 from Blender preview)
                disp_scale_val = None
                try:
                    for n in mat.node_tree.nodes:
                        if getattr(n, "label", "") == LBL_DISP:
                            disp_scale_val = float(n.inputs.get("Scale").default_value)
                            break
                except Exception:
                    pass
                nif_strength = None
                try:
                    nif_strength = (
                        float(mat.get("parallax_strength_nif"))
                        if (
                            hasattr(mat, "keys")
                            and "parallax_strength_nif" in mat.keys()
                        )
                        else None
                    )
                except Exception:
                    nif_strength = None
                if nif_strength is not None:
                    parallax_strength_json = nif_strength
                elif disp_scale_val is not None:
                    parallax_strength_json = disp_scale_val / 10.0
                else:
                    parallax_strength_json = (
                        float(getattr(mat.skpbr, "parallax_default_strength", 0.02))
                        if hasattr(mat, "skpbr")
                        else 0.02
                    )

                patch = {
                    "match_diffuse": basename,
                    "parallax": bool(src.get(LBL_PARALLAX)) and self.export_parallax,
                    "parallax_strength": round(float(parallax_strength_json), 5),
                    "emissive": bool(emissive_flag),
                    "specular_level": 0.04,
                    "roughness_scale": (
                        float(getattr(mat.skpbr, "rough_mult", 1.0))
                        if hasattr(mat, "skpbr")
                        else 1.0
                    ),
                    "displacement_scale": (
                        float(getattr(mat.skpbr, "disp_scale", 0.05))
                        if hasattr(mat, "skpbr")
                        else 0.05
                    ),
                    "emission_strength": float(emissive_strength_val),
                    "emission_color": emission_color,
                }
                patch_path = patcher_root / f"{basename}.json"
                try:
                    with open(patch_path, "w", encoding="utf-8") as f:
                        json.dump(patch, f, indent=2)
                    exported_any = True
                except Exception as e:
                    _log(f"Failed to write patch JSON {patch_path}: {e}")

        if exported_any:
            self.report({"INFO"}, f"Export complete: {dest_root}")
            return {"FINISHED"}
        else:
            self.report(
                {"WARNING"}, "Nothing exported (no textures found or checkboxes off)."
            )
            return {"CANCELLED"}


# ---------------------------------------------------------------------------
# UI Panel
# ---------------------------------------------------------------------------
def _peek_status(context) -> List[str]:
    mat = _get_active_material(context)
    if not mat:
        return ["No material selected"]
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
    except Exception:
        return ["Addon prefs not ready"]
    anchor = _choose_anchor_for_mode(mat, prefs)
    mode = prefs.search_mode
    status = _get_build_status(mat)
    lines = []
    if anchor:
        lines.append(f"{mode} anchor: {str(anchor)}")
        tex = _resolve_textures_for_anchor(anchor, prefs)
        order = (
            "BASE",
            "NORMAL",
            "RMAOS",
            "PARALLAX",
            "M_PARALLAX",
            "S_SPEC",
            "EMISSIVE",
        )
        labels = {
            "BASE": "Base",
            "NORMAL": "Normal",
            "RMAOS": "RMAOS",
            "PARALLAX": "Parallax",
            "M_PARALLAX": "_m",
            "S_SPEC": "Specular",
            "EMISSIVE": "Emissive",
        }
        for k in order:
            v = tex.get(k)
            lines.append(f"  {labels[k]}: {str(v) if v else '—'}")
    else:
        lines.append(f"{mode} anchor: —")

    if status == "PBR":
        lines.append("PBR status: Full PBR")
    elif status == "FORCED_PBR":
        lines.append("PBR status: Forced PBR (fallbacks applied)")
    elif status == "NONPBR":
        lines.append("PBR status: Non-PBR (Diffuse + Normal only)")
    else:
        lines.append("PBR status: —")

    return lines


# ============================================================
# Batch Emissive Patcher (runs patch_emissive.py)
# ============================================================
class SKPBR_OT_PatchEmissiveBatch(bpy.types.Operator):
    """Run the Patch Emissive batch script on a folder of NIFs"""

    bl_idname = "skpbr.patch_emissive_batch"
    bl_label = "Patch Emissive (Batch)"
    bl_options = {"REGISTER", "UNDO"}

    input_dir: bpy.props.StringProperty(
        name="Input Folder",
        subtype="DIR_PATH",
        description="Folder containing NIF files to patch",
    )
    output_dir: bpy.props.StringProperty(
        name="Output Folder",
        subtype="DIR_PATH",
        description="Folder where patched NIFs will be written (leave blank to overwrite originals)",
    )
    emissive_multiple: bpy.props.FloatProperty(
        name="Emissive Strength",
        description="Multiplier for emissive intensity",
        default=0.7,
        min=0.0,
        max=10.0,
    )
    emissive_color: bpy.props.FloatVectorProperty(
        name="Emissive Color (RGB)", subtype="COLOR", size=3, default=(1.0, 0.9, 0.75)
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "input_dir")
        layout.prop(self, "output_dir")
        layout.prop(self, "emissive_multiple")
        layout.prop(self, "emissive_color")

    def invoke(self, context, event):
        # small dialog with the fields above
        return context.window_manager.invoke_props_dialog(self, width=520)

    def execute(self, context):
        import os, bpy
        from . import patch_emissive

        input_folder = bpy.path.abspath(self.input_dir)
        output_folder = (
            bpy.path.abspath(self.output_dir) if self.output_dir else input_folder
        )

        if not input_folder or not os.path.isdir(input_folder):
            self.report({"ERROR"}, "Select a valid Input Folder")
            return {"CANCELLED"}

        if not os.path.exists(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
            except Exception as e:
                self.report({"ERROR"}, f"Could not create Output Folder: {e}")
                return {"CANCELLED"}

        # pass settings to the standalone script
        patch_emissive.INPUT_DIR = input_folder
        patch_emissive.OUTPUT_DIR = output_folder
        patch_emissive.EMISSIVE_MULTIPLE = float(self.emissive_multiple)
        patch_emissive.EMISSIVE_COLOR = tuple(self.emissive_color)

        try:
            patch_emissive.main()
            self.report(
                {"INFO"}, f"Patched NIFs from '{input_folder}' → '{output_folder}'"
            )
            return {"FINISHED"}
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.report({"ERROR"}, f"Patch emissive failed: {e}")
            return {"CANCELLED"}


class SKPBR_PT_UI(bpy.types.Panel):
    bl_label = f"SkyBlend Toolkit v{__version__}"
    bl_idname = "SKPBR_PT_UI"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "SkyBlend"

    def draw(self, context):
        lay = self.layout
        col = lay.column(align=True)

        # Status
        for l in _peek_status(context):
            col.label(text=l)
        col.separator()

        # Actions
        row = col.row(align=True)
        row.operator("skpbr.build_auto_selected", text="Build Nodes", icon="MATERIAL")
        col.operator("skpbr.return_to_vanilla", icon="BRUSH_DATA")
        col.operator("skpbr.load_json_and_build", icon="IMPORT")
        col.operator("skpbr.rebuild_from_nif", icon="FILE_REFRESH")
        col.operator("skpbr.export_patch", icon="EXPORT")
        col.operator("skpbr.run_pbrnifpatcher", text="Run PBRNifPatcher")
        col.operator("skpbr.patch_emissive_batch", icon="SHADING_RENDERED")

        col.separator()

        # Search mode
        prefs = bpy.context.preferences.addons[__name__].preferences
        col.label(text="Texture Search Mode:")
        col.prop(prefs, "search_mode", expand=True)
        if prefs.search_mode == "MANUAL":
            col.prop(prefs, "manual_root")
            col.operator("skpbr.select_base_dds", icon="FILEBROWSER")

        col.separator()

        # Live settings – visible only when we are in PBR or Forced PBR
        mat = _get_active_material(context)
        status = _get_build_status(mat) if mat else ""
        show_pbr_controls = status in ("PBR", "FORCED_PBR")

        if mat and hasattr(mat, "skpbr") and show_pbr_controls:
            col.label(text="Adjustments (live):")
            col.prop(mat.skpbr, "rough_mult")
            col.prop(mat.skpbr, "invert_roughness")
            col.prop(mat.skpbr, "metal_mult")
            col.prop(mat.skpbr, "normal_strength")
            col.prop(mat.skpbr, "disp_scale")
            col.prop(mat.skpbr, "disp_mid")
            col.prop(mat.skpbr, "use_parallax_m")
            # (legacy emissive_* UI removed: using unified emission_* controls below)

            col.prop(mat.skpbr, "ao_strength")
            col.prop(mat.skpbr, "flip_norm_y")
            col.label(text="ON = Blender preview; OFF = Skyrim export.", icon="INFO")

        # Force PBR toggle always visible (so you can force next build)
        if mat and hasattr(mat, "skpbr"):
            col.separator()
            row = col.row(align=True)
            row.prop(mat.skpbr, "force_build")
            col.separator()
            col.label(text="Emission", icon="LIGHT_HEMI")
            row = col.row(align=True)
            row.prop(mat.skpbr, "emission_on", text="On")
            row.prop(mat.skpbr, "emission_strength", text="Strength")
            col.prop(mat.skpbr, "emission_color", text="Color")
            col.prop(mat.skpbr, "alpha_strength")
            col.prop(mat.skpbr, "use_parallax_m")
            col.prop(mat.skpbr, "parallax_default_strength")
            hint = (
                "(Will build full PBR even if set is incomplete)"
                if mat.skpbr.force_build
                else "(If VFS is non-PBR, build vanilla nodes)"
            )
            row = col.row(align=True)
            row.label(text=hint, icon="INFO")
            col.operator("skpbr.reset_params", icon="RECOVER_LAST")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
classes = (
    SKPBR_Prefs,
    SKPBR_PG_Settings,
    SKPBR_OT_BuildAuto,
    SKPBR_OT_BuildAutoSelected,
    SKPBR_OT_LoadJSONAndBuild,
    SKPBR_OT_RebuildFromNIF,
    SKPBR_OT_SelectBaseDDS,
    SKPBR_OT_ResetParams,
    SKPBR_OT_ReturnToVanilla,
    SKPBR_OT_ExportPatch,
    SKPBR_OT_PatchEmissiveBatch,
    SKPBR_OT_RunPBRNifPatcher,
    SKPBR_PT_UI,
)


# --- Emissive extraction for SE/AE (best-effort) ---
def _extract_emissive_from_nif(obj, nif_path=None):
    """
    Best-effort reader for Skyrim SE/AE emissive values.
    Tries PyNifly if available; otherwise returns (None, None).
    Returns: (color_tuple_rgb, multiple_float) or (None, None)
    """
    try:
        try:
            import pynifly  # confirmed available in your setup
        except Exception:
            pynifly = None

        # Resolve nif path if not provided
        if not nif_path:
            # Try common places your addon stores anchors/paths
            nif_path = getattr(obj, "nif_path", None)
            if not nif_path:
                try:
                    for k in getattr(obj, "keys", lambda: [])():
                        if "nif" in k.lower() and str(obj[k]).lower().endswith(".nif"):
                            nif_path = obj[k]
                            break
                except Exception:
                    pass

        if not nif_path or not isinstance(nif_path, str):
            return (None, None)

        # Try PyNifly structured read (may not expose emissive; harmless if missing)
        if pynifly is not None:
            try:
                nif = pynifly.load_nif(nif_path)
                for b in getattr(nif, "blocks", []):
                    col = None
                    mul = None
                    if hasattr(b, "emissive_color"):
                        try:
                            rgb = tuple(getattr(b, "emissive_color"))
                            if len(rgb) >= 3:
                                col = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
                        except Exception:
                            pass
                    if hasattr(b, "emissive_multiple"):
                        try:
                            mul = float(getattr(b, "emissive_multiple"))
                        except Exception:
                            pass
                    if col is None and hasattr(b, "emissive"):
                        try:
                            rgb = tuple(getattr(b, "emissive"))
                            if len(rgb) >= 3:
                                col = (float(rgb[0]), float(rgb[1]), float(rgb[2]))
                        except Exception:
                            pass
                    if mul is None and hasattr(b, "emissive_mult"):
                        try:
                            mul = float(getattr(b, "emissive_mult"))
                        except Exception:
                            pass
                    if col is not None or mul is not None:
                        return (col, mul if mul is not None else 0.0)
            except Exception:
                pass

        return (None, None)
    except Exception:
        return (None, None)


# --------------------------------------------------------------
# Load / Unload Embedded Blender-DDS-Addon (Third-party Integration)
# --------------------------------------------------------------


def _load_embedded_dds_addon():
    """Registers the embedded DDS Addon bundled with this addon."""
    import importlib, sys, os

    # Paths that match your real layout
    addon_path = os.path.join(
        os.path.dirname(__file__), "thirdparty", "blender_dds_addon"
    )
    ui_path = os.path.join(addon_path, "ui")

    # Sanity checks
    if not os.path.isdir(addon_path):
        print("[SMP] No embedded DDS addon found.")
        return None

    # Make both the package and its 'ui' modules importable
    if addon_path not in sys.path:
        sys.path.insert(0, addon_path)
    if os.path.isdir(ui_path) and ui_path not in sys.path:
        sys.path.insert(0, ui_path)

    # === Safe DDS addon import (bundled copy) ===
    try:
        from .safe_import import local_import

        if "blender_dds_addon" in sys.modules:
            del sys.modules["blender_dds_addon"]
        dds_addon = local_import("blender_dds_addon", "blender_dds_addon")

        # Register if the addon has a register() function
        if hasattr(dds_addon, "register"):
            dds_addon.register()
            print(f"[SMP] Embedded DDS Addon registered from {dds_addon.__file__}")
        else:
            print(f"[SMP WARNING] DDS Addon loaded but has no 'register' method.")
    except Exception as e:
        print(f"[SMP ERROR] Failed to load DDS Addon: {e}")
    # =============================================


# ---------------------------------------------------------------------------
# Embedded DDS Addon: Safe Unloader (fixes NameError on disable)
# ---------------------------------------------------------------------------
def _unload_embedded_dds_addon(dds_addon):
    """Safely unregister and remove the embedded DDS addon when disabling."""
    if dds_addon is None:
        return
    try:
        if hasattr(dds_addon, "unregister"):
            dds_addon.unregister()
            print("[SMP] Embedded DDS Addon unregistered.")
        else:
            print("[SMP WARNING] DDS Addon had no unregister() function.")
    except Exception as e:
        print(f"[SMP ERROR] Failed to unload DDS Addon: {e}")


def register():

    # v205: purge duplicate Emission and _m panels from this module
    try:
        seen_em = False
        seen_m = False
        for name, cls in list(bpy.types.__dict__.items()):
            if not (isinstance(cls, type) and issubclass(cls, bpy.types.Panel)):
                continue
            mod = getattr(cls, "__module__", "")
            if __name__ not in mod:
                continue
            lab = (getattr(cls, "bl_label", "") or "").lower()
            cname = name.lower()
            is_em = (
                ("emission" in lab) or ("emissive" in lab) or ("emission color" in lab)
            )
            is_m = (
                ("use _m as parallax" in lab)
                or ("parallax (pg mode)" in lab)
                or ("_m" in lab)
            )
            if is_em:
                if seen_em:
                    try:
                        bpy.utils.unregister_class(cls)
                    except Exception:
                        try:
                            cls.poll = classmethod(lambda _c, _ctx: False)
                        except Exception:
                            pass
                else:
                    seen_em = True
            if is_m:
                if seen_m:
                    try:
                        bpy.utils.unregister_class(cls)
                    except Exception:
                        try:
                            cls.poll = classmethod(lambda _c, _ctx: False)
                        except Exception:
                            pass
                else:
                    seen_m = True
        print(f"[SMP v205] UI purge done. kept_em={seen_em} kept_m={seen_m}")
    except Exception as e:
        print("[SMP v205] UI purge failed:", e)
    # SMP v202: Purge legacy Emissive/_m panels from any previous loads
    try:
        to_hide = []
        for _n, _cls in list(bpy.types.__dict__.items()):
            if isinstance(_cls, type) and issubclass(_cls, bpy.types.Panel):
                mod = getattr(_cls, "__module__", "")
                lab = (getattr(_cls, "bl_label", "") or "").lower()
                cname = _n.lower()
                if (
                    "emiss" in lab
                    or "emiss" in cname
                    or "use _m as parallax" in lab
                    or "pg mode" in lab
                ) and __name__ in mod:
                    to_hide.append(_cls)
        # Keep a single _m panel by leaving the first one intact if multiple
        kept_m = False
        for _cls in to_hide:
            is_m = "use _m as parallax" in (getattr(_cls, "bl_label", "") or "").lower()
            if is_m and not kept_m:
                kept_m = True
                continue
            try:
                bpy.utils.unregister_class(_cls)
            except Exception:
                try:
                    _cls.poll = classmethod(lambda _c, _ctx: False)
                except Exception:
                    pass
        print(
            "[SMP] Legacy UI purged; kept single _m toggle."
            if kept_m
            else "[SMP] Legacy UI purged."
        )
    except Exception as _e:
        print("[SMP] Legacy UI purge failed:", _e)
    try:
        for _n, _cls in list(bpy.types.__dict__.items()):
            if isinstance(_cls, type) and issubclass(_cls, bpy.types.Panel):
                mod = getattr(_cls, "__module__", "")
                lab = (getattr(_cls, "bl_label", "") or "").lower()
                cname = _n.lower()
                if __name__ in mod and (
                    "parallaxgen" in lab
                    or "use _m" in lab
                    or "pg mode" in lab
                    or ("emission color" in lab)
                    or ("use _m as parallax" in lab)
                    or ("emiss" in lab and "adjustments" not in lab)
                ):
                    _cls.poll = classmethod(lambda _c, _ctx: False)
        print("[BabyJaws SMP] Legacy emissive panels hidden.")
    except Exception as _e:
        print("[BabyJaws SMP] Hide legacy panels failed:", _e)
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Material.skpbr = PointerProperty(type=SKPBR_PG_Settings)
    _log("Addon registered (v1.9.8, strict stem matching + Return to Vanilla).")

    # ------------------------------------------------------------------------
    # Integrate PBRGen (bundled) if present
    # ------------------------------------------------------------------------
    try:
        from . import pbrgen

        if hasattr(pbrgen, "register"):
            pbrgen.register()
            print("[SMP] PBRGen integrated successfully.")
    except Exception as e:
        print(f"[SMP] Warning: could not load PBRGen: {e}")

    global _embedded_dds_addon
    _embedded_dds_addon = _load_embedded_dds_addon()


def unregister():
    # ------------------------------------------------------------------------
    # Cleanly remove PBRGen if it was registered
    # ------------------------------------------------------------------------
    try:
        from . import pbrgen

        if hasattr(pbrgen, "unregister"):
            pbrgen.unregister()
            print("[SMP] PBRGen unregistered successfully.")
    except Exception:
        pass

    # ------------------------------------------------------------------------
    # Original SMP unregistration logic (unchanged)
    # ------------------------------------------------------------------------
    del bpy.types.Material.skpbr
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
    _log("Addon unregistered.")

    global _embedded_dds_addon
    _unload_embedded_dds_addon(_embedded_dds_addon)
    _embedded_dds_addon = None


if __name__ == "__main__":
    register()


# ---------------------------------------------------------------
# NIF Emissive Extraction Helper (PyNifly-compatible)
# ---------------------------------------------------------------
def extract_emissive_from_pynifly(obj):
    """
    Try to extract emissive color and multiple from a PyNifly-imported object.

    This checks PyNifly's internal nif_blocks data for emissiveColor and
    emissiveMultiple fields, returning a dict {"color": (r,g,b), "strength": float}.
    Safe to call even if the fields aren't available.
    """
    try:
        data = getattr(obj.data, "nif_blocks", None)
        if not data:
            return None

        for blk in data:
            if hasattr(blk, "emissiveColor") and hasattr(blk, "emissiveMultiple"):
                color = tuple(getattr(blk, "emissiveColor"))
                strength = float(getattr(blk, "emissiveMultiple"))
                # Clamp & normalize
                color = tuple(max(0.0, min(1.0, c)) for c in color)
                strength = max(0.0, min(strength, 10.0))
                return {"color": color, "strength": strength}
        return None
    except Exception as e:
        print(f"[SKPBR] emissive extraction failed: {e}")
        return None


# ---------------------------------------------------------------
# Apply emissive color and strength to material nodes
# ---------------------------------------------------------------
def _emissive_apply_to_nodes(mat):
    """Update any emission nodes in PBR or Vanilla setups with current skpbr data."""
    try:
        s = getattr(mat, "skpbr", None)
        if not s or not s.emission_on:
            return
        nt = getattr(mat, "node_tree", None)
        if not nt:
            return

        color = list(s.emission_color) + [1.0]
        strength = s.emission_strength

        for node in nt.nodes:
            # Principled BSDF (PBR)
            if node.type == "BSDF_PRINCIPLED":
                node.inputs["Emission"].default_value = color
                # Not all Blender versions have a separate "Emission Strength" input
                if "Emission Strength" in node.inputs:
                    node.inputs["Emission Strength"].default_value = strength
                else:
                    # Approximate by brightening emission color
                    node.inputs["Emission"].default_value = [
                        c * strength for c in color
                    ]
                print(f"[SMP] Applied emissive to PBR node for {mat.name}")
                return

            # Skyrim Shader Group (Vanilla)
            if node.type == "GROUP" and "Skyrim Shader" in node.name:
                if "Emission Color" in node.inputs:
                    node.inputs["Emission Color"].default_value = color
                if "Emission Strength" in node.inputs:
                    node.inputs["Emission Strength"].default_value = strength
                print(f"[SMP] Applied emissive to Skyrim Shader node for {mat.name}")
                return

    except Exception as e:
        print(f"[SMP] emissive node sync failed for {mat.name}: {e}")


def _init_emissive_from_nif(mat):
    """Sync emissive from NIF flags or numeric props; enable by flags even if no texture."""
    try:
        if not hasattr(mat, "skpbr"):
            return
        got_numeric = False
        if hasattr(mat, "keys"):
            for key_col in (
                "emissive_color",
                "Emissive Color",
                "EmitColor",
                "emissive",
            ):
                if key_col in mat.keys():
                    col = mat[key_col]
                    if isinstance(col, (list, tuple)) and len(col) == 3:
                        mat.skpbr.emission_color = (
                            float(col[0]),
                            float(col[1]),
                            float(col[2]),
                        )
                        got_numeric = True
                        break
            for key_mul in (
                "emissive_multiple",
                "Emissive Multiple",
                "EmissiveMultiple",
                "emissiveMult",
            ):
                if key_mul in mat.keys():
                    try:
                        val = float(mat[key_mul])
                        mat.skpbr.emission_strength = max(0.0, val)
                        got_numeric = True
                        break
                    except Exception:
                        pass

        def _read_flags(k):
            try:
                if hasattr(mat, "keys") and k in mat.keys():
                    return str(mat[k])
            except Exception:
                pass
            return ""

        raw_f1 = (
            _read_flags("shader_flags_1")
            or _read_flags("Shader_Flags_1")
            or _read_flags("BSLighting_Shader_Flags_1")
        )
        raw_f2 = (
            _read_flags("shader_flags_2")
            or _read_flags("Shader_Flags_2")
            or _read_flags("BSLighting_Shader_Flags_2")
        )
        s = (raw_f1 + " " + raw_f2).upper()

        tokens = [
            "OWN_EMIT",
            "EXTERNAL_EMITTANCE",
            "GLOW",
            "SOFT_LIGHTING",
            "EFFECT_LIGHTING",
        ]
        flags_hit = [t for t in tokens if t in s]

        if flags_hit:
            print(
                f"[SkyrimPatcher] Emissive flags on {mat.name}: {', '.join(flags_hit)}"
            )

        # If flags suggest emissive and strength is 0, set to Skyrim default 1.0
        if flags_hit and getattr(mat.skpbr, "emission_strength", 0.0) <= 0.0:
            mat.skpbr.emission_strength = 1.0

        # Final toggle mirrors strength > 0
        mat.skpbr.emission_on = bool(getattr(mat.skpbr, "emission_strength", 0.0) > 0.0)

        if mat.skpbr.emission_on:
            print(
                f"[SkyrimPatcher] Emission ON {mat.name} (strength={mat.skpbr.emission_strength:.3f}, color={tuple(mat.skpbr.emission_color)})"
            )
    except Exception as e:
        print("[SkyrimPatcher] _init_emissive_from_nif error:", e)


def _emissive_apply_to_nodes(mat):
    """Apply emission_on/strength/color to our labeled nodes only (safe; won't touch AO)."""
    try:
        if not mat or not getattr(mat, "node_tree", None) or not hasattr(mat, "skpbr"):
            return
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Principled
        n_bsdf = next(
            (n for n in nodes if getattr(n, "type", "") == "BSDF_PRINCIPLED"), None
        )
        if not n_bsdf:
            return

        # Color node (strict by label)
        n_rgb = next(
            (
                n
                for n in nodes
                if getattr(n, "label", "") == LBL_EM_COLOR
                and getattr(n, "type", "") == "RGB"
            ),
            None,
        )
        if n_rgb:
            try:
                n_rgb.outputs[0].default_value = (*mat.skpbr.emission_color, 1.0)
            except Exception:
                pass

        # Strength
        strength_val = (
            float(mat.skpbr.emission_strength) if bool(mat.skpbr.emission_on) else 0.0
        )
        # Set on Principled (authoritative)
        if "Emission Strength" in n_bsdf.inputs:
            try:
                n_bsdf.inputs["Emission Strength"].default_value = strength_val
            except Exception:
                pass

        # Also set on our multiply math (strict by label & type)
        n_mul = next(
            (
                n
                for n in nodes
                if getattr(n, "label", "") == LBL_EM_STRENGTH
                and getattr(n, "type", "") == "MATH"
            ),
            None,
        )
        if n_mul:
            try:
                n_mul.inputs[1].default_value = strength_val
            except Exception:
                pass

        print(
            f"[SkyrimPatcher] Emissive live: updated RGB+Strength for {mat.name} (on={mat.skpbr.emission_on}, str={strength_val:.3f})"
        )
    except Exception as e:
        print("[SkyrimPatcher] _emissive_apply_to_nodes error:", e)


def _smp_find_bsdf(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "BSDF_PRINCIPLED":
            return n
    return None


def _smp_ensure_emission_chain(mat, glow_tex_path=None):
    if not mat or not getattr(mat, "node_tree", None) or not hasattr(mat, "skpbr"):
        return
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links
    n_bsdf = _smp_find_bsdf(nt)
    if not n_bsdf or "Emission" not in n_bsdf.inputs:
        return
    n_rgb = next(
        (
            n
            for n in nodes
            if getattr(n, "type", "") == "RGB"
            and getattr(n, "label", "") == LBL_EM_COLOR
        ),
        None,
    )
    if not n_rgb:
        n_rgb = nodes.new("ShaderNodeRGB")
        n_rgb.label = LBL_EM_COLOR
    try:
        n_rgb.outputs[0].default_value = (*mat.skpbr.emission_color, 1.0)
    except Exception:
        pass
    color_out = n_rgb.outputs["Color"]
    if glow_tex_path:
        n_tex = nodes.new("ShaderNodeTexImage")
        n_tex.label = "Glow Map"
        try:
            _load_image(n_tex, glow_tex_path, "Non-Color")
        except Exception:
            pass
        n_add = next(
            (
                n
                for n in nodes
                if getattr(n, "type", "") == "MIX_RGB"
                and getattr(n, "label", "") == LBL_EM_ADD
            ),
            None,
        )
        if not n_add:
            n_add = nodes.new("ShaderNodeMixRGB")
            n_add.label = LBL_EM_ADD
            n_add.blend_type = "ADD"
            n_add.inputs[0].default_value = 1.0
        if not n_add.inputs[1].is_linked:
            links.new(n_rgb.outputs["Color"], n_add.inputs[1])
        if n_tex.image and not n_add.inputs[2].is_linked:
            links.new(n_tex.outputs["Color"], n_add.inputs[2])
        color_out = n_add.outputs["Color"]
    n_mul = next(
        (
            n
            for n in nodes
            if getattr(n, "type", "") == "MATH"
            and getattr(n, "label", "") == LBL_EM_STRENGTH
        ),
        None,
    )
    if not n_mul:
        n_mul = nodes.new("ShaderNodeMath")
        n_mul.operation = "MULTIPLY"
        n_mul.label = LBL_EM_STRENGTH
    if not n_mul.inputs[0].is_linked:
        links.new(color_out, n_mul.inputs[0])
    sval = (
        float(getattr(mat.skpbr, "emission_strength", 0.0))
        if bool(getattr(mat.skpbr, "emission_on", False))
        else 0.0
    )
    try:
        n_mul.inputs[1].default_value = sval
    except Exception:
        pass
    if not n_bsdf.inputs["Emission"].is_linked:
        links.new(n_mul.outputs[0], n_bsdf.inputs["Emission"])
    if "Emission Strength" in n_bsdf.inputs:
        try:
            n_bsdf.inputs["Emission Strength"].default_value = sval
        except Exception:
            pass


def _smp_apply_emissive_live(mat):
    if not mat or not getattr(mat, "node_tree", None) or not hasattr(mat, "skpbr"):
        return
    nt = mat.node_tree
    nodes = nt.nodes
    n_bsdf = _smp_find_bsdf(nt)
    n_rgb = next(
        (
            n
            for n in nodes
            if getattr(n, "type", "") == "RGB"
            and getattr(n, "label", "") == LBL_EM_COLOR
        ),
        None,
    )
    n_mul = next(
        (
            n
            for n in nodes
            if getattr(n, "type", "") == "MATH"
            and getattr(n, "label", "") == LBL_EM_STRENGTH
        ),
        None,
    )
    if n_rgb:
        try:
            n_rgb.outputs[0].default_value = (*mat.skpbr.emission_color, 1.0)
        except Exception:
            pass
    sval = (
        float(getattr(mat.skpbr, "emission_strength", 0.0))
        if bool(getattr(mat.skpbr, "emission_on", False))
        else 0.0
    )
    if n_mul:
        try:
            n_mul.inputs[1].default_value = sval
        except Exception:
            pass
    if n_bsdf and "Emission Strength" in n_bsdf.inputs:
        try:
            n_bsdf.inputs["Emission Strength"].default_value = sval
        except Exception:
            pass


# === SMP v202: Emissive helpers (auto-create Option A) ===
def _smp_find_node(nt, type_name=None, label=None):
    for n in nt.nodes:
        if (type_name is None or getattr(n, "type", "") == type_name) and (
            label is None or getattr(n, "label", "") == label
        ):
            return n
    return None


def _smp_get_material_output(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "OUTPUT_MATERIAL":
            return n
    return None


def _smp_find_bsdf(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "BSDF_PRINCIPLED":
            return n
    return None


def _smp_emission_detect_from_nif(mat):
    """Return (detected_bool, color_tuple, strength_float)."""
    try:
        col = None
        mul = None
        detected = False
        # common nif properties from pyNifly
        for key in ("emissive_color", "Emissive Color", "EmitColor", "EmissiveColor"):
            if hasattr(mat, "keys") and key in mat.keys():
                v = mat[key]
                if isinstance(v, (list, tuple)) and len(v) >= 3:
                    col = (float(v[0]), float(v[1]), float(v[2]))
        for key in (
            "emissive_multiple",
            "Emissive Multiple",
            "EmissiveMultiple",
            "emissiveMult",
        ):
            if hasattr(mat, "keys") and key in mat.keys():
                try:
                    mul = float(mat[key])
                except Exception:
                    pass
        # flags
        flags = ""
        for fkey in (
            "shader_flags_1",
            "Shader_Flags_1",
            "BSLighting_Shader_Flags_1",
            "shader_flags_2",
            "Shader_Flags_2",
            "BSLighting_Shader_Flags_2",
        ):
            if hasattr(mat, "keys") and fkey in mat.keys():
                try:
                    flags += " " + str(mat[fkey]).upper()
                except Exception:
                    pass
        if any(
            tok in flags
            for tok in (
                "OWN_EMIT",
                "EXTERNAL_EMITTANCE",
                "SOFT_LIGHTING",
                "EFFECT_LIGHTING",
                "GLOW",
            )
        ):
            detected = True
        # heuristic: non-black color also counts
        if col and any(c > 0.001 for c in col):
            detected = True
        if mul is None:
            mul = 1.0 if detected else 0.0
        if col is None:
            col = (1.0, 1.0, 1.0)
        return bool(detected), col, float(mul)
    except Exception:
        return False, (1.0, 1.0, 1.0), 0.0


def _smp_ensure_emission_chain_optionA(mat, force_on=None):
    """Ensure: RGB (UI color) -> Emission -> Mix with BSDF -> Output. Auto-create if missing.
    If force_on is not None, it overrides mat.skpbr.emission_on for building.
    """
    if not mat or not getattr(mat, "node_tree", None):
        return
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links
    n_out = _smp_get_material_output(nt)
    n_bsdf = _smp_find_bsdf(nt)
    if not n_out or not n_bsdf:
        return

    # Decide ON/OFF
    use_on = (
        bool(force_on)
        if force_on is not None
        else bool(getattr(getattr(mat, "skpbr", None), "emission_on", False))
    )

    # Color node (always present when we manage emissive)
    n_rgb = _smp_find_node(nt, "RGB", LBL_EM_COLOR)
    if not n_rgb:
        n_rgb = nodes.new("ShaderNodeRGB")
        n_rgb.label = LBL_EM_COLOR

    # Emission shader
    n_em = _smp_find_node(nt, "EMISSION", LBL_EMISSION_NODE)
    if not n_em:
        n_em = nodes.new("ShaderNodeEmission")
        n_em.label = LBL_EMISSION_NODE

    # Mix Shader
    n_mix = _smp_find_node(nt, "MIX_SHADER", LBL_EMISSION_MIX)
    if not n_mix:
        n_mix = nodes.new("ShaderNodeMixShader")
        n_mix.label = LBL_EMISSION_MIX

    # Ensure BSDF is wired into mix, and mix to output
    # Find current link from BSDF to Output and reroute through Mix if needed
    if n_out.inputs["Surface"].is_linked:
        src = n_out.inputs["Surface"].links[0].from_node
        if src is not n_mix:
            # Rewire: output.Surface <- mix
            links.new(n_mix.outputs["Shader"], n_out.inputs["Surface"])
            # Ensure BSDF is on input[1] of mix (base)
            links.new(n_bsdf.outputs["BSDF"], n_mix.inputs[1])
    else:
        links.new(n_mix.outputs["Shader"], n_out.inputs["Surface"])
        links.new(n_bsdf.outputs["BSDF"], n_mix.inputs[1])

    # Link color wheel to Emission color
    try:
        n_rgb.outputs[0].default_value = (
            *getattr(mat.skpbr, "emission_color", (1.0, 1.0, 1.0)),
            1.0,
        )
    except Exception:
        pass
    (
        links.new(n_rgb.outputs["Color"], n_em.inputs["Color"])
        if not n_em.inputs["Color"].is_linked
        else None
    )

    # Strength drives Emission Strength and Mix Factor (0=off, 1=full)
    sval = float(getattr(mat.skpbr, "emission_strength", 0.0)) if use_on else 0.0
    try:
        n_em.inputs["Strength"].default_value = sval
    except Exception:
        pass
    # Mix factor: use_on gates it; we can map strength -> factor for preview (clamp 0..1)
    try:
        n_mix.inputs["Fac"].default_value = 1.0 if use_on and sval > 0.0 else 0.0
    except Exception:
        pass

    # Connect emission into mix input[2]
    (
        links.new(n_em.outputs["Emission"], n_mix.inputs[2])
        if not n_mix.inputs[2].is_linked
        else None
    )


def _smp_apply_emission_live_optionA(mat):
    if not mat or not getattr(mat, "node_tree", None):
        return
    nt = mat.node_tree
    nodes = nt.nodes
    n_rgb = _smp_find_node(nt, "RGB", LBL_EM_COLOR)
    n_em = _smp_find_node(nt, "EMISSION", LBL_EMISSION_NODE)
    n_mix = _smp_find_node(nt, "MIX_SHADER", LBL_EMISSION_MIX)
    use_on = bool(getattr(getattr(mat, "skpbr", None), "emission_on", False))
    sval = float(getattr(mat.skpbr, "emission_strength", 0.0)) if use_on else 0.0

    if n_rgb:
        try:
            n_rgb.outputs[0].default_value = (*mat.skpbr.emission_color, 1.0)
        except Exception:
            pass
    if n_em:
        try:
            n_em.inputs["Strength"].default_value = sval
        except Exception:
            pass
    if n_mix:
        try:
            n_mix.inputs["Fac"].default_value = 1.0 if use_on and sval > 0.0 else 0.0
        except Exception:
            pass


# === v205 Emission helpers (always-present color, Option A auto-create) ===
def _v205_find(nt, type_id=None, label=None):
    for n in nt.nodes:
        if (type_id is None or getattr(n, "type", "") == type_id) and (
            label is None or getattr(n, "label", "") == label
        ):
            return n
    return None


def _v205_output(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "OUTPUT_MATERIAL":
            return n
    return None


def _v205_bsdf(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "BSDF_PRINCIPLED":
            return n
    return None


def _v205_emission_detect_from_nif(mat):
    """Placeholder for PyNifly integration; returns (detected, color, strength)."""
    try:
        # Try common custom properties that pyNifly may set
        col = None
        mul = None
        detected = False
        for key in ("Emissive Color", "emissive_color", "EmitColor", "EmissiveColor"):
            if hasattr(mat, "keys") and key in mat.keys():
                v = mat[key]
                if isinstance(v, (list, tuple)) and len(v) >= 3:
                    col = (float(v[0]), float(v[1]), float(v[2]))
        for key in (
            "Emissive Multiple",
            "emissive_multiple",
            "EmissiveMultiple",
            "emissiveMult",
        ):
            if hasattr(mat, "keys") and key in mat.keys():
                try:
                    mul = float(mat[key])
                except:
                    pass
        flags = ""
        for fkey in (
            "Shader_Flags_1",
            "shader_flags_1",
            "BSLighting_Shader_Flags_1",
            "Shader_Flags_2",
            "shader_flags_2",
        ):
            if hasattr(mat, "keys") and fkey in mat.keys():
                try:
                    flags += " " + str(mat[fkey]).upper()
                except:
                    pass
        if any(
            tok in flags
            for tok in (
                "OWN_EMIT",
                "EXTERNAL_EMITTANCE",
                "SOFT_LIGHTING",
                "EFFECT_LIGHTING",
            )
        ):
            detected = True
        if col and any(c > 0.001 for c in col):
            detected = True
        if col is None:
            col = (1.0, 1.0, 1.0)
        if mul is None:
            mul = 1.0 if detected else 0.0
        return bool(detected), col, float(mul)
    except Exception:
        return False, (1.0, 1.0, 1.0), 0.0


def _v205_emission_ensure_chain(mat, force_on=None):
    """Ensure: RGB(color) -> Emission -> Mix -> BSDF -> Output.
    Always create RGB node and hook it; emission On/Off gates strength/fac.
    """
    if not mat or not getattr(mat, "node_tree", None):
        return
    nt = mat.node_tree
    nodes = nt.nodes
    links = nt.links
    out = _v205_output(nt)
    bsdf = _v205_bsdf(nt)
    if not out or not bsdf:
        return

    # Create/find nodes
    rgb = _v205_find(nt, "RGB", LBL_EM_COLOR)
    if not rgb:
        rgb = nodes.new("ShaderNodeRGB")
        rgb.label = LBL_EM_COLOR
        rgb.location = (bsdf.location.x - 500, bsdf.location.y - 200)

    em = _v205_find(nt, "EMISSION", LBL_EM_NODE)
    if not em:
        em = nodes.new("ShaderNodeEmission")
        em.label = LBL_EM_NODE
        em.location = (bsdf.location.x - 250, bsdf.location.y - 150)

    mix = _v205_find(nt, "MIX_SHADER", LBL_EM_MIX)
    if not mix:
        mix = nodes.new("ShaderNodeMixShader")
        mix.label = LBL_EM_MIX
        mix.location = (bsdf.location.x + 200, bsdf.location.y)

    # Rewire Surface through mix once
    if out.inputs["Surface"].is_linked:
        if out.inputs["Surface"].links[0].from_node is not mix:
            for l in list(links):
                if l.to_node == out and l.to_socket.name == "Surface":
                    links.remove(l)
            links.new(mix.outputs["Shader"], out.inputs["Surface"])
    else:
        links.new(mix.outputs["Shader"], out.inputs["Surface"])

    # Ensure BSDF base into mix
    if not mix.inputs[1].is_linked:
        links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    # Ensure RGB -> Emission color
    if not em.inputs["Color"].is_linked:
        links.new(rgb.outputs["Color"], em.inputs["Color"])
    # Ensure Emission -> mix shader 2
    if not mix.inputs[2].is_linked:
        links.new(em.outputs["Emission"], mix.inputs[2])

    # Apply values
    sk = getattr(mat, "skpbr", None)
    if sk:
        try:
            rgb.outputs[0].default_value = (
                *getattr(sk, "emission_color", (1.0, 1.0, 1.0)),
                1.0,
            )
        except:
            pass
        use_on = (
            bool(getattr(sk, "emission_on", False))
            if force_on is None
            else bool(force_on)
        )
        sval = float(getattr(sk, "emission_strength", 0.0)) if use_on else 0.0
    else:
        use_on = False
        sval = 0.0
    try:
        em.inputs["Strength"].default_value = sval
    except:
        pass
    try:
        mix.inputs["Fac"].default_value = 1.0 if (use_on and sval > 0.0) else 0.0
    except:
        pass


def _v205_emission_live(mat):
    """Live sync color & strength to nodes (assumes chain exists)."""
    if not mat or not getattr(mat, "node_tree", None):
        return
    nt = mat.node_tree
    nodes = nt.nodes
    rgb = _v205_find(nt, "RGB", LBL_EM_COLOR)
    em = _v205_find(nt, "EMISSION", LBL_EM_NODE)
    mix = _v205_find(nt, "MIX_SHADER", LBL_EM_MIX)
    sk = getattr(mat, "skpbr", None)
    if not sk:
        return
    # color
    if rgb:
        try:
            rgb.outputs[0].default_value = (*sk.emission_color, 1.0)
        except:
            pass
    sval = float(sk.emission_strength) if sk.emission_on else 0.0
    if em:
        try:
            em.inputs["Strength"].default_value = sval
        except:
            pass
    if mix:
        try:
            mix.inputs["Fac"].default_value = 1.0 if sval > 0.0 else 0.0
        except:
            pass


# ================== SMP v206 Append: UI cleanup + emissive hookup ==================
# This block does NOT modify existing classes; it appends helpers and replaces the
# main panel's draw() to avoid duplicate UI while keeping the rest of the addon intact.

import bpy

# Labels reused from the main script (fallbacks if not defined)
try:
    LBL_EM_COLOR
except NameError:
    LBL_EM_COLOR = "Emissive Color"
try:
    LBL_BSDF
except NameError:
    LBL_BSDF = "Principled BSDF"

LBL_EM_NODE = "SMP Emission"
LBL_EM_MIX = "SMP Emission Mix"


def _smp_n_find(nt, type_id=None, label=None):
    for n in nt.nodes:
        if (type_id is None or getattr(n, "type", "") == type_id) and (
            label is None or getattr(n, "label", "") == label
        ):
            return n
    return None


def _smp_n_out(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "OUTPUT_MATERIAL":
            return n
    return None


def _smp_n_bsdf(nt):
    for n in nt.nodes:
        if getattr(n, "type", "") == "BSDF_PRINCIPLED":
            return n
    return None


def _smp_ensure_emissive_chain(mat):
    if not mat or not getattr(mat, "use_nodes", False):
        return
    nt = mat.node_tree
    if not nt:
        return
    out = _smp_n_out(nt)
    bsdf = _smp_n_bsdf(nt)
    if not out or not bsdf:
        return
    links = nt.links

    rgb = _smp_n_find(nt, "RGB", LBL_EM_COLOR)
    if not rgb:
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.label = LBL_EM_COLOR
        rgb.location = (bsdf.location.x - 500, bsdf.location.y - 200)

    emis = _smp_n_find(nt, "EMISSION", LBL_EM_NODE)
    if not emis:
        emis = nt.nodes.new("ShaderNodeEmission")
        emis.label = LBL_EM_NODE
        emis.location = (bsdf.location.x - 250, bsdf.location.y - 150)

    mix = _smp_n_find(nt, "MIX_SHADER", LBL_EM_MIX)
    if not mix:
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.label = LBL_EM_MIX
        mix.location = (bsdf.location.x + 200, bsdf.location.y)

    # Wire: BSDF -> Mix[1], Emission -> Mix[2], Mix -> Output
    if not mix.inputs[1].is_linked:
        links.new(bsdf.outputs["BSDF"], mix.inputs[1])
    if not emis.inputs["Color"].is_linked:
        links.new(rgb.outputs["Color"], emis.inputs["Color"])
    if not mix.inputs[2].is_linked:
        links.new(emis.outputs["Emission"], mix.inputs[2])
    if out.inputs["Surface"].is_linked:
        if out.inputs["Surface"].links[0].from_node is not mix:
            # replace existing
            for l in list(links):
                if l.to_node == out and l.to_socket.name == "Surface":
                    links.remove(l)
            links.new(mix.outputs["Shader"], out.inputs["Surface"])
    else:
        links.new(mix.outputs["Shader"], out.inputs["Surface"])

    # Drive from mat.skpbr (support both emission_* and emissive_* names)
    on = False
    strength = 0.0
    color = (1.0, 1.0, 1.0)
    sk = getattr(mat, "skpbr", None)
    if sk is not None:
        on = bool(getattr(sk, "emission_on", getattr(sk, "emissive_on", False)))
        strength = float(
            getattr(sk, "emission_strength", getattr(sk, "emissive_strength", 0.0))
        )
        col = getattr(sk, "emission_color", getattr(sk, "emissive_color", None))
        if isinstance(col, (list, tuple)) and len(col) >= 3:
            color = (float(col[0]), float(col[1]), float(col[2]))

    try:
        rgb.outputs[0].default_value = (color[0], color[1], color[2], 1.0)
    except Exception:
        pass
    val = strength if (on and strength > 0.0) else 0.0
    try:
        emis.inputs["Strength"].default_value = val
    except Exception:
        pass
    try:
        mix.inputs["Fac"].default_value = 1.0 if val > 0.0 else 0.0
    except Exception:
        pass


# -------- Replace main panel draw to remove duplicates & ensure wiring --------
try:
    _SKPANEL = bpy.types.SKPBR_PT_UI
    _ORIG_DRAW = _SKPANEL.draw
except Exception:
    _SKPANEL = None
    _ORIG_DRAW = None


def _SMP_v206_draw(self, context):
    # Render original UI
    if _ORIG_DRAW is not None:
        _ORIG_DRAW(self, context)

    # Now fix duplicates by re-drawing a clean, minimal section at the end (optional)
    # and ensure emissive chain is wired every time the panel draws.
    try:
        mat = context.object.active_material if context.object else None
        _smp_ensure_emissive_chain(mat)
    except Exception:
        pass

    # Remove duplicate _m toggle visually isn’t possible post-draw,
    # so we rely on the fact that the original panel only *shows* one hereafter.
    # (If your original panel draws two, comment out its second occurrence in your base file.)


if _SKPANEL is not None:
    _SKPANEL.draw = _SMP_v206_draw

# ================== /SMP v206 Append ==================


# ======================= Alpha helpers (added) =======================

# [AlphaMix ensure function removed]

# [AlphaMix removed]


def _smp_selected_materials(include_active=True):
    """Yield unique materials from all selected mesh objects (optionally include active mat)."""
    import bpy

    seen = set()
    mats = []
    ctx = bpy.context
    if include_active:
        mat = _get_active_material(ctx)
        if mat and id(mat) not in seen:
            mats.append(mat)
            seen.add(id(mat))
    for obj in getattr(ctx, "selected_objects", []) or []:
        try:
            if getattr(obj, "type", "") != "MESH":
                continue
            for slot in getattr(obj, "material_slots", []) or []:
                mat = getattr(slot, "material", None)
                if mat and id(mat) not in seen:
                    mats.append(mat)
                    seen.add(id(mat))
        except Exception:
            pass
    return mats


def _smp_set_prop_if_exists(mat, prop_name, value):
    """Set skpbr.prop_name on mat if present, guarding against re-entrant updates."""
    import bpy

    global _SMP_PROP_SYNC_GUARD
    try:
        s = getattr(mat, "skpbr", None)
        if not s or not hasattr(s, prop_name):
            return
        # Avoid redundant sets that would retrigger updates
        try:
            cur = getattr(s, prop_name)
            if isinstance(cur, float):
                # tolerate tiny differences
                if abs(float(cur) - float(value)) < 1e-9:
                    return
            elif (
                isinstance(cur, (tuple, list))
                and isinstance(value, (tuple, list))
                and len(cur) == len(value)
            ):
                if all(abs(float(a) - float(b)) < 1e-9 for a, b in zip(cur, value)):
                    return
            elif cur == value:
                return
        except Exception:
            pass
        _SMP_PROP_SYNC_GUARD = True
        setattr(s, prop_name, value)
    except Exception:
        pass
    finally:
        _SMP_PROP_SYNC_GUARD = False


def _on_alpha_strength_changed(s):
    """Called when the Alpha Strength slider changes (live preview + sync) across selection."""
    global _SMP_PROP_SYNC_GUARD
    if _SMP_PROP_SYNC_GUARD:
        return
    try:
        import bpy

        val = float(getattr(s, "alpha_strength", 1.0))
        for m in _smp_selected_materials(include_active=True):
            try:
                m["skpbr_alpha_strength"] = val
            except Exception:
                pass
            _smp_set_prop_if_exists(m, "alpha_strength", val)
            # Find Base texture for correct wiring
            base_tex_node = None
            if getattr(m, "node_tree", None):
                nt = m.node_tree
                for n in nt.nodes:
                    if getattr(n, "type", "") == "TEX_IMAGE" and getattr(
                        n, "label", ""
                    ).lower().startswith("base"):
                        base_tex_node = n
                        break
                _apply_alpha_logic(m, base_tex_node)
    except Exception as e:
        print(f"[SMP] Alpha strength update failed: {e}")


def _extract_alpha_from_nif_for_object(obj) -> dict | None:
    """
    Ask nifparser.parse_nif_alpha for this object, using VFS if present.
    Returns {'mode': 'BLEND'|'CLIP'|'NONE', 'threshold': float|None} or None.
    """
    try:
        if not obj:
            return None
        nif_path = getattr(obj, "nif_path", None)

        # Try to resolve a NIF path via custom props if not set
        if not nif_path and hasattr(obj, "keys"):
            for k in obj.keys():
                try:
                    v = str(obj[k])
                except Exception:
                    continue
                if v.lower().endswith(".nif"):
                    nif_path = v
                    break

        if not nif_path:
            return None

        info = None
        # Prefer VFS-read if available
        if _smp_vfs_exists(nif_path):
            with _smp_open(nif_path, "rb") as f:
                data = f.read()
            if hasattr(nifparser, "parse_nif_alpha"):
                info = nifparser.parse_nif_alpha(
                    data, match_name=getattr(obj, "name", None)
                )
        elif os.path.exists(nif_path) and hasattr(nifparser, "parse_nif_alpha"):
            info = nifparser.parse_nif_alpha(
                nif_path, match_name=getattr(obj, "name", None)
            )

        return info if info else None
    except Exception as e:
        _log(f"[NIF Alpha] read failed: {e}")
        return None


def _image_has_soft_alpha(img) -> bool:
    """Heuristic to pick BLEND vs CLIP from texture alpha when NIF data is missing."""
    try:
        if not img:
            return False
        if not getattr(img, "has_data", True):
            img.reload()
        a = img.pixels[3::4]
        if not a:
            return False
        step = max(1, len(a) // 4096)
        return any(0.0 < a[i] < 1.0 for i in range(0, len(a), step))
    except Exception:
        return False


def _init_alpha_from_nif(mat: bpy.types.Material):
    """Store the NIF-driven mode on the material for preview/exporters."""
    try:
        user_obj = None
        for ob in bpy.data.objects:
            if ob.type != "MESH":
                continue
            for slot in ob.material_slots:
                if slot.material is mat:
                    user_obj = ob
                    break
            if user_obj:
                break

        mode, thr = "NONE", None
        info = _extract_alpha_from_nif_for_object(user_obj) if user_obj else None
        if info:
            mode = str(info.get("mode", "NONE")).upper()
            thr = info.get("threshold", None)

        mat["skpbr_alpha_mode"] = mode
        try:
            mat["skpbr_alpha_strength"] = (
                float(thr) if (thr is not None and 0.0 <= float(thr) <= 1.0) else 1.0
            )
        except Exception:
            pass
        if thr is not None:
            try:
                mat["skpbr_alpha_threshold"] = float(thr)
            except Exception:
                pass
    except Exception as e:
        _log(f"[NIF Alpha] init failed: {e}")


def _apply_alpha_logic(mat, base_tex_node=None):
    # Ensure ALPHAGOOD-style alpha wiring: Base Alpha -> (Multiply 'Alpha Strength') -> BSDF Alpha; auto blend/shadow.
    try:
        import bpy

        nt = getattr(mat, "node_tree", None)
        if not nt:
            return
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if not bsdf:
            return
        # Find Base texture node by label if not provided
        if not base_tex_node:
            for n in nt.nodes:
                if (
                    getattr(n, "type", "") == "TEX_IMAGE"
                    and "base" in (getattr(n, "label", "") or "").lower()
                ):
                    base_tex_node = n
                    break
        if not base_tex_node:
            return

        s = getattr(mat, "skpbr", None)
        strength = float(getattr(s, "alpha_strength", 1.0)) if s else 1.0
        links = nt.links

        # Find existing Multiply node (labeled)
        mul = None
        for n in nt.nodes:
            if n.type == "MATH" and getattr(n, "label", "") == "Alpha Strength":
                mul = n
                break

        def safe_unlink_out(sock):
            for l in list(getattr(sock, "links", [])):
                try:
                    nt.links.remove(l)
                except Exception:
                    pass

        if strength < 0.999:
            if not mul:
                mul = nt.nodes.new("ShaderNodeMath")
                mul.operation = "MULTIPLY"
                mul.label = "Alpha Strength"
                mul.name = "Alpha Strength"
                mul.location = (
                    base_tex_node.location.x + 220,
                    base_tex_node.location.y - 120,
                )
                # connect Base Alpha to mul[0], mul -> BSDF Alpha
                try:
                    links.new(base_tex_node.outputs["Alpha"], mul.inputs[0])
                except Exception:
                    pass
                try:
                    # Remove any direct Base->BSDF Alpha links to avoid doubles
                    try:
                        for l in list(bsdf.inputs["Alpha"].links):
                            nt.links.remove(l)
                    except Exception:
                        pass
                    links.new(mul.outputs[0], bsdf.inputs["Alpha"])
                except Exception:
                    pass
            # Update strength factor
            try:
                mul.inputs[1].default_value = strength
            except Exception:
                pass
        else:
            # Full opacity -> remove multiply node and connect directly
            if mul:
                try:
                    safe_unlink_out(mul.outputs[0])
                    nt.nodes.remove(mul)
                except Exception:
                    pass
            # Ensure at least a direct link exists
            direct = any(
                l.to_node == bsdf
                and getattr(l, "to_socket", None)
                and getattr(l.to_socket, "name", "") == "Alpha"
                for l in nt.links
            )
            if not direct:
                try:
                    links.new(base_tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
                except Exception:
                    pass

        # Adjust viewport/material alpha modes
        try:
            mat.blend_method = "BLEND" if strength < 1.0 else "OPAQUE"
        except Exception:
            pass
        try:
            mat.shadow_method = "HASHED" if strength < 1.0 else "OPAQUE"
        except Exception:
            pass

    except Exception as e:
        print(
            f"[SMP] _apply_alpha_logic failed for {getattr(mat, 'name', '<mat>')}: {e}"
        )

    """
    Apply Blender preview consistent with Skyrim:
      NONE  -> OPAQUE
      CLIP  -> CLIP
      BLEND -> BLEND (HASHED shadows)
    Then wire Base Alpha → BSDF Alpha if present, with optional Alpha Strength multiply.
    """
    if not mat or not mat.node_tree:
        return
    nt = mat.node_tree
    links = nt.links

    # Find Principled BSDF
    bsdf = None
    for n in nt.nodes:
        if (
            getattr(n, "type", "") == "BSDF_PRINCIPLED"
            or getattr(n, "label", "") == "Principled BSDF"
        ):
            bsdf = n
            break
    if not bsdf or not bsdf.inputs.get("Alpha"):
        try:
            mat.blend_method = "OPAQUE"
            mat.shadow_method = "OPAQUE"
        except Exception:
            pass
        return

    # Decide mode
    mode = str(mat.get("skpbr_alpha_mode", "NONE")).upper()
    if mode == "NONE" and base_tex_node and getattr(base_tex_node, "image", None):
        mode = "BLEND" if _image_has_soft_alpha(base_tex_node.image) else "CLIP"

    # Apply Blender preview modes
    try:
        if mode == "BLEND":
            mat.blend_method = "BLEND"
            mat.shadow_method = "HASHED"
        elif mode == "CLIP":
            mat.blend_method = "CLIP"
            mat.shadow_method = "CLIP"
        else:
            mat.blend_method = "OPAQUE"
            mat.shadow_method = "OPAQUE"
    except Exception:
        pass

    # Wire Base Alpha → BSDF Alpha
    try:
        for lk in list(bsdf.inputs["Alpha"].links):
            links.remove(lk)
        if base_tex_node and base_tex_node.outputs.get("Alpha"):
            links.new(base_tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
    except Exception as e:
        _log(f"[NIF Alpha] rewire failed: {e}")

    # Optional Alpha Strength multiplier (preview only)
    try:
        strength = 1.0
        if hasattr(mat, "keys") and "skpbr_alpha_strength" in mat:
            strength = float(mat["skpbr_alpha_strength"])
        if strength != 1.0 and base_tex_node and base_tex_node.outputs.get("Alpha"):
            # Reuse/create a Math(MULTIPLY) node labeled 'Alpha Strength'
            mult = None
            for n in nt.nodes:
                if (
                    getattr(n, "label", "") == "Alpha Strength"
                    and getattr(n, "bl_idname", "") == "ShaderNodeMath"
                ):
                    mult = n
                    break
            if mult is None:
                mult = nt.nodes.new("ShaderNodeMath")
                mult.operation = "MULTIPLY"
                mult.label = "Alpha Strength"
                mult.location = (bsdf.location.x - 240, bsdf.location.y - 220)
            mult.inputs[1].default_value = strength
            # Reroute through multiplier
            for lk in list(bsdf.inputs["Alpha"].links):
                links.remove(lk)
            links.new(base_tex_node.outputs["Alpha"], mult.inputs[0])
            links.new(mult.outputs[0], bsdf.inputs["Alpha"])
    except Exception as e:
        _log(f"[NIF Alpha] strength apply failed: {e}")


# === End injected ===


# === Helper for ALPHA preview ===


def _alpha_preview_refresh(mat):
    """Refresh alpha preview: read NIF alpha (mode/threshold) and wire Base alpha to BSDF Alpha with optional strength."""
    try:
        _init_alpha_from_nif(mat)
    except Exception:
        pass
    try:
        nt = getattr(mat, "node_tree", None)
        base_tex = _find_node_by_label(nt, LBL_BASE) if nt else None
    except Exception:
        base_tex = None
    try:
        _apply_alpha_logic(mat, base_tex)
    except Exception:
        pass
