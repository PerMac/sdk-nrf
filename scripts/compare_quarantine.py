#!/usr/bin/env python3

# Copyright (c) 2025 Zephyr Project
# SPDX-License-Identifier: Apache-2.0

"""
Script to compare two quarantine.yaml files and report added/removed scenarios.
"""

import argparse
import sys
from collections.abc import Iterable
from itertools import product
from pathlib import Path


# Add the twister library path to import Quarantine
import os
ZEPHYR_BASE = Path(os.getenv("ZEPHYR_BASE"))
sys.path.insert(0, str(ZEPHYR_BASE / 'scripts' / 'pylib' / 'twister' / 'twisterlib'))

from quarantine import QuarantineData


ALL_PLATFORMS_TOKEN = "__ALL__"

def get_all_configurations(quarantine_file):
    """Extract all configurations from a quarantine file."""
    try:
        quarantine_data = QuarantineData.load_data_from_yaml(quarantine_file)
        configurations = set()

        for qelem in quarantine_data.qlist:
            # Add all configurations from this quarantine element
            scenarios = qelem.scenarios if qelem.scenarios else [None]
            platforms = qelem.platforms if qelem.platforms else [ALL_PLATFORMS_TOKEN]
            # Generate all possible pairs
            configurations.update(product(scenarios, platforms))
        return configurations
    except Exception as e:
        print(f"Error loading {quarantine_file}: {e}")
        sys.exit(1)


def expand_patterns(patterns: Iterable[str], known_scenarios: Iterable[str]) -> set[str]:
    out: set[str] = set()
    compiled: list[tuple[str, re.Pattern]] = []
    for p in patterns:
        try:
            compiled.append((p, compile_rx(p)))
        except re.error:
            compiled.append((p, re.compile(re.escape(p) + r"\Z")))
    for name in known_scenarios:
        for _, rx in compiled:
            if rx.fullmatch(name):
                out.add(name)
                break
    return out


def discover_scenarios(repo_root: Path) -> dict[str, set[str]]:
    """
    Map: scenario_name -> set(yaml_paths_defining_it)
    Keys in top-level 'tests:' mapping of each YAML are Twister scenario names.
    """
    mapping: dict[str, set[str]] = {}
    for pattern in SCENARIO_YAML_GLOBS:
        for p in repo_root.glob(pattern):
            if not p.is_file():
                continue
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                tests = data.get("tests", {})
                if isinstance(tests, dict):
                    for scenario in tests:
                        s = str(scenario).strip()
                        if s:
                            rel = p.resolve().relative_to(repo_root.resolve()).as_posix()
                            mapping.setdefault(s, set()).add(rel)
            except Exception:
                continue
    return mapping


def compare_quarantine_files(file1, file2, scenario_map):
    """Compare two quarantine files and return added/removed configurations."""
    print("Comparing quarantine files:")
    print(f"  File 1: {file1}")
    print(f"  File 2: {file2}")

    configurations1 = get_all_configurations(file1)
    configurations2 = get_all_configurations(file2)

    expanded_add = expand_patterns(sorted(set(configurations1.keys())), scenario_map.keys())
    expanded_del = expand_patterns(sorted(set(configurations2.keys())), scenario_map.keys())

    added_configurations = expanded_add - expanded_del
    removed_configurations = expanded_del - expanded_add
    return added_configurations, removed_configurations


if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(
        description="Compare two quarantine.yaml files and report added/removed configurations."
    )
    parser.add_argument("file1", type=Path, help="First quarantine file")
    parser.add_argument("file2", type=Path, help="Second quarantine file")
    parser.add_argument("--outdir", type=Path, default=Path("."), help="Directory for output txt files")
    parser.add_argument("--repo-root", default=".", help="Repository root (default: .)")
    
    args = parser.parse_args()

    file1 = args.file1
    file2 = args.file2
    outdir_arg = args.outdir
    root = Path(args.repo_root).resolve()

    if file1.stem != file2.stem:
        print("Error: file1 and file2 must have the same stem.")
        sys.exit(1)

    # Determine suffix for output files
    suffix = file1.stem.split("quarantine")[1]

    # Determine output directory: if provided, resolve relative paths against cwd
    # and create the directory (parents=True). If not provided, use current working dir.
    if outdir_arg:
        outdir = Path(outdir_arg).resolve(strict=False)
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            print(f"Error: insufficient permissions to create output directory '{outdir}'.")
            sys.exit(1)
        except Exception as e:
            print(f"Error: unable to create output directory '{outdir}': {e}")
            sys.exit(1)
    else:
        outdir = Path.cwd()

    scenario_map = discover_scenarios(root)

    print(f"Writing reports to: {outdir}")
    added_configurations, removed_configurations = compare_quarantine_files(file1, file2, scenario_map)

    # Report results
    if removed_configurations:
        print(f"Configurations REMOVED ({len(removed_configurations)}):")
        for config in sorted(removed_configurations):
            print(f"  - {config}")
        print()
    else:
        print("No configurations removed.")
        print()

    if added_configurations:
        print(f"Configurations ADDED ({len(added_configurations)}):")
        for config in sorted(added_configurations):
            print(f"  + {config}")
        print()
    else:
        print("No configurations added.")
        print()

    # Summary
    total_changes = len(added_configurations) + len(removed_configurations)
    if total_changes == 0:
        print("No changes detected between the files.")
    else:
        print(f"Total changes: {total_changes} ({len(added_configurations)} added, {len(removed_configurations)} removed)")

    with open(outdir / f"configurations_added{suffix}.txt", "w") as report_file:
        for config in sorted(added_configurations):
            report_file.write(f"{config}\n")    
    with open(outdir / f"configurations_removed{suffix}.txt", "w") as report_file:
        for config in sorted(removed_configurations):
            report_file.write(f"{config}\n")
    # TODO: WRITE SCENARIO_MAP and REUSE IN NEXT WORKFLOW