<p align="center">
  <img src="SkyBlendBanner.png" alt="Nymphs SkyBlend Toolkit Banner" width="100%">
</p>


# 🌌 Nymphs SkyBlend Toolkit

A Blender 4.5 addon suite for Skyrim modders — built by the Nymph Nerds for the Nymph Nerds.  
SkyBlend helps you import, patch, and rebuild Skyrim materials with proper PBR workflows,  
NIF-based shader parsing, emissive fixes, PBR map generation, and texture lookup through MO2’s VFS.

---

## ✨ Features

- 🔍 **Smart NIF Material Reader**  
  Reads Skyrim NIF shader properties (shader flags, texture paths, emissive colors, translucency, alpha settings, etc).

- 🧪 **PBR Material Generator**  
  Builds principled BSDF PBR materials in Blender using Skyrim textures.

- 🧬 **PBRGen — Generate PBR Maps From Diffuse**  
  Creates: AO, Roughness, Metallic, ORM (combined) maps  
  from only a **diffuse** texture (or diffuse + normal).  
  Supports emission-aware smoothing and SpeedTree presets.

---

## 🌀 Texture Building Pipelines

SkyBlend provides three ways to source and build textures, matching real Skyrim workflows.

### 1. **VFS (NIF Path Mode)** — *Fully automatic*  
Uses the texture paths embedded in the NIF.  
If Blender is launched through **Mod Organizer 2**, SkyBlend resolves these via MO2’s **Virtual File System**, exactly like Skyrim.

Use this when:  
• You want a zero-setup “just import and build” workflow  
• Working with vanilla assets or MO2-installed mods  
• Textures follow Skyrim's directory structure

Benefits:  
• Most accurate  
• Handles overridden textures from multiple mods  
• No browsing required

---

### 2. **Manual (Pick Texture Folder)** — *User-controlled*  
Select any folder on your PC containing diffuse / normal / roughness / metallic / mask / ORM maps.

Use this when:  
• Textures live anywhere (e.g. `D:\MyTextures\PBR`)  
• Working on custom or non-Skyrim assets  
• Iterating on WIP texture sets

Benefits:  
• Extremely flexible  
• No Skyrim structure required

---

### 3. **Mod Folder (MO2 Mod Scan)** — *Target one mod’s textures*  
Choose a **specific MO2 mod directory**, and SkyBlend scans:

`<MO2>/mods/<ModName>/textures/...`

Use this when:  
• A mod replaces vanilla textures  
• You want ONLY that mod’s textures  
• Avoiding conflicts with other mods' overrides

Benefits:  
• Predictable  
• Clean for debugging  
• Perfect for targeted PBR conversions

---

## 🔥 Additional Tools

- **Emissive Patch Tool**  
  Converts Skyrim emissive settings into proper Blender emission nodes.

- **SpeedTree PBR Support**  
  Dedicated material builder for leaves, bark, cross-planes, and billboard trees.

- **NIF Path Detection**  
  Resolves real Skyrim-style paths automatically (with or without MO2).

- **PBRNifPatcher Integration**  
  Builds ORM textures directly from Skyrim’s texture data.

---

## 🚀 Quickstart

1. (Recommended) Launch Blender **through Mod Organizer 2**  
2. Install the addon by selecting the folder `nymphs_skyblend`  
3. Import a Skyrim NIF using **PyNifly**  
4. Open 3D View → Sidebar → **SkyBlend**  
5. Choose a pipeline:  
   • **VFS (NIF Path)**  
   • **Manual Folder**  
   • **Mod Folder**  
6. Press **Build PBR Material**

See `docs/QUICKSTART.md` for a detailed guide.

---

## 📁 Repository Structure

```
nymphs-skyblend-toolkit/
│
├─ nymphs_skyblend/      ← The addon (install this folder)
├─ docs/                 ← User guides & documentation
├─ DEV_NOTES/            ← Internal dev notes (not for end users)
├─ LICENSE               ← GPL-3.0-or-later
└─ README.md             ← You are here
```

---

## 🔧 Developer Installation

1. Clone the repo  
2. In Blender: **Edit → Preferences → Add-ons → Install…**  
3. Select the folder `nymphs_skyblend` (NOT a ZIP)  
4. Press **F8** to reload scripts when editing

Blender will run directly from your repo.

---

## 🧙 About

Part of **Nymphs Savage World**  
Made by the **Nymph Nerds**  
Powered by questionable decisions and thicc PBR energy ✨

---

## 📝 License

Licensed under the **GNU General Public License v3.0 (GPL-3.0-or-later)**.  
See the `LICENSE` file for details.

See the `LICENSE` file for details.

