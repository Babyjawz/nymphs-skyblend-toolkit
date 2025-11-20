from pynifly import NifFile
import os

# -----------------------------------------------------------
# LEGACY: PyNifly-based emissive patcher (unused, replaced by patch_emissive.py)
# -----------------------------------------------------------

# # === CONFIGURATION ===
# input_dir  = r"D:\Nymphs\mods\Skyrim Fantasy Overhaul - Base Object Swapper\meshes\architecture\winterhold\WinterholdTGC"
# output_dir = r"D:\Nymphs\mods\Nymphs -Glowmaps\meshes\architecture\winterhold"

# # New emissive settings (values in RGB 0–255)
# EMISSIVE_COLOR = (250, 212, 166)
# EMISSIVE_MULT  = 0.7

# # === PATCH FUNCTION ===
# def patch_emissive(nif_path, out_path):
#     """Loads a NIF, patches emissive color/multiplier, and saves to out_path."""
#     try:
#         nif = NifFile()       # Create instance
#         nif.Load(nif_path)    # Load NIF file using PyNifly
#     except Exception as e:
#         print(f"❌ Failed to load {nif_path}: {e}")
#         return

#     changed = False
#     for block in nif.blocks:
#         if block.name == "BSLightingShaderProperty":
#             try:
#                 if hasattr(block, "emissive_color"):
#                     block.emissive_color = EMISSIVE_COLOR
#                 if hasattr(block, "emissive_mult"):
#                     block.emissive_mult = EMISSIVE_MULT
#                 elif hasattr(block, "emissive_multiple"):
#                     block.emissive_multiple = EMISSIVE_MULT
#                 changed = True
#             except Exception as e:
#                 print(f"⚠️ Could not modify emissive in {nif_path}: {e}")

#     if changed:
#         try:
#             nif.Save(out_path)
#             print(f"✅ Patched {os.path.basename(nif_path)}")
#         except Exception as e:
#             print(f"❌ Failed to save {os.path.basename(nif_path)}: {e}")
#     else:
#         print(f"— No BSLightingShaderProperty found in {os.path.basename(nif_path)}")


# === MAIN FUNCTION ===
def main():
    os.makedirs(output_dir, exist_ok=True)
    nifs = [f for f in os.listdir(input_dir) if f.lower().endswith(".nif")]

    if not nifs:
        print(f"⚠️ No .nif files found in {input_dir}")
        return

    for fname in nifs:
        in_path = os.path.join(input_dir, fname)
        out_path = os.path.join(output_dir, fname)
        patch_emissive(in_path, out_path)

    print("\n🎉 Done! All NIFs processed.\n")


if __name__ == "__main__":
    main()


# === Alpha Reader ===
def parse_nif_alpha(nif_path_or_bytes, match_name=None):
    """
    Best-effort: read NiAlphaProperty / BSLightingShaderProperty transparency.
    Returns dict: {'mode': 'CLIP'|'BLEND'|'NONE', 'threshold': float|None}
    """
    try:
        from pynifly import NifFile
        import io, os

        nf = NifFile()
        if isinstance(nif_path_or_bytes, (bytes, bytearray)):
            # Load from bytes using a stream if available
            if hasattr(nf, "LoadStream"):
                nf.LoadStream(io.BytesIO(nif_path_or_bytes))
            else:
                # Fallback: write temp to disk (avoid if possible)
                tmp = os.path.join(os.getcwd(), "_tmp_alpha_read.nif")
                with open(tmp, "wb") as f:
                    f.write(nif_path_or_bytes)
                nf.Load(tmp)
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        else:
            nf.Load(nif_path_or_bytes)

        # Iterate over blocks, find the one that matches the object (if provided)
        # and read alpha flags.
        def _blk_name(b):
            return str(getattr(b, "name", "") or getattr(b, "block_name", "")).lower()

        wanted = (match_name or "").lower() if match_name else None

        mode = None
        threshold = None

        for blk in getattr(nf, "blocks", []):
            nm = _blk_name(blk)

            if wanted and wanted not in nm:
                continue  # not our piece

            cls = blk.__class__.__name__

            # NiAlphaProperty-style
            if "Alpha" in cls or "NiAlphaProperty" in cls or hasattr(blk, "flags"):
                flags = int(getattr(blk, "flags", 0))
                test_enabled = bool(flags & 0x1)  # engine-specific: test bit
                blend_enabled = bool(flags & 0x2)  # engine-specific: blend bit
                threshold = getattr(blk, "threshold", None)
                if blend_enabled:
                    mode = "BLEND"
                elif test_enabled:
                    mode = "CLIP"
                else:
                    mode = "NONE"
                break

            # BSLightingShaderProperty alpha fallback
            if "BSLightingShaderProperty" in cls:
                a = getattr(blk, "alpha", None)
                if isinstance(a, (int, float)) and a < 1.0:
                    mode = "BLEND"
                    threshold = float(a)
                    break

        if not mode:
            return {"mode": "NONE"}
        return {"mode": mode, "threshold": threshold}

    except Exception as e:
        print(f"[nifparser] parse_nif_alpha failed: {e}")
        return None


