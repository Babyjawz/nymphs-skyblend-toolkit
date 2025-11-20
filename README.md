# Nymphs SkyBlend Toolkit

A Blender addon suite for Skyrim modders — built by the Nymph Nerds for the Nymph Nerds.  
SkyBlend helps you import, patch, and rebuild Skyrim materials with proper PBR workflows, 
NIF-based shader parsing, emissive fixes, and fast texture generation.

---

## ✨ Features

- 🔍 **Smart NIF Material Reader**  
  Reads Skyrim NIF shader properties (including shader flags, texture paths, emissive colors).

- 🧪 **PBR Material Generator**  
  Auto-builds principled BSDF PBR materials in Blender from Skyrim textures.

- 🌀 **Three Texture Pipeline Modes**  
  1. **Use Textures From NIF (DTX1/5)**  
  2. **Browse Your Own Folder**  
  3. **Mass Build from a Directory**

- 🔥 **Emissive Patch Tool**  
  Converts Skyrim’s emissive data into Blender emission nodes.

- 🌲 **SpeedTree Support**  
  Special PBR builder for SpeedTree meshes (leaves, bark, billboards).

- 🧩 **NIF-Based Path Detection**  
  Reads real Skyrim-style texture paths and works beautifully inside MO2 VFS.

- ⚙️ **PBRNifPatcher Integration**  
  Build PBR maps (AO / Roughness / Metallic) directly from source textures.

---

## 🚀 Quickstart

1. Run Blender **through Mod Organizer 2** (optional but recommended).  
2. Install the addon by selecting the folder `nymphs_skyblend` inside this repo.  
3. Import a Skyrim NIF using **PyNifly**.  
4. In the 3D View → Sidebar → **SkyBlend**, choose one of the build methods:  
   - *Use textures from NIF*  
   - *Browse for textures*  
   - *Batch build*  
5. Press **Build PBR Material** and enjoy your shiny (or thicc) result.

See `docs/QUICKSTART.md` for the full guide.

---

## 📁 Repository Structure

nymphs-skyblend-toolkit/
│
├─ nymphs_skyblend/ ← The addon (install this folder)
├─ docs/ ← User guides & documentation
├─ DEV_NOTES/ ← Internal dev notes (not for end users)
├─ LICENSE ← GPL-3.0-or-later
└─ README.md ← You are here


---

## 🔧 Installation (Developer-Friendly)

To develop the addon:

1. Clone this repo locally.
2. In Blender: **Edit → Preferences → Add-ons → Install…**
3. Select the folder `nymphs_skyblend` directly (not a ZIP).
4. Press **F8** to reload scripts while editing.

Blender will read the addon directly from your repo.

---

## 🧙 About

Part of **Nymphs Savage World**  
Made by the **Nymph Nerds**  
Powered by questionable decisions and thicc PBR energy.

---

## 📝 License

This project is licensed under the **GNU General Public License v3.0** (GPL-3.0-or-later).  
See the `LICENSE` file for details.

