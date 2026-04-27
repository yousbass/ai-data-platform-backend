

"""
Batch runner for the AI data platform backend.

This script processes every CSV file inside the input/ folder and creates a
separate output folder for each file.

Run from the project root with:
    python batch_run.py
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_ROOT / "input"

# Make sure main.py can be imported from the project root.
sys.path.insert(0, str(PROJECT_ROOT))

from main import run_pipeline  # noqa: E402


def main() -> None:
    csv_files = sorted(INPUT_DIR.glob("*.csv"))

    if not csv_files:
        print(f"No CSV files found in: {INPUT_DIR}")
        return

    print(f"Found {len(csv_files)} CSV file(s) in: {INPUT_DIR}")
    print("=" * 60)

    successful = []
    failed = []

    for csv_file in csv_files:
        print(f"\nProcessing: {csv_file.name}")
        print("-" * 60)

        try:
            run_pipeline(str(csv_file))
            successful.append(csv_file.name)
        except Exception as exc:
            print(f"Failed to process {csv_file.name}: {exc}")
            failed.append({"file": csv_file.name, "error": str(exc)})

    print("\n" + "=" * 60)
    print("Batch run completed.")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")

    if successful:
        print("\nSuccessful files:")
        for name in successful:
            print(f"  - {name}")

    if failed:
        print("\nFailed files:")
        for item in failed:
            print(f"  - {item['file']}: {item['error']}")

    print("\nEach CSV output is saved in:")
    print(f"  {PROJECT_ROOT / 'output'}/<csv_file_name>/")


if __name__ == "__main__":
    main()