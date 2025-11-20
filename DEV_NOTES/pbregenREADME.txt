# PBRGen – Skyrim PBR Texture Generator
**Author:** BabyJaws Studios  
**Blender Compatibility:** 4.5+  
**DDS Addon Required:** [Matyalatte’s Blender-DDS-Addon](https://github.com/matyalatte/Blender-DDS-Addon)

---

## 🧩 Overview
**PBRGen** is an advanced texture generation utility for Skyrim and Skyrim SE, designed to produce **physically based rendering (PBR)** texture sets that are fully compatible with **PBRNifPatcher** and Creation Engine 2 material workflows.

It derives complete PBR maps (normal, height, roughness, metallic, AO, specular, glow) directly from a single **source texture** — such as a diffuse, color, or albedo image — using physically plausible approximations.

---

## 🧠 Features
- ✅ Generates **_basecolor**, **_n**, **_p**, and **_rmaos** maps automatically  
- ✅ Optional **_g** (glowmap / emissive) generation  
- ✅ Proper **Skyrim RMAOS channel packing**:
  - **R** → Gloss (inverted roughness)  
  - **G** → Metallic  
  - **B** → Ambient Occlusion  
  - **A** → Gamma-corrected Specular  
- ✅ Supports PNG, JPG, and DDS inputs  
- ✅ Outputs to **DDS first (via Matyalatte’s addon)** with PNG fallback  
- ✅ Adjustable parameters for:
  - Height contrast  
  - Normal strength  
  - Roughness mode & gain  
  - AO radius & intensity  
  - Metallic mode (constant or auto)  
  - Specular level  
  - Glow threshold & intensity  
- ✅ Optionally saves separate roughness, metallic, AO, and specular channels
- ✅ Built-in **progress bar** for long texture generations

---

## ⚙️ Installation
1. Install the **Matyalatte Blender-DDS-Addon** from  
   👉 https://github.com/matyalatte/Blender-DDS-Addon/releases  
2. Copy `pbrgen.py` into your Blender addons folder:  




3. Enable **“PBRGen – Skyrim PBR Texture Generator”** in Blender Preferences.  
4. Ensure the **DDS Textures** addon by Matyalatte is also enabled.  

---

## 🧰 Usage
1. Open **Shader Editor** or **Image Editor** → **PBRGen** tab (right sidebar).  
2. Select your **source image** (PNG, JPG, or DDS).  
3. Optionally set an **output folder** (or leave blank to save next to source).  
4. Adjust generation parameters (roughness, AO, metallic, glow, etc.).  
5. Click **“Generate Skyrim PBR Set”**.

---

## 🧾 Output Files
For a source image like `MyTexture.png`, output textures will be:

| File Name | Purpose |
|------------|----------|
| `MyTexture_basecolor.dds` | Diffuse/albedo |
| `MyTexture_n.dds` | Normal map |
| `MyTexture_p.dds` | Height/Parallax |
| `MyTexture_rmaos.dds` | Packed PBR map (R=Gloss, G=Metal, B=AO, A=Specular) |
| `MyTexture_g.dds` | Glowmap (optional) |
| *(optional)* `MyTexture_rough.dds`, `MyTexture_metal.dds`, `MyTexture_ao.dds`, `MyTexture_spec.dds` | Separate channel exports |

---

## 🔧 Notes
- Uses **BC7** DDS export (default in Matyalatte’s addon) — ideal for modern Skyrim PBR workflows.
- If DDS export fails or the addon isn’t found, fallback PNGs will be written instead.
- Fully compatible with **PBRNifPatcher** material nodes and BSDF PBR setups.
- Default parameters provide realistic results but can be fine-tuned for stylized or high-contrast materials.

---

## 🧑‍💻 Developer Info
- Compatible with **Blender 4.5 API**  
- Non-destructive: always writes output beside source or to a custom folder  
- Designed for easy extension (e.g., smart metal detection, improved AO methods, or shader previews)

---

### 💬 Credits
Developed by **BabyJaws Studios**  
Built for **Skyrim PBR + PBRNifPatcher** community  
Uses [Pillow](https://python-pillow.org) + [Matyalatte DDS Exporter](https://github.com/matyalatte/Blender-DDS-Addon)


13/10/25

## 🧾 Session Summary — PBRGen Addon Enhancement (Blender 4.5.3 LTS)

### 🎯 Original Goal
You started with a fully working Blender add-on for generating PBR textures (mainly for Skyrim) and wanted to **add new features** without removing or altering any of your existing code or UI layout.

---

### ✅ Key Goals Discussed
- Preserve 100% of your current functionality and UI.
- Add **new export targets and logic**.
- Introduce a **universal glow/opacity system** (usable for both emissive and alpha cutout).
- Add **mini live preview** for glow/opacity directly in the UI.
- Implement **clean export logic** for PNG and SpeedTree.
- Make **Base Map Generation** and **Material Tweaks** sections collapsible by default.

---

### 🧰 New Export Targets
We agreed on **three export targets**:

1. **Skyrim (DDS)**
   - Uses BC7 (or fallback BC3/BC1).
   - No “BaseColor” suffix.
   - Edge bleed **disabled**.
   - Uses RMAOS packing.

2. **Authoring (PNG)**
   - For Substance Painter / general PBR authoring.
   - Uses suffixes:
     - `_BaseColor`, `_Normal`, `_Roughness`, `_Metallic`, `_AO`, `_Height`, `_Subsurface`, `_Emissive`, `_RMAOS`.
   - Edge bleed **enabled** to avoid transparency halos.

3. **SpeedTree (PNG)**
   - No RMAOS packing.
   - Always exports **separate maps** only.
   - Naming follows SpeedTree convention:
     - `_Color`, `_Normal`, `_Gloss` (= inverted roughness), `_AO`, `_Height`, `_Subsurface`, `_Metallic`, `_Opacity`.
   - Uses **binary opacity** (from glow map).
   - Edge bleed **enabled**.

---

### ⚙️ Export Modes
We formalized three export modes:
- **Full** → RMAOS + everything else.
- **Both** → RMAOS + individual separate maps.
- **Separates Only** → Only the individual channels.

SpeedTree always behaves as **Separates Only** internally, regardless of selected mode.

---

### 🧾 Naming Conventions
- **Skyrim:**
  - Base diffuse: `basename.dds`
  - Example: `Tree.dds`, `Tree_n.dds`, `Tree_rmaos.dds`

- **Authoring PNG:**
  - `_BaseColor`, `_Normal`, `_Roughness`, `_Metallic`, `_AO`, `_Height`, `_Subsurface`, `_Emissive`, `_RMAOS`

- **SpeedTree PNG:**
  - `_Color`, `_Normal`, `_Gloss`, `_AO`, `_Height`, `_Subsurface`, `_Metallic`, `_Opacity`

You requested *no “speedtree2”*, *no “basecolor”* in Skyrim mode, and *no redundant suffixes.*

---

### 💡 Glow / Opacity System
We replaced your old glowmap logic with a **universal luminance-based system**:
- Uses **perceptual luminance (Rec.709)** for accurate brightness detection.
- Has a **threshold slider** and **Binary toggle**:
  - When **Binary ON** → hard 0/255 cutout (ideal for alpha opacity).
  - When **Binary OFF** → smooth emissive falloff (good for glow).
- Works identically for **Skyrim, PNG, and SpeedTree**.

**SpeedTree:**  
Glowmap automatically becomes `_Opacity.png`.

---

### 🌈 Glow Preview
We added a live **mini preview** of the glow/opacity mask in your UI:
- Appears under the “Extra Outputs” box.
- Uses Blender’s `template_preview()` UI element.
- Updates automatically when changing glow threshold or binary mode.
- Lightweight (no OpenGL, no extra handlers, pure NumPy + PIL).

---

### 🎨 Subsurface % Map
A new grayscale “Subsurface Percent” map is generated from the luminance of the `_Subsurface` texture and saved as:
- `_SubsurfacePercent.dds/png` (for Skyrim/Authoring)
- `_SubsurfacePercent.png` (for SpeedTree)

This aligns with SpeedTree’s “Subsurface %” input.

---

### 🧩 Edge Bleed Logic
To prevent dark seams or outlines:
- **Enabled** for PNG and SpeedTree exports.
- **Disabled** for Skyrim DDS exports (not needed with alpha test).
- Uses your existing dilation method to extend color under transparent pixels.

---

### 🧱 UI / UX Enhancements
- **Collapsible sections**:
  - “Base Map Generation” and “Material Look Tweaks”
  - Default collapsed (small triangle icons)
- **Greyed-out UI**:
  - DDS format and mipmap settings disabled when target ≠ Skyrim.
- **Mini preview** shown only when “Include Glow/Opacity Map” is enabled.

---

### 🗂️ DDS Handling
- Uses Matyalatte’s DDS exporter if available (`blender_dds_addon`).
- Falls back to standard PNG if DDS export unavailable.
- Auto-applies BC7 format, generates mipmaps unless disabled.

---

### 🧪 Internal Logic Adjustments
- Added logic so SpeedTree always bypasses RMAOS creation.
- Integrated conditional save suffix system.
- Preserved “save separate rough/metal/ao” legacy option for Skyrim/PNG only.
- Added automatic `_Gloss` = 255 - Roughness for SpeedTree.

---

### 🧠 Technical Implementation Notes
- Removed hard dependency on `cv2`; now pure **NumPy + PIL**.
- Added `update_glow_preview()` function hooked to property updates.
- Added `PBRGen_GlowPreview` image datablock for live thumbnail.
- Maintained your property registration and operator order.
- Default export flow preserved, no logic stripped or reordered.

---

### ⚠️ Debug / Registration Issues
When UI disappeared:
- Caused by unguarded imports (e.g., `cv2`).
- Fixed by wrapping in `try/except` or using NumPy-based grayscale.
- Ensured `PropertyGroup` registered before panel.
- Verified class naming matches Blender 4.5 conventions.

---

### 🧩 Remaining To-Do (for clean final version)
1. Integrate `update_glow_preview()` + binary glow logic into your existing file.
2. Keep all original texture-gen helpers intact (roughness, AO, etc.).
3. Add collapsible layout blocks with small `TRIA_RIGHT / TRIA_DOWN` icons.
4. Grey out DDS UI if non-Skyrim.
5. Merge all three export targets/modes into your current operator logic.

---

### 🧾 Agreed End State
Your final add-on should:
- Work identically to your current one for Skyrim.
- Add authoring-quality PNG export.
- Add SpeedTree export with separate maps and correct naming.
- Include glow/opacity preview and binary toggle.
- Preserve all existing UI, presets, and property layout.
- Remain free of external dependencies.
- Register cleanly in Blender 4.5+.

---

### 💬 Next Step
You requested a **single, complete patched file** containing:
- All original code (no removals)
- Added logic for:
  - PNG + SpeedTree export targets
  - Universal glow/opacity system (binary toggle)
  - Mini glow preview
  - Edge bleed conditions
  - Collapsible UI sections

That’s the final integration target.

Once confirmed, the next step would be a **line-faithful merge** into your current `pbrgen.py`, preserving every original function and structure.

