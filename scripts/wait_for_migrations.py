"""Read-only startup gate for API instances; the worker release owns migrations."""

import os
import sys
import time
from pathlib import Path


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    for _attempt in range(150):
        try:
            executor = MigrationExecutor(connection)
            if not executor.migration_plan(executor.loader.graph.leaf_nodes()):
                print("Database schema is ready")
                return
        except Exception:
            connection.close()
        time.sleep(2)
    raise SystemExit("Database migrations are not ready; inspect the worker release")


if __name__ == "__main__":
    main()
