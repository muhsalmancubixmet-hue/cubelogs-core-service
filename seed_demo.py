#!/usr/bin/env python
# --------------------------------------------------------------------------------
#       Standalone Demo Dataset Seeder & Cleanup Runner
# --------------------------------------------------------------------------------
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cubelogs.settings")
    try:
        import django
        django.setup()
        from django.core.management import call_command
    except Exception as exc:
        sys.stderr.write(f"Error bootstrapping Django environment: {exc}\n")
        sys.exit(1)

    # Forward CLI arguments directly to the management command
    args = sys.argv[1:]
    call_command("seed_full_demo", *args)
