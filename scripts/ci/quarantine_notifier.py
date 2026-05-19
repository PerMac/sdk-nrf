
#!/usr/bin/env python3
# Copyright (c) 2025 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
"""
Qarantine notifier

INPUTS:
  --added-file  configurations_added.txt   # lines: ("scenario","platform")
  --removed-file configurations_removed.txt # lines: ("scenario","platform")
  --repo-root .                            # repo root for scanning YAML tests and CODEOWNERS
  --ref <sha>                              # head sha for blob URLs in the comment
  --scenario-map scenario_map.txt          # list of all scenarios and their defining paths (for codeowners resolution)

OUTPUTS:
  * quarantine_comment.md                  # Markdown body to post

No GitHub API calls here; the workflow will post the comment and upload artifacts.
"""

import argparse
import ast
import json
import os
import pathspec
import re
import sys
from collections.abc import Iterable
from pathlib import Path

try:
    import yaml  # PyYAML
except Exception:
    print("ERROR: PyYAML is required (pip install pyyaml).", file=sys.stderr)
    raise


# ---------------- CLI ----------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=("Prepare quarantine owners notification comment from configuration files."),
        allow_abbrev=False,
    )
    p.add_argument("--repo-root", default=".", help="Repository root (default: .)")
    p.add_argument(
        "--added-file",
        required=True,
        help="Path to configurations_added.txt (lines: ('scenario','platform'))",
    )
    p.add_argument(
        "--removed-file",
        required=True,
        help="Path to configurations_removed.txt (lines: ('scenario','platform'))",
    )
    p.add_argument(
        "--output", default="quarantine_comment.md", help="Output Markdown file with comment body."
    )
    p.add_argument(
        "--ref",
        default=os.environ.get("GITHUB_SHA", "main"),
        help="Git ref/sha used for blob links in comment (default: env GITHUB_SHA or 'main').",
    )
    p.add_argument(
        "--scenario-map",
        default="scenario_map.txt",
        help="Path to scenario map file (lines: ('scenario','defining_path')) for codeowners resolution.",
    )
    return p.parse_args()


# ---------------- CODEOWNERS parsing & matching ----------------
CODEOWNERS_PATH = "CODEOWNERS"
CODEOWNER_LINE_RE = re.compile(r"^\s*([^\s#][^\s]*)\s+(.+?)\s*$")
FIND_MY = "find_my"


