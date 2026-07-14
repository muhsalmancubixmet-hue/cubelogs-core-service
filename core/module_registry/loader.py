# --------------------------------------------------------------------------------
#       Module Registry Loader
# --------------------------------------------------------------------------------

# STANDARD LIBRARY
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

# DJANGO

# THIRD PARTY

# APPLICATION SPECIFIC


logger = logging.getLogger(__name__)

# Resolve JSON file path relative to this loader.py
REGISTRY_PATH = Path(__file__).resolve().parent / "feature_modules.json"


def load_modules() -> Dict[str, Any]:
    """
    Load the entire module registry dictionary from the JSON file.
    """
    if not REGISTRY_PATH.exists():
        logger.error(f"Module registry file not found at {REGISTRY_PATH}")
        return {"modules": []}

    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                logger.error("Module registry format is invalid: expected dictionary root.")
                return {"modules": []}
            return data
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Error reading module registry file: {e}")
        return {"modules": []}


def get_modules() -> List[Dict[str, Any]]:
    """
    Get the list of all registered optional feature modules.
    """
    registry = load_modules()
    modules = registry.get("modules", [])
    if isinstance(modules, list):
        return modules
    return []


def get_module(module_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a specific feature module detail by its ID.
    """
    modules = get_modules()
    for module in modules:
        if isinstance(module, dict) and module.get("id") == module_id:
            return module
    return None


def get_enabled_modules() -> List[Dict[str, Any]]:
    """
    Get all feature modules that are currently enabled.
    """
    modules = get_modules()
    return [m for m in modules if isinstance(m, dict) and m.get("enabled", True) is True]


def get_functional_capabilities(module_id: str) -> List[Dict[str, Any]]:
    """
    Get the functional capabilities associated with a given module.
    """
    module = get_module(module_id)
    if module:
        capabilities = module.get("functional_capabilities", [])
        if isinstance(capabilities, list):
            return capabilities
    return []


def get_capability(capability_id: str) -> Optional[Dict[str, Any]]:
    """
    Find a specific functional capability details by its capability ID.
    Example: capability_id="attendance:staff" or "leaves:apply"
    """
    modules = get_modules()
    for module in modules:
        if isinstance(module, dict):
            capabilities = module.get("functional_capabilities", [])
            if isinstance(capabilities, list):
                for cap in capabilities:
                    if isinstance(cap, dict) and cap.get("id") == capability_id:
                        return cap
    return None


def get_dynamic_feature_gates(module_id: str) -> Dict[str, Any]:
    """
    Get dynamic sub feature gates (booleans) associated with a given module.
    """
    module = get_module(module_id)
    if module:
        gates = module.get("dynamic_sub_feature_gates", {})
        if isinstance(gates, dict):
            return gates
    return {}


def is_module_enabled(module_id: str) -> bool:
    """
    Check if a specific module is registered and enabled.
    """
    module = get_module(module_id)
    return module is not None and module.get("enabled", True) is True
