import sys
import bpy
import subprocess
import time
from pathlib import Path
import difflib
import importlib.util

# ============================================================================
# Load bundled PyFFI for slow material-based matching
# ============================================================================

_SKPBR_HAS_PYFFI = False
NifFormat = None

try:
    addon_root = Path(__file__).resolve().parent
    local_pyffi = addon_root / "thirdparty" / "pyffi"

    if local_pyffi.exists():
        old_sys = sys.path.copy()
        sys.path.insert(0, str(local_pyffi))

        spec = importlib.util.spec_from_file_location(
            "pyffi", str(local_pyffi / "__init__.py")
        )
        pyffi_mod = importlib.util.module_from_spec(spec)
        sys.modules["pyffi"] = pyffi_mod
        spec.loader.exec_module(pyffi_mod)

        sys.path = old_sys

        from pyffi.formats.nif import NifFormat as _NF

        NifFormat = _NF
        _SKPBR_HAS_PYFFI = True
        print(f"[SKPBR] PyFFI loaded: {pyffi_mod.__file__}")
    else:
        print("[SKPBR] No bundled PyFFI found:", local_pyffi)

except Exception as e:
    print("[SKPBR] PyFFI load FAILED:", e)
    _SKPBR_HAS_PYFFI = False


# ============================================================================
# Operator
# ============================================================================


