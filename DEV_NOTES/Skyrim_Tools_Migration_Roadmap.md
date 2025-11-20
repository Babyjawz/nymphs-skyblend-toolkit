# 🧭 Skyrim Tools Implementation Roadmap

## 1️⃣ Create a workspace and safety backups

**Goal:** be able to recover *instantly* if a migration goes wrong.

- Create a new folder alongside your working addon:  
  ```
  D:\3dTools\Skyrim_Tools_Migration\
  ```
- Copy the entire working add-on and thirdparty folders into it.  
  ```
  D:\3dTools\Skyrim_Tools_Migration\blender_addon\
  D:\3dTools\Skyrim_Tools_Migration\thirdparty\
  ```
- Rename your current `__init__.py` to  
  ```
  __init__.legacy.py
  ```
  Keep this forever as your “golden fallback.”

✅ **Checkpoint:**  
Open Blender → enable the legacy addon copy → confirm all operators and DDS export still work.

---

## 2️⃣ Build the new minimal structure

Create these empty files:

```
blender_addon/
│
├── __init__.py
├── core.py
├── patcher_nodes.py
├── patcher_ops.py
├── ui_panels.py
├── pbrgen.py
└── emissive_patcher.py
```

Each should have:
```python
import bpy

def register(): pass
def unregister(): pass
```

✅ **Checkpoint:**  
Enable the empty addon in Blender.  
It should register without errors (no functions yet, just placeholders).

---

## 3️⃣ Add `thirdparty` bootstrapping

Inside `blender_addon/thirdparty/__init__.py`:

```python
import sys, os
_here = os.path.dirname(__file__)
if _here not in sys.path:
    sys.path.insert(0, _here)
```

✅ **Checkpoint:**  
Run in Blender’s console:  
```python
import pyffi, pynifly
print(pyffi, pynifly)
```  
→ both should import from your local `thirdparty/` folder.

---

## 4️⃣ Move `core.py` helpers

From your legacy `__init__.py`, move:
- constants (suffixes, default DDS formats, etc.)
- path-building and image helper functions
- color-space utilities
- config read/write (add that JSON config helper we discussed)

Leave logic untouched.

✅ **Checkpoint:**  
Launch Blender, import manually in console:
```python
from blender_addon import core
print(core.build_paths("Test", "D:/Temp"))
```
→ should print valid paths with suffixes.

---

## 5️⃣ Split your logic safely

Now, open your legacy `__init__.py` side-by-side with the new folder.

Copy entire *blocks* into the new files:

| Destination | What to move |
|--------------|--------------|
| `patcher_nodes.py` | all node creation functions |
| `patcher_ops.py` | all `bpy.types.Operator` classes |
| `ui_panels.py` | all UI panel classes |
| `pbrgen.py` | texture generation classes (keep working version) |
| `emissive_patcher.py` | emissive/glow operator (if Blender-side) |

Don’t rewrite—just paste and adjust top-level imports, e.g.  
`from . import core, patcher_nodes`

✅ **Checkpoint:**  
Register the addon.  
All menus and operators should appear exactly as before.  
Run a test generation (Elm tree textures) → confirm identical console output.

---

## 6️⃣ Integrate the external Emissive Patcher

Create a new top-level folder (next to `blender_addon/`):

```
emissive_patcher/
├── __main__.py
└── batch_patch.py
```

At the top of both, add:

```python
import sys, os
root = os.path.dirname(os.path.dirname(__file__))
thirdparty = os.path.join(root, "thirdparty")
if thirdparty not in sys.path:
    sys.path.insert(0, thirdparty)
```

Then paste your existing emissive patch logic.

✅ **Checkpoint:**  
Open a command prompt:
```
cd D:\3dTools\Skyrim_Tools_Migration\emissive_patcher
python __main__.py
```
→ It should run your current batch patch successfully.

---

## 7️⃣ Add the bridge button in Blender

In `patcher_ops.py`, add:

```python
import subprocess, sys, os

class EMISSIVE_OT_run_batch(bpy.types.Operator):
    bl_idname = "skyrimtools.run_emissive_batch"
    bl_label = "Run Emissive Batch Patcher"
    bl_description = "Launch external emissive patcher"

    def execute(self, context):
        root = os.path.dirname(os.path.dirname(__file__))
        script = os.path.join(root, "emissive_patcher", "__main__.py")
        subprocess.Popen([sys.executable, script])
        self.report({'INFO'}, "Launched Emissive Batch Patcher.")
        return {'FINISHED'}
```

In `ui_panels.py`:
```python
layout.operator("skyrimtools.run_emissive_batch", icon="OUTLINER_OB_LIGHT")
```

✅ **Checkpoint:**  
Click the button in Blender → emissive patcher opens externally.

---

# 🧩 Validation Checklist

✅ DDS export (Matyalatte integration)  
✅ PBRGen generates all maps (`_rmaos`, `_m`, `_s`, `_g`)  
✅ Material patcher builds nodes and assigns textures  
✅ Emissive patcher runs externally and edits NIFs  
✅ No missing imports or UI changes  
✅ Core helpers reusable by both domains  

---

# 🧠 After Successful Migration

Once all checkpoints pass:
- Remove `__init__.legacy.py` from active use (keep a backup).  
- Compress the whole folder as `skyrim_tools.zip`.  
- Install it via Blender’s “Install Add-on from File…” option.  
- Optionally, create a `README.md` using the markdown architecture doc you saved.

---

## 🚀 Optional Enhancements (Post-Migration)

- **Shared logging:** Have both tools log into `~/SkyrimTools/logs/`.
- **Config UI:** Add a “Preferences” panel inside Blender’s Add-on tab for default DDS format and overwrite toggle.
- **Future modules:** collision patcher, auto-parallax normal fix, texture renamer, etc.

---

### ✅ TL;DR

> Follow this migration in 7 small, tested steps.  
> Each checkpoint ensures the suite still runs identically.  
> When finished, you’ll have a clean, modular, self-contained toolkit:
> - `blender_addon` → texture generation & materials  
> - `emissive_patcher` → NIF batch editing  
> - `thirdparty` → stable local dependencies  
> ready to ship as **Skyrim Tools**.
