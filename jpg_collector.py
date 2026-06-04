import shutil
from pathlib import Path


def collect_jpgs(source_folder="output", destination_folder="output/_______all_jpgs"):
    source = Path(source_folder)
    destination = Path(destination_folder)

    if not source.exists():
        print(f"Error: Source folder '{source_folder}' does not exist.")
        return

    destination.mkdir(parents=True, exist_ok=True)

    copied = 0
    renamed = 0

    # Walk through all jpg files
    for jpg_file in source.rglob("*.jpg"):

        # Only process actual files
        if not jpg_file.is_file():
            continue

        # Skip destination folder itself
        if destination in jpg_file.parents:
            continue

        target = destination / jpg_file.name

        # Handle duplicate names
        if target.exists():
            stem = jpg_file.stem
            suffix = jpg_file.suffix
            counter = 1

            while target.exists():
                target = destination / f"{stem}_{counter}{suffix}"
                counter += 1

            print(f"Duplicate found — saving as: {target.name}")
            renamed += 1

        shutil.copy2(jpg_file, target)
        print(f"Copied: {jpg_file} -> {target}")
        copied += 1

    print(
        f"\nDone! {copied} file(s) copied to '{destination_folder}'. "
        f"{renamed} duplicate(s) renamed."
    )


if __name__ == "__main__":
    collect_jpgs()