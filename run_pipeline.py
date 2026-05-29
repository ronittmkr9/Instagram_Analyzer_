"""
run_pipeline.py — Instagram Pipeline Launcher
══════════════════════════════════════════════
Place this file in the SAME folder as the four pipeline scripts:

    run_pipeline.py
    iteration01scraper.py
    analyze_insta_except.py
    jpg_collector.py
    csvmaker.py

Pipeline steps (all four run by default):
    python run_pipeline.py               # all four steps
    python run_pipeline.py --analyze     # skip scraper; run analyzer → jpg → csv
    python run_pipeline.py --csv         # skip scraper + analyzer;  run jpg → csv only

URL tracking:
    input.csv and inputdone.csv ALWAYS live in the base launcher directory.
    They are NEVER copied into the project output folder.
    As each URL is scraped, it is:
        • Removed from  input.csv      (in the base dir, in real-time)
        • Added to      inputdone.csv  (in the base dir, with timestamp)
    After the scraper finishes, a final reconcile pass double-checks both files.

Output structure for a project named e.g. "Ronit":
    <launcher>/
    ├── input.csv            ← URLs to scrape (processed ones removed in real-time)
    ├── inputdone.csv        ← completed URLs with timestamps
    └── Output_Ronit/
        ├── output/
        │   └── <username>/
        │       ├── userInfo.json
        │       ├── postInfo.json
        │       └── <username>.jpg
        ├── Ronit_data.json
        ├── Ronit.jsonl
        ├── Ronit.csv
        └── _____all_jpg/
            └── *.jpg
"""

import os
import sys
import csv
import shutil
import subprocess
import datetime
import importlib.util
from pathlib import Path
from threading import Lock

# ── script locations ──────────────────────────────────────────────────────────
LAUNCHER_DIR   = Path(__file__).parent.resolve()
SCRAPER_SCRIPT = LAUNCHER_DIR / "iteration01scraper.py"
ANALYZE_SCRIPT = LAUNCHER_DIR / "analyze_insta_except.py"
JPG_SCRIPT     = LAUNCHER_DIR / "jpg_collector.py"
CSV_SCRIPT     = LAUNCHER_DIR / "csvmaker.py"

# ── canonical CSV paths — ALWAYS in launcher dir, never in project folder ─────
INPUT_CSV      = LAUNCHER_DIR / "input.csv"
INPUT_DONE_CSV = LAUNCHER_DIR / "inputdone.csv"

# Thread-safe lock for concurrent URL moves
_url_lock = Lock()


# ── helpers ───────────────────────────────────────────────────────────────────

def banner(text: str):
    line = "─" * 62
    print(f"\n{line}\n  {text}\n{line}")


def check_scripts_exist():
    required = [SCRAPER_SCRIPT, ANALYZE_SCRIPT, JPG_SCRIPT, CSV_SCRIPT]
    missing  = [s.name for s in required if not s.exists()]
    if missing:
        print("\n  ✗  Missing scripts (must be next to run_pipeline.py):")
        for n in missing:
            print(f"       – {n}")
        raise SystemExit(1)


def ask_project_name() -> str:
    while True:
        raw = input("\n📁  Enter a name for this project: ").strip()
        if not raw:
            print("    ⚠  Name cannot be empty.")
            continue
        return raw.replace(" ", "_").replace("/", "_").replace("\\", "_")


def make_project_folder(name: str) -> tuple:
    project_root = LAUNCHER_DIR / f"Output_{name}"
    output_dir   = project_root / "output"

    if project_root.exists():
        ans = input(
            f"\n    Folder 'Output_{name}' already exists. Use it? [y/n]: "
        ).strip().lower()
        if ans != "y":
            raise SystemExit("Aborted.")
    else:
        project_root.mkdir(parents=True)
        print(f"    ✓  Created: {project_root}")

    output_dir.mkdir(exist_ok=True)
    return project_root, output_dir


