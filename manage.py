#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    # Automatically re-execute within the virtual environment if available
    base_dir = os.path.dirname(os.path.abspath(__file__))
    venv_python = None
    if sys.platform == "win32":
        candidate = os.path.join(base_dir, ".venv", "Scripts", "python.exe")
        if os.path.exists(candidate):
            venv_python = candidate
    else:
        candidate = os.path.join(base_dir, ".venv", "bin", "python")
        if os.path.exists(candidate):
            venv_python = candidate

    if venv_python:
        norm_venv = os.path.normcase(os.path.abspath(venv_python))
        norm_exec = os.path.normcase(os.path.abspath(sys.executable))
        if norm_venv != norm_exec and os.environ.get("MANAGE_PY_REEXEC") != "1":
            os.environ["MANAGE_PY_REEXEC"] = "1"
            try:
                os.execv(venv_python, [venv_python] + sys.argv)
            except Exception:
                pass

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cubelogs.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
