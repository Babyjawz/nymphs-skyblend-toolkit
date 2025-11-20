# Quickstart Guide — Skyrim Material & PBR Toolkit

## 1. Launch Blender Through MO2 (Recommended)
Running Blender through Mod Organizer 2 (MO2) gives it access to the Skyrim virtual file system (VFS).

**Steps:**
1. In MO2: open the **Executables** menu → **Add…**
2. Choose your `blender.exe`
3. Name it **Blender (Skyrim)**
4. Launch Blender from MO2

Blender now sees NIF paths and textures through the VFS.

---

## 2. Import a NIF (PyNifly)
1. In Blender: **File → Import → NetImmerse/Gamebryo (.nif)**
2. Pick your NIF from the Skyrim Data paths (visible via VFS)
3. The model loads with correct file paths

---

## 3. Choose a Texture Search Mode
### • VFS (NIF Path)
Uses the texture paths stored inside the NIF.  
Best for vanilla or MO2-installed assets.

### • Manual
Choose a folder containing your PBR textures.

### • Mod Folder
Search inside one specific MO2 mod’s texture folder.

If needed:
**Select Base DDS** to specify the diffuse texture manually.

---

## 4. Build the Material (3 Methods)

### • Build Nodes
Build a PBR material using textures found by the active search mode.

### • Rebuild from NIF Textures
Build a PBR material using the texture paths referenced in the NIF.

### • Run PBRNifPatcher
Patch the *existing* material using shader data from the NIF  
(alpha, emissive, shader flags, etc.)

---

## 5. Optional Tools

**Return to Vanilla** — Restore a simple Skyrim-style non-PBR material  
**Load JSON & Build** — Rebuild a PBR material using saved JSON data  
**Export Selected PBR Patch** — Save current material settings to JSON  
**Patch Emissive (Batch)** — Apply emissive settings to multiple materials at once  

---

This Quickstart covers the core workflow:  
**Import → Choose Search Mode → Build → Optional Patch**
