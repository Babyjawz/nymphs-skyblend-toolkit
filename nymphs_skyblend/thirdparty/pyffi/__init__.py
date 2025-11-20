"""
:mod:`pyffi` --- Interfacing block structured files
===================================================

Local version for Skyrim Material Patcher (PyFFI shim)
"""

# ***** BEGIN LICENSE BLOCK *****
# Copyright (c) 2007-2012, Python File Format Interface
# All rights reserved.
# ***** END LICENSE BLOCK *****

import os

# --- Version ---
with open(os.path.join(os.path.dirname(__file__), "VERSION"), "rt") as f:
    __version__ = f.read().strip()
globals()["__version__"] = __version__  # expose version for external tools

# --- Ensure all PyFFI submodules are importable ---
try:
    from . import formats
    from . import object_models
    from . import spells
    from . import utils
    # qscope sometimes triggers circular import; defer until end
    try:
        from . import qscope
    except ImportError:
        qscope = None
        print("[PyFFI] (deferred) qscope import skipped to avoid circular reference.")
except Exception as e:
    print(f"[PyFFI] Warning: failed to import local submodules: {e}")

__all__ = ["formats", "object_models", "spells", "utils", "qscope"]


print(f"[PyFFI] Local PyFFI initialized successfully (v{__version__})")
