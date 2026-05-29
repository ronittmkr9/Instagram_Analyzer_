import os
import shutil
from pathlib import Path


def collect_jpgs(source_folder="output", destination_folder="output/_______all_jpgs"):
    source = Path(source_folder)
    destination = Path(destination_folder)

    if not source.exists():
        print(f"Error: Source folder '{source_folder}' does not exist.")
        return

    destination.mkdir(parents=True, exist_ok=True)

    moved = 0
    skipped = 0

    # Walk through all subfolders
    for jpg_file in source.rglob("*.jpg"):
        # Skip files already in the destination folder
        if destination in jpg_file.parents:
            continue

        target = destination / jpg_file.name

        # Handle duplicates by appending a counter
        if target.exists():
            stem = jpg_file.stem
            suffix = jpg_file.suffix
            counter = 1
            while target.exists():
                target = destination / f"{stem}_{counter}{suffix}"
                counter += 1
            print(f"Duplicate found — saving as: {target.name}")
            skipped += 1

        shutil.copy2(str(jpg_file), str(target))
        print(f"Copied: {jpg_file} -> {target}")
        moved += 1

    print(f"\nDone! {moved} file(s) moved to '{destination_folder}'. {skipped} duplicate(s) renamed.")


if __name__ == "__main__":
    collect_jpgs()