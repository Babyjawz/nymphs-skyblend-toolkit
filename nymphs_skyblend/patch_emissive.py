# ============================================================
# Load bundled PyFFI safely (no global version expected)
# ============================================================
import importlib.util, os, sys

addon_dir = os.path.dirname(__file__)
local_pyffi = os.path.join(addon_dir, "thirdparty", "pyffi")

# Temporarily insert our bundled path
old_sys_path = sys.path.copy()
sys.path.insert(0, local_pyffi)

# Load the bundled __init__.py directly
spec = importlib.util.spec_from_file_location(
    "pyffi", os.path.join(local_pyffi, "__init__.py")
)
pyffi_mod = importlib.util.module_from_spec(spec)
sys.modules["pyffi"] = pyffi_mod
spec.loader.exec_module(pyffi_mod)

# Restore sys.path
sys.path = old_sys_path

# Try importing NifFormat normally from the bundled PyFFI
try:
    from pyffi.formats.nif import NifFormat

    print(f"[SMP] Bundled PyFFI loaded successfully from: {pyffi_mod.__file__}")
except Exception as e:
    raise RuntimeError(f"[SMP ERROR] Failed to load NifFormat from bundled PyFFI: {e}")

# ============================================================
# Configuration defaults (can be overridden by UI inputs)
# ============================================================


# Default emissive settings — used if not overridden from UI
EMISSIVE_MULTIPLE = 0.7
EMISSIVE_COLOR = (1.0, 0.9, 0.75)  # warm tint

# Optional filters and behavior flags
WINDOW_KEYWORDS = ("window", "_g.dds", "_emit", "_glow", "_light")
RECURSIVE = True
OVERWRITE = True

# ----------------------------------------


def is_window_material(block):
    """Return True if the shader's textures suggest it's a window/glow material."""
    texset = getattr(block, "texture_set", None)
    if not texset or not hasattr(texset, "textures"):
        return False
    for tex in texset.textures:
        if not tex:
            continue
        try:
            t = tex.decode("utf-8").lower()
            if any(k in t for k in WINDOW_KEYWORDS):
                return True
        except Exception:
            pass
    return False


def process_nif(in_path, out_path):
    """Load, modify, and save one NIF file."""
    modified = 0
    try:
        data = NifFormat.Data()
        with open(in_path, "rb") as f:
            data.read(f)

        for block in data.roots:
            for sub in block.tree():
                if sub.__class__.__name__ == "BSLightingShaderProperty":
                    if not is_window_material(sub):
                        continue
                    sub.emissive_color.r, sub.emissive_color.g, sub.emissive_color.b = (
                        EMISSIVE_COLOR
                    )
                    sub.emissive_multiple = EMISSIVE_MULTIPLE
                    modified += 1

        if modified == 0:
            return False

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        tmp_path = out_path + ".tmp"
        with open(tmp_path, "wb") as f:
            data.write(f)
        os.replace(tmp_path, out_path)

        print(f"✅ Saved {os.path.basename(out_path)} — modified {modified} shader(s).")
        return True

    except Exception as e:
        print(f"❌ Failed: {in_path}")
        traceback.print_exc()
        return False


def main():
    print(f"🔍 Scanning {INPUT_DIR}\n")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    total = 0
    changed = 0

    for root, _, files in os.walk(INPUT_DIR):
        for name in files:
            if not name.lower().endswith(".nif"):
                continue
            total += 1
            in_path = os.path.join(root, name)
            rel_path = os.path.relpath(in_path, INPUT_DIR)
            out_path = os.path.abspath(os.path.join(OUTPUT_DIR, rel_path))

            if not OVERWRITE and os.path.exists(out_path):
                continue

            if process_nif(in_path, out_path):
                changed += 1

        if not RECURSIVE:
            break

    print(f"\n✅ Done — patched {changed}/{total} NIFs.")
    print(f"📂 Output: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