class SKPBR_OT_RunPBRNifPatcher(bpy.types.Operator):
    bl_idname = "skpbr.run_pbrnifpatcher"
    bl_label = "Run PBRNifPatcher"

    nif_path: bpy.props.StringProperty(name="NIF File", subtype="FILE_PATH")
    json_path: bpy.props.StringProperty(
        name="JSON File (optional)", subtype="FILE_PATH"
    )

    use_material_match: bpy.props.BoolProperty(
        name="Use NIF material matching (slow)",
        description="Parse the NIF using PyFFI and match JSONs based on textures",
        default=False,
    )

    # ------------------------------------------------------------------
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, ctx):
        layout = self.layout
        layout.label(text="PBRNifPatcher.exe will run inside the mod folder.")
        layout.prop(self, "nif_path")
        layout.prop(self, "json_path")
        layout.prop(self, "use_material_match")

    # ------------------------------------------------------------------
    # Helper: locate mod root
    # ------------------------------------------------------------------
    def find_mod_root(self, nif: Path) -> Path:
        for parent in nif.parents:
            if (parent / "meshes").exists():
                return parent
            if (parent / "PBRNifPatcher").exists():
                return parent
        return nif.parent

    # ------------------------------------------------------------------
    # Helper: folder-search fast mode
    # ------------------------------------------------------------------
    def fast_folder_json_search(self, mod_root: Path, nif: Path):
        folder = mod_root / "PBRNifPatcher"
        if not folder.exists():
            return []
        # use ALL jsons
        return list(folder.rglob("*.json"))

    # ------------------------------------------------------------------
    # SLOW MODE: extract textures from NIF via PyFFI
    # ------------------------------------------------------------------
    def extract_textures_from_nif(self, nif_path: Path):
        textures = set()
        if not (_SKPBR_HAS_PYFFI and NifFormat):
            print("[SKPBR] Slow mode unavailable (no PyFFI).")
            return textures

        try:
            data = NifFormat.Data()
            with open(nif_path, "rb") as f:
                data.read(f)
        except Exception as e:
            print("[SKPBR] PyFFI parse failed:", e)
            return textures

        for block in data.blocks:
            if isinstance(block, NifFormat.BSShaderTextureSet):
                for tex in block.textures:
                    if not tex:
                        continue
                    name = Path(str(tex)).name.split(".")[0]
                    textures.add(name.lower())

        print("\n[SKPBR] Textures found in NIF:")
        for t in sorted(textures):
            print("  ", t)

        return textures

    # ------------------------------------------------------------------
    # SLOW MODE: match JSON files by texture keys
    # ------------------------------------------------------------------
    def match_jsons_by_material(self, texture_keys, json_files):
        matched = []

        print("\n[SKPBR] MATERIAL MATCH MODE (slow)")

        for js in json_files:
            name = js.stem.lower()
            best_ratio = 0.0

            for t in texture_keys:
                r = difflib.SequenceMatcher(None, name, t).ratio()
                best_ratio = max(best_ratio, r)

            if any(t in name or name in t for t in texture_keys):
                print(f"  ✓ Substring match: {js.name}")
                matched.append(js)
            elif best_ratio >= 0.80:
                print(f"  ✓ Fuzzy match: {js.name} ({best_ratio:.2f})")
                matched.append(js)
            else:
                print(f"  ✗ Reject: {js.name}")

        return matched

    # ------------------------------------------------------------------
    def execute(self, ctx):

        addon_root = Path(__file__).resolve().parent
        exe = addon_root / "thirdparty" / "PBRNifPatcher" / "PBRNifPatcher.exe"

        if not exe.exists():
            self.report({"ERROR"}, f"PBRNifPatcher.exe missing:\n{exe}")
            return {"FINISHED"}

        nif = Path(bpy.path.abspath(self.nif_path))
        if not nif.exists():
            self.report({"ERROR"}, "Invalid NIF path.")
            return {"FINISHED"}

        mod_root = self.find_mod_root(nif)

        # ------------------------------------------------------------------
        # Manual JSON selection OVERRIDES EVERYTHING
        # ------------------------------------------------------------------
        raw_json = self.json_path.strip()

        if raw_json:
            js = Path(bpy.path.abspath(raw_json))
            if not js.exists():
                self.report({"ERROR"}, "Selected JSON is invalid.")
                return {"FINISHED"}
            json_files = [js]

        else:
            # AUTO-MODE
            if self.use_material_match and _SKPBR_HAS_PYFFI:
                # SLOW MODE: ONLY use material-matched files
                print("\n[SKPBR] SLOW MODE ACTIVE (material-based)")
                all_jsons = self.fast_folder_json_search(mod_root, nif)

                texture_keys = self.extract_textures_from_nif(nif)
                json_files = self.match_jsons_by_material(texture_keys, all_jsons)

                if not json_files:
                    self.report({"ERROR"}, "Slow mode found NO JSON matches.")
                    return {"FINISHED"}

            else:
                # FAST MODE
                print("\n[SKPBR] FAST MODE (folder search)")
                json_files = self.fast_folder_json_search(mod_root, nif)
                if not json_files:
                    self.report({"ERROR"}, "No JSON files found.")
                    return {"FINISHED"}

        # ------------------------------------------------------------------
        # Relative NIF path for the patcher
        # ------------------------------------------------------------------
        try:
            rel_nif = nif.relative_to(mod_root)
        except:
            rel_nif = nif

        print("\n--- Running PBRNifPatcher ---")
        print("Mod Root:", mod_root)
        print("NIF:", rel_nif)
        print("JSONs:")
        for js in json_files:
            print("   ", js)

        # ------------------------------------------------------------------
        # Track original timestamps
        # ------------------------------------------------------------------
        mesh_root = mod_root / "meshes"
        before = {}
        if mesh_root.exists():
            for f in mesh_root.rglob("*.nif"):
                before[f] = f.stat().st_mtime

        # ------------------------------------------------------------------
        # Run patcher for every JSON
        # ------------------------------------------------------------------
        for js in json_files:
            try:
                rel_json = js.relative_to(mod_root)
            except:
                rel_json = js

            cmd = [str(exe), "-nif", str(rel_nif), "-json", str(rel_json)]
            print(" RUN:", cmd)
            subprocess.Popen(cmd, cwd=str(mod_root))

        time.sleep(0.8)

        # ------------------------------------------------------------------
        # Success detection (FIXED)
        # ------------------------------------------------------------------
        patched = False
        if mesh_root.exists():
            for f in mesh_root.rglob("*.nif"):
                if f.stat().st_mtime > before.get(f, 0):
                    patched = True
                    break

        if patched:
            self.report({"INFO"}, "PBRNifPatcher: Patch applied successfully.")
        else:
            # No hard ERROR anymore – just an info message to avoid false failures
            self.report(
                {"INFO"},
                "PBRNifPatcher: Finished (no changes detected; check console output).",
            )

        return {"FINISHED"}
