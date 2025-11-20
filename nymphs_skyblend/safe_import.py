# safe_import.py
import sys, os, importlib


def local_import(module_name, subfolder=""):
    """
    Safely load a thirdparty module bundled inside this add-on.
    Always adds the parent 'thirdparty' folder to sys.path so Python
    can correctly find top-level modules like 'pyffi' or 'blender_dds_addon'.
    """
    addon_dir = os.path.dirname(__file__)
    lib_path = os.path.join(addon_dir, "thirdparty")

    sys.path.insert(0, lib_path)
    try:
        module = importlib.import_module(module_name)
    finally:
        sys.path.pop(0)  # clean up after import

    return module