# === Emissive Reader ===
def parse_nif_emissives(nif_path_or_bytes, match_name=None):
    """
    Read emissive color (RGB) and strength from BSLightingShaderProperty blocks.
    Returns dict like: {'em_color': (r,g,b), 'em_strength': float} or None if not found.
    - Accepts either a filesystem path or raw bytes.
    - If match_name is provided, try to pick a block whose name contains that token.
    """
    try:
        from pynifly import NifFile
        import io, os

        nf = NifFile()
        # Load from bytes or path
        if isinstance(nif_path_or_bytes, (bytes, bytearray)):
            if hasattr(nf, "LoadStream"):
                nf.LoadStream(io.BytesIO(nif_path_or_bytes))
            else:
                tmp = "_tmp_emissive_read.nif"
                with open(tmp, "wb") as f:
                    f.write(nif_path_or_bytes)
                nf.Load(tmp)
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        else:
            nf.Load(nif_path_or_bytes)

        want = (match_name or "").lower() if match_name else None

        # Helpers to normalize possibly 0-255 tuples to 0-1 floats
        def _norm_color(c):
            if not isinstance(c, (list, tuple)) or len(c) < 3:
                return None
            mx = max(float(c[0]), float(c[1]), float(c[2]))
            if mx > 1.5:
                return (float(c[0]) / 255.0, float(c[1]) / 255.0, float(c[2]) / 255.0)
            return (float(c[0]), float(c[1]), float(c[2]))

        best = None

        for b in getattr(nf, "blocks", []):
            try:
                cls = b.__class__.__name__
                if "BSLightingShaderProperty" not in cls:
                    continue

                nm = str(getattr(b, "name", "") or getattr(b, "block_name", "")).lower()
                # Gather candidates; name match will be scored later
                col = None
                mul = None

                # Common field names across different PyNifly builds
                for key in (
                    "emissive_color",
                    "emissiveColor",
                    "emissive",
                    "emit_color",
                    "EmitColor",
                ):
                    if hasattr(b, key):
                        col = getattr(b, key)
                        break

                for key in (
                    "emissive_mult",
                    "emissive_multiple",
                    "emissiveMultiple",
                    "emissiveMult",
                    "EmitMultiple",
                ):
                    if hasattr(b, key):
                        try:
                            mul = float(getattr(b, key))
                        except Exception:
                            mul = None
                        break

                if (
                    col is None
                    and hasattr(b, "emissive")
                    and isinstance(getattr(b, "emissive"), (list, tuple))
                ):
                    col = getattr(b, "emissive")

                ncol = _norm_color(col) if col is not None else None

                # Scoring: prefer a name match if 'match_name' was provided; then prefer with both fields
                score = 0
                if want and want in nm:
                    score += 10
                if ncol is not None:
                    score += 2
                if mul is not None:
                    score += 1

                if ncol is not None or mul is not None:
                    item = {
                        "em_color": ncol if ncol is not None else (0.0, 0.0, 0.0),
                        "em_strength": float(mul) if mul is not None else 0.0,
                    }
                    if best is None or score > best[0]:
                        best = (score, item)

            except Exception:
                # Skip malformed blocks
                continue

        return best[1] if best else None

    except Exception as e:
        print(f"[nifparser] parse_nif_emissives failed: {e}")
        return None