def load_codeowners(repo_root: Path) -> list[tuple[str, list[str]]]:
    path = repo_root / CODEOWNERS_PATH
    if not path.exists():
        return []
    rules: list[tuple[str, list[str]]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = CODEOWNER_LINE_RE.match(s)
        if not m:
            continue
        pattern, owners_str = m.groups()
        owners = [tok for tok in owners_str.split() if tok.startswith("@")]
        if owners:
            rules.append((pattern, owners))
    return rules


def compile_pathspecs(rules):
    compiled = []
    for pattern, owners in rules:
        spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
        compiled.append((spec, owners))
    return compiled


def find_owners(filepath: str, compiled_specs: list[tuple[str, list[str]]]) -> set[str]:
    matched_owners = set()
    for spec, owners in compiled_specs:
        if spec.match_file(filepath):
            matched_owners = owners
    return matched_owners


# ---------------- Comment formatting ----------------
COMMENT_MARKER = "<!-- quarantine-notifier -->"
ALL_PLATFORMS_TOKEN = "__ALL_PLATFORMS__"
ALL_SCENARIOS_TOKEN = "__ALL_SCENARIOS__"


def make_comment(
    owner_to_added: dict[str, list[tuple[str, str]]],
    owner_to_removed: dict[str, list[tuple[str, str]]],
    unowned_added: list[tuple[str, str]],
    unowned_removed: list[tuple[str, str]],
    repo_full: None | str,
    scenario_to_added_platforms: dict[str, set[str]],
    scenario_to_removed_platforms: dict[str, set[str]],
    platform_only_added: set[str],
    platform_only_removed: set[str],
) -> str:
    all_owner_keys = sorted(
        set(owner_to_added.keys()) | set(owner_to_removed.keys()), key=str.lower
    )
    any_owned = bool(all_owner_keys)
    any_unowned = bool(unowned_added or unowned_removed)

    if not any_owned and not any_unowned and not platform_only_added and not platform_only_removed:
        return ""  # nothing to notify

    def link(path: str) -> str:
        return f"{path}" if repo_full else path

    def section(title: str, items: list[str], lines: list[str]):
        if items:
            lines.append(f"### {title}")
            lines.extend(items)
            lines.append("")

    lines: list[str] = []
    lines.append(COMMENT_MARKER)
    lines.append("**Quarantine update – notifying maintainers**\n")

    for key in all_owner_keys:
        owners = [o.strip() for o in key.split(",") if o.strip()]
        mention = ", ".join(owners) if owners else "_(no owners found)_"
        mention = mention if mention else "_(no owners found)_"
        lines.append(
            f"{mention}: Please take a note of quarantine changes for scenarios "
            f"under your maintainership."
        )

        add_lines: list[str] = []
        del_lines: list[str] = []

        for scen, path in sorted(owner_to_added.get(key, [])):
            plats = scenario_to_added_platforms.get(scen, set())
            plat_str = (
                "all platforms"
                if ALL_PLATFORMS_TOKEN in plats
                else ", ".join(sorted(plats))
            )

            if scen == ALL_SCENARIOS_TOKEN:
                scen = "all scenarios"
                
            add_lines.append(
                f"- **Added to quarantine**: `{scen}` (platforms: {plat_str}) (defined in {link(path)})"
            )

        for scen, path in sorted(owner_to_removed.get(key, [])):
            plats = scenario_to_removed_platforms.get(scen, set())
            plat_str = (
                "all platforms"
                if ALL_PLATFORMS_TOKEN in plats
                else ", ".join(sorted(plats))
            )

            if scen == ALL_SCENARIOS_TOKEN:
                scen = "all scenarios"

            del_lines.append(
                f"- **Removed from quarantine**: `{scen}` (platforms: {plat_str}) (defined in {link(path)})"
            )

        section("Added", add_lines, lines)
        section("Removed", del_lines, lines)
        lines.append("---")

    if any_unowned:
        header = "### ⚠️ Missing CODEOWNERS"
        lines.append(header)

        if unowned_added:
            lines.append("**Added to quarantine – no owners resolved:**")
            for scen, path in sorted(unowned_added):
                plats = scenario_to_added_platforms.get(scen, set())
                plat_str = (
                    "all platforms"
                    if ALL_PLATFORMS_TOKEN in plats
                    else ", ".join(sorted(plats)) if plats else "-"
                )
                lines.append(f"- `{scen}` (platforms: {plat_str}) (defined in {link(path)})")

        if unowned_removed:
            if unowned_added:
                lines.append("")
            lines.append("**Removed from quarantine – no owners resolved:**")
            for scen, path in sorted(unowned_removed):
                plats = scenario_to_removed_platforms.get(scen, set())
                plat_str = (
                    "all platforms"
                    if ALL_PLATFORMS_TOKEN in plats
                    else ", ".join(sorted(plats)) if plats else "-"
                )
                lines.append(f"- `{scen}` (platforms: {plat_str}) (defined in {link(path)})")

        lines.append("---")

    # Platform-only notices (scenario == None)
    platform_add_lines = [f"- Platform {p} is quarantined" for p in sorted(platform_only_added)]
    platform_del_lines = [f"- Platform {p} quarantine removed" for p in sorted(platform_only_removed)]
    section("Added (platform-only)", platform_add_lines, lines)
    section("Removed (platform-only)", platform_del_lines, lines)

    return "\n".join(lines).strip() + "\n"


# ---------------- Grouping ----------------
def resolve_codeowners_for_scenarios(
    scenario_to_paths: dict[str, set[str]],
    scenarios: Iterable[str],
    compiled_specs: list[tuple[pathspec.PathSpec, list[str]]],
) -> tuple[dict[str, list[tuple[str, str]]], list[tuple[str, str]]]:
    owners_map: dict[str, list[tuple[str, str]]] = {}
    unowned: list[tuple[str, str]] = []

    for scen in scenarios:
        # find-my is not part of nrf, has no codeowners and repo with scenario YAMLs is private
        if FIND_MY in scen:
            owners = ["@nrfconnect/ncs-si-bluebagel"]
            path_full = "sdk-find-my repository (private)"
        elif scen == ALL_SCENARIOS_TOKEN:
            owners = ["@nrfconnect/ncs-test-leads"]  # Full platform quarantine, assign to all test leads
            path_full = "N/A (all scenarios)"
        else:
            path_full = scenario_to_paths.get(scen, set())
            path_prefix, path = path_full.split("/", 1)
            if path_prefix != "nrf":
                owners = ["@nrfconnect/ncs-code-owners"]  # No codeowners for scenarios outside nrf/
            else:
                owners = find_owners(path, compiled_specs)
        if not owners:
            unowned.append((scen, path_full))
            continue
        key = ",".join(sorted(set(owners), key=str.lower))
        owners_map.setdefault(key, []).append((scen, path_full))

    return owners_map, unowned


def write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def load_configurations(path: Path) -> list[tuple[str | None, str | None]]:
    """
    Each non-empty line should look like: ("scenario","platform")
    Accepts 'None' (string) or actual None for either field.
    """
    if not path.exists():
        return []
    pairs: list[tuple[str | None, str | None]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        try:
            t = ast.literal_eval(s)
        except Exception:
            m = re.match(r'^\(\s*"?(.*?)"?\s*,\s*"?(.*?)"?\s*\)\s*$', s)
            if not m:
                continue
            t = (m.group(1), m.group(2))
        scen_raw = t[0]
        plat_raw = t[1]
        scen = None if (scen_raw is None or str(scen_raw).strip() == "None") else str(scen_raw).strip()
        plat = None if (plat_raw is None or str(plat_raw).strip() == "None") else str(plat_raw).strip()
        pairs.append((scen, plat))
    return pairs


def load_scenario_map(path: Path) -> dict[str, set[str]]:
    """
    Load scenario map from a file.
    Each line should look like: ("scenario": "path_to_yaml_with_it_defined")
    """
    map = {}
    with open(path, 'r') as f:
        for line in f:
            s = line.strip()
            scen, loc = s.split(":")
            map[scen] = loc.strip()
    return map


# ---------------- Main ----------------
def main() -> int:
    args = parse_args()
    root = Path(args.repo_root).resolve()
    repo_full = os.environ.get("GITHUB_REPOSITORY")

    added_path = Path(args.added_file)
    removed_path = Path(args.removed_file)
    if not added_path.exists() or not removed_path.exists():
        missing = []
        if not added_path.exists():
            missing.append(str(added_path))
        if not removed_path.exists():
            missing.append(str(removed_path))
        print(f"Configuration file(s) not found: {', '.join(missing)}", file=sys.stderr)
        Path(args.output).write_text("", encoding="utf-8")
        return 1

    added_cfg = load_configurations(added_path)
    removed_cfg = load_configurations(removed_path)

    scenario_map = load_scenario_map(args.scenario_map)
    rules = load_codeowners(root)
    compiled_specs = compile_pathspecs(rules)

    # Build scenario -> platforms maps and platform-only sets
    scenario_to_added_platforms: dict[str, set[str]] = {}
    scenario_to_removed_platforms: dict[str, set[str]] = {}
    platform_only_added: set[str] = set()
    platform_only_removed: set[str] = set()

    def add_pair(target_map: dict[str, set[str]], scen: str, plat: str | None):
        s = target_map.setdefault(scen, set())
        if plat is None:
            s.add(ALL_PLATFORMS_TOKEN)
        else:
            s.add(plat)

    for scen, plat in added_cfg:
        if scen is None and plat is not ALL_PLATFORMS_TOKEN:
            platform_only_added.add(plat)
        else:
            add_pair(scenario_to_added_platforms, scen, plat)

    for scen, plat in removed_cfg:
        if scen is None and plat is not ALL_PLATFORMS_TOKEN:
            platform_only_removed.add(plat)
        else:
            add_pair(scenario_to_removed_platforms, scen, plat)

    # expanded_add/del replace with list from input
    owned_add, unowned_add = resolve_codeowners_for_scenarios(
        scenario_map, scenario_to_added_platforms, compiled_specs
    )
    owned_del, unowned_del = resolve_codeowners_for_scenarios(
        scenario_map, scenario_to_removed_platforms, compiled_specs
    )


    body = make_comment(
        owner_to_added=owned_add,
        owner_to_removed=owned_del,
        unowned_added=unowned_add,
        unowned_removed=unowned_del,
        repo_full=repo_full, 
        scenario_to_added_platforms=scenario_to_added_platforms,
        scenario_to_removed_platforms=scenario_to_removed_platforms,
        platform_only_added=platform_only_added,
        platform_only_removed=platform_only_removed,
    )

    Path(args.output).write_text(body, encoding="utf-8")

    if body.strip():
        print("Prepared quarantine comment with maintainer mentions and platforms.")
    else:
        print("No content to post (no owners matched, no platform-only items, and no unowned items).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