def run_script(script: Path, cwd: Path, extra_args=None) -> int:
    cmd = [sys.executable, str(script)] + (extra_args or [])
    print(f"\n  ▶  {script.name}  (cwd: {cwd.name})\n")
    result = subprocess.run(cmd, cwd=str(cwd))
    return result.returncode


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _ensure_input_csv():
    if not INPUT_CSV.exists():
        with open(INPUT_CSV, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["url"])


def _ensure_done_csv():
    if not INPUT_DONE_CSV.exists():
        with open(INPUT_DONE_CSV, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["url", "processed_at"])


def load_csv_urls(filepath: Path) -> list:
    if not filepath.exists():
        return []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["url"].strip() for row in reader if row.get("url", "").strip()]


def load_done_rows(filepath: Path) -> list:
    if not filepath.exists():
        return []
    with open(filepath, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── post-scrape reconcile (safety-net) ───────────────────────────────────────

def reconcile_input_files():
    """
    After the scraper exits, remove from input.csv any URL already in
    inputdone.csv. Catches anything the scraper missed if it crashed mid-run.
    """
    _ensure_input_csv()
    _ensure_done_csv()

    done_urls = {r["url"].strip() for r in load_done_rows(INPUT_DONE_CSV)}

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = reader.fieldnames or ["url"]
        all_rows   = list(reader)

    remaining = [r for r in all_rows if r.get("url", "").strip() not in done_urls]
    moved     = len(all_rows) - len(remaining)

    with open(INPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(remaining)

    if moved:
        print(f"    • Reconciled: {moved} URL(s) cleaned from input.csv.")
    else:
        print("    • Reconcile: input.csv already clean.")

    print(f"    • input.csv     → {len(remaining)} URL(s) remaining")
    print(f"    • inputdone.csv → {len(done_urls)} URL(s) completed")


# ── pipeline steps ────────────────────────────────────────────────────────────

def step_scraper(project_root: Path, output_dir: Path):
    banner("Step 1 / 4 — Scraper  (iteration01scraper.py)")

    _ensure_input_csv()
    _ensure_done_csv()

    total_before = len(load_csv_urls(INPUT_CSV))
    print(f"  input.csv     : {INPUT_CSV}  ({total_before} URLs)")
    print(f"  inputdone.csv : {INPUT_DONE_CSV}")
    print(f"  output dir    : {output_dir}")
    print("\n  Completed URLs are removed from input.csv and added to inputdone.csv in real-time.\n")

    # ── KEY FIX: run the scraper with cwd=LAUNCHER_DIR so that its default  ──
    # "input.csv" and "inputdone.csv" resolve to the launcher directory.
    # The scraper also writes output/<username>/ relative to its cwd, so we
    # create a symlink (or junction on Windows) from LAUNCHER_DIR/output →
    # project output_dir, then remove it after the scraper finishes.
    #
    # Symlink approach works on macOS/Linux. On Windows it requires admin or
    # Developer Mode; we fall back to a plain env-var hint in that case.

    output_link = LAUNCHER_DIR / "output"
    link_created = False

    try:
        # Remove stale link/dir if it exists from a previous interrupted run
        if output_link.is_symlink():
            output_link.unlink()
        elif output_link.exists():
            # A real output/ folder exists in the launcher dir — don't touch it,
            # just let the scraper write there and we'll move files after.
            pass

        if not output_link.exists():
            output_link.symlink_to(output_dir, target_is_directory=True)
            link_created = True
            print(f"  ✓  Symlink: {output_link.name} → {output_dir}")

    except (OSError, NotImplementedError):
        # Symlinks unavailable (Windows without privileges) — fall back to
        # running the scraper from the project root and fixing up paths via env.
        link_created = False

    env = os.environ.copy()
    # Pass absolute paths as env vars so the scraper can optionally use them
    env["PIPELINE_INPUT_CSV"]      = str(INPUT_CSV)
    env["PIPELINE_INPUT_DONE_CSV"] = str(INPUT_DONE_CSV)
    env["PIPELINE_OUTPUT_DIR"]     = str(output_dir)

    try:
        # Run scraper from LAUNCHER_DIR so relative paths resolve correctly
        cmd = [sys.executable, str(SCRAPER_SCRIPT)]
        print(f"\n  ▶  {SCRAPER_SCRIPT.name}  (cwd: {LAUNCHER_DIR.name})\n")
        result = subprocess.run(cmd, cwd=str(LAUNCHER_DIR), env=env)
    finally:
        # Always clean up the symlink
        if link_created and output_link.is_symlink():
            output_link.unlink()
            print(f"  ✓  Symlink removed: {output_link.name}")

    if result.returncode != 0:
        print(f"\n  ✗  Scraper failed (exit {result.returncode}).")
        raise SystemExit(result.returncode)

    print("\n  ✓  Scraper done.")

    # ── If there was no symlink and the scraper wrote to LAUNCHER_DIR/output,
    #    move those folders into the project output_dir.
    launcher_output = LAUNCHER_DIR / "output"
    if launcher_output.exists() and not launcher_output.is_symlink():
        for item in launcher_output.iterdir():
            dest = output_dir / item.name
            if not dest.exists():
                shutil.move(str(item), str(dest))
                print(f"  Moved: output/{item.name} → {output_dir.name}/{item.name}")

    print("\n  Reconciling input.csv ↔ inputdone.csv …")
    reconcile_input_files()
    print()


def step_analyzer(project_root: Path, output_dir: Path, project_name: str):
    banner("Step 2 / 4 — Analyzer  (analyze_insta_except.py)")
    print(f"  Reads from:  Output_{project_name}/output/")
    print(f"  Produces:    {project_name}_data.json  +  {project_name}.jsonl\n")

    for fname in (f"{project_name}_data.json", f"{project_name}.jsonl"):
        stale = LAUNCHER_DIR / fname
        if stale.exists():
            stale.unlink()

    rc = run_script(
        ANALYZE_SCRIPT,
        cwd=LAUNCHER_DIR,
        extra_args=["--project", project_name, "--output", str(output_dir)],
    )
    if rc != 0:
        print(f"\n  ✗  Analyzer failed (exit {rc}).")
        raise SystemExit(rc)

    for fname in (f"{project_name}_data.json", f"{project_name}.jsonl"):
        src = LAUNCHER_DIR / fname
        if src.exists():
            dest = project_root / fname
            shutil.move(str(src), str(dest))
            print(f"  ✓  {fname}  →  {project_root.name}/")
        elif fname.endswith(".jsonl"):
            print(f"  ⚠  {fname} was not produced by the analyzer.")
        else:
            print(f"\n  ✗  Analyzer ran but produced no {fname}.")
            raise SystemExit(1)


def step_jpg_collector(project_root: Path, output_dir: Path):
    banner("Step 3 / 4 — JPG Collector  (copy mode)")
    destination = project_root / "_____all_jpg"
    destination.mkdir(parents=True, exist_ok=True)

    print(f"  Source:       {output_dir}")
    print(f"  Destination:  {destination}")
    print(f"  Mode:         COPY  (originals stay in output/)\n")

    copied = renamed = 0
    for jpg_file in output_dir.rglob("*.jpg"):
        target = destination / jpg_file.name
        if target.exists():
            stem, suffix = jpg_file.stem, jpg_file.suffix
            counter = 1
            while target.exists():
                target = destination / f"{stem}_{counter}{suffix}"
                counter += 1
            print(f"  Duplicate → saving as: {target.name}")
            renamed += 1
        shutil.copy2(str(jpg_file), str(target))
        print(f"  Copied: {jpg_file.relative_to(output_dir)}  →  _____all_jpg/{target.name}")
        copied += 1

    print(f"\n  ✓  {copied} JPG(s) copied  ({renamed} renamed for duplicate filenames).")


def step_csv(project_root: Path, project_name: str):
    banner("Step 4 / 4 — CSV Maker  (csvmaker.py)")

    data_json  = project_root / f"{project_name}_data.json"
    output_csv = project_root / f"{project_name}.csv"

    if not data_json.exists():
        print(f"  ✗  {data_json.name} not found in '{project_root.name}/'.")
        print("     Run the analyzer step first.")
        raise SystemExit(1)

    print(f"  Input:   {data_json.name}")
    print(f"  Output:  {output_csv.name}\n")

    spec   = importlib.util.spec_from_file_location("csvmaker", CSV_SCRIPT)
    csvmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(csvmod)

    success, total_users = csvmod.create_csv_from_analyzed_json_efficiently(
        str(data_json), str(output_csv)
    )

    if success:
        print(f"\n  ✓  {project_name}.csv saved → {project_root.name}/")
        print(f"  ✓  Total creators exported: {total_users}")
    else:
        print("\n  ✗  CSV maker failed.")
        raise SystemExit(1)


# ── summary ───────────────────────────────────────────────────────────────────

def print_tree(project_root: Path, output_dir: Path):
    def _tree(folder: Path, prefix: str = "    "):
        items = sorted(folder.iterdir())
        for i, item in enumerate(items):
            connector = "└──" if i == len(items) - 1 else "├──"
            icon = "📂" if item.is_dir() else "📄"
            print(f"{prefix}{connector} {icon}  {item.name}")
            if item.is_dir():
                ext = "    " if i == len(items) - 1 else "│   "
                if item.name == "output":
                    sub_items = sorted(item.iterdir())
                    for j, sub in enumerate(sub_items[:5]):
                        sc = "└──" if j == min(4, len(sub_items) - 1) else "├──"
                        print(f"{prefix}{ext}{sc} 📂  {sub.name}/")
                    if len(sub_items) > 5:
                        print(f"{prefix}{ext}    … ({len(sub_items) - 5} more folders)")
                elif item.name == "_____all_jpg":
                    jpg_count = len(list(item.glob("*.jpg")))
                    print(f"{prefix}{ext}    ({jpg_count} JPG file(s))")
    _tree(project_root)


def print_url_summary():
    remaining = load_csv_urls(INPUT_CSV)
    done_rows = load_done_rows(INPUT_DONE_CSV)
    print(f"\n  URL Summary  (base dir):")
    print(f"    ✓  Completed : {len(done_rows):>5}  →  inputdone.csv")
    print(f"    ⏳  Remaining : {len(remaining):>5}  →  input.csv")
    if remaining:
        print(f"\n  Remaining URLs (will run on next scrape):")
        for url in remaining[:10]:
            print(f"    – {url}")
        if len(remaining) > 10:
            print(f"    … and {len(remaining) - 10} more")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    banner("Instagram Pipeline Launcher")
    check_scripts_exist()

    args        = sys.argv[1:]
    run_scraper = "--analyze" not in args and "--csv" not in args
    run_analyze = "--csv"     not in args

    project_name             = ask_project_name()
    project_root, output_dir = make_project_folder(project_name)

    if run_scraper:
        step_scraper(project_root, output_dir)
    else:
        print("\n  ⏭  Skipping scraper.")
        if not output_dir.exists() or not any(output_dir.iterdir()):
            print(f"  ⚠  No data found in 'Output_{project_name}/output/'.")
            raise SystemExit(1)

    if run_analyze:
        step_analyzer(project_root, output_dir, project_name)
    else:
        print("\n  ⏭  Skipping analyzer.")
        if not (project_root / f"{project_name}_data.json").exists():
            print(f"  ⚠  {project_name}_data.json not found — cannot run CSV step.")
            raise SystemExit(1)

    step_jpg_collector(project_root, output_dir)
    step_csv(project_root, project_name)

    banner("✅  Pipeline complete")
    print(f"  All outputs are inside:\n  📂  Output_{project_name}/\n")
    print_tree(project_root, output_dir)
    print_url_summary()
    print()


if __name__ == "__main__":
    main()