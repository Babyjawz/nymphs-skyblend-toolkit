### 🌲 SpeedTree → Skyrim PBR Texture Packer
This new **SpeedTree packing feature** for the Skyrim Material Patcher addon automatically converts exported SpeedTree `.png` textures into fully compatible **Skyrim PBR `.dds`** texture sets. It uses the Matyalatte **Blender-DDS-Addon** for high-quality DDS export, supports gloss-to-roughness inversion, and follows the correct Skyrim texture naming conventions used by **PBR NIF Patcher** and **ParallaxGen**.
#### 🔧 Features
- Batch-processes an entire SpeedTree export folder at once.  
- Converts all SpeedTree `.png` textures directly to `.dds` (no PNG fallback).  
- Automatically inverts gloss maps to roughness for Skyrim’s PBR polarity.  
- Uses Skyrim-accurate file naming (no `_d` suffix for basecolor).  
- Generates all key PBR texture maps, ready for use with PBR NIF Patcher or ParallaxGen.  
- Integrates seamlessly with the existing **PBRGen** panel in Blender.  
#### 🧩 Output Naming Scheme
Each material set produces a complete set of Skyrim-ready `.dds` textures:  
Bark.dds → Base Color / Albedo (no suffix)  
Bark_n.dds → Normal (RGB) + Height (A)  
Bark_m.dds → Mask / Parallax map  
Bark_p.dds → Height / ParallaxGen source  
Bark_s.dds → Specular / Subsurface / Transmission  
Bark_rmaos.dds → Roughness-Metalness-AO-Opacity-Specular packed map  
#### 🪄 Usage
1. In Blender, open **PBRGen → Pack SpeedTree Textures → RMAOS + DDS Set**.  
2. Select your **SpeedTree export folder** (the one containing all the `.png` textures).  
3. The addon will process all matching textures and export the `.dds` results into a **New Folder** inside the source directory.  
4. The exported maps are immediately usable with **PBR NIF Patcher** and **ParallaxGen**.  
#### ⚙️ Requirements
- [Matyalatte Blender-DDS-Addon](https://github.com/matyalatte/Blender-DDS-Addon)  
- Skyrim Material Patcher v1.9.8 or newer  
- Blender 4.5 (Python 3.11+)  
#### 💡 Notes
- Gloss maps from SpeedTree are automatically inverted to roughness.  
- Missing texture types (like subsurface or opacity) are skipped gracefully.  
- Only `.dds` files are generated — no intermediate PNGs are kept.  
- The workflow is designed for **Skyrim SE/AE PBR pipelines** using community shader tools.  
