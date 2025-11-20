import os
from skyrim_mat_patcher import pynifly

DLL_PATH = os.path.join(
    os.path.dirname(pynifly.__file__),
    "NiflyDLL.dll"
)

print(f"🔧 Trying to load: {DLL_PATH}")

nifly = pynifly.NifFile()
if nifly.Load(DLL_PATH):  # sanity check
    print("✅ DLL loaded.")
else:
    print("⚠️ Failed to load DLL (unexpected).")

# === TEST FILE ===
NIF_PATH = r"D:\Nymphs\mods\Skyrim Fantasy Overhaul - Base Object Swapper\meshes\architecture\winterhold\WinterholdTGC\WHWallChunkWindows01TGC_ST.nif"

print(f"\n🔹 Loading {NIF_PATH} ...")
nif = pynifly.NifFile()
nif.Load(NIF_PATH)

print(f"→ Class: {type(nif)}")
print(f"→ Dir: {dir(nif)}")

try:
    num_blocks = getattr(nif, "GetNumBlocks", None)
    if callable(num_blocks):
        count = num_blocks()
        print(f"📦 Number of blocks: {count}")
        for i in range(count):
            block = nif.GetBlock(i)
            print(f"  Block[{i}] type = {block.GetType()}")
            print(f"    Attrs: {[a for a in dir(block) if not a.startswith('__')]}")
except Exception as e:
    print("❌ Error inspecting blocks:", e)
