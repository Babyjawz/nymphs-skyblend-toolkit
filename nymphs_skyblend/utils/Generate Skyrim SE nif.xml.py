import os, shutil, datetime, textwrap

# --- Paths ------------------------------------------------------------
nif_dir = os.path.expanduser(
    r"~\AppData\Roaming\Blender Foundation\Blender\4.5\scripts\addons"
    r"\skyrim_mat_patcher\thirdparty\pyffi\formats\nif"
)
xml_path = os.path.join(nif_dir, "nif.xml")
backup = xml_path + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

# --- Backup old file --------------------------------------------------
if os.path.exists(xml_path):
    shutil.copy2(xml_path, backup)
    print(f"📦  Backed up existing nif.xml → {backup}")

# --- Skyrim SE / AE nif.xml template ----------------------------------
xml = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<niftoolsxml xmlns="http://niftools.sourceforge.net/nif.xml" version="20.2.0.7">
  <!-- Skyrim SE / AE + Fallout 4 blocks -->
  <basic name="HeaderString" type="string" template="SkyrimSE NIF"/>

  <compound name="Color3">
    <field name="r" type="float"/>
    <field name="g" type="float"/>
    <field name="b" type="float"/>
  </compound>

  <niobject name="BSTriShape" inherit="NiTriShape">
    <doc>Skyrim SE geometry node</doc>
  </niobject>

  <niobject name="BSLightingShaderProperty" inherit="NiShadeProperty">
    <doc>Lighting shader (Skyrim SE)</doc>
    <field name="shader_type" type="uint"/>
    <field name="shader_flags_1" type="uint"/>
    <field name="shader_flags_2" type="uint"/>
    <field name="uv_offset" type="Vector2"/>
    <field name="uv_scale" type="Vector2"/>
    <field name="texture_set" type="Ref" template="BSShaderTextureSet"/>
    <field name="emissive_color" type="Color3"/>
    <field name="emissive_multiple" type="float"/>
    <field name="specular_color" type="Color3"/>
    <field name="specular_strength" type="float"/>
    <field name="soft_lighting" type="float"/>
    <field name="rim_lighting" type="float"/>
  </niobject>

  <niobject name="BSShaderTextureSet" inherit="NiObject">
    <doc>Texture set containing diffuse, normal, glow maps, etc.</doc>
    <field name="num_textures" type="uint"/>
    <field name="textures" type="SizedString" arr1="num_textures"/>
  </niobject>

  <niobject name="BSFadeNode" inherit="NiNode">
    <doc>Used for grouped fading objects</doc>
  </niobject>
</niftoolsxml>
""")

# --- Write file -------------------------------------------------------
with open(xml_path, "w", encoding="utf-8") as f:
    f.write(xml)
print(f"✅  Skyrim SE nif.xml written to:\n    {xml_path}")
print("You can now re-run emissive / texture scripts — BSTriShape & emissive data will load correctly.")
