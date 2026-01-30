#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
from typing import Dict, Any, Tuple, List

import pandas as pd
import plotly.express as px
from jinja2 import Template


# ----------------------------- CLI ---------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Generate HTML report with pie charts and a folder tree from a CSV report."
    )
    p.add_argument("csv", help="Path to the CSV report (columns: path, partitions_present)")
    p.add_argument("-o", "--output", default="report.html", help="Output HTML file name")
    return p.parse_args()


# --------------------------- Helpers --------------------------------
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim whitespace and normalize column names for robustness."""
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    return df


def coerce_to_bool(series: pd.Series) -> pd.Series:
    """Coerce a column of strings/ints/bools into clean Python booleans."""
    truthy = {"true", "1", "yes", "y", "t"}
    falsy = {"false", "0", "no", "n", "f"}

    def _to_bool(x):
        if isinstance(x, bool):
            return x
        if pd.isna(x):
            return False
        s = str(x).strip().lower()
        if s in truthy:
            return True
        if s in falsy:
            return False
        # Default fallback: anything non-empty not recognized becomes False
        # (safer for your use-case; adjust if needed)
        return False

    return series.apply(_to_bool)


def extract_platform_from_path(path: str, preferred_root: str = "twister-out") -> str:
    """Extract platform as the folder immediately after preferred_root. Fallbacks included."""
    parts = path.strip("/").split("/")
    if not parts:
        return "(unknown)"

    # Preferred pattern: ".../twister-out/<platform>/..."
    if preferred_root in parts:
        idx = parts.index(preferred_root)
        if idx + 1 < len(parts):
            return parts[idx + 1]
        return "(unknown)"

    # Fallback: use the first segment as "platform"
    return parts[0]


# ------------------------ Tree construction -------------------------
def new_node() -> Dict[str, Any]:
    return {"children": {}, "partitions": []}  # partitions: list[bool] only at leaves


def insert_path(root_node: Dict[str, Any], parts: List[str], has_partition: bool):
    """Insert a path into the tree and record the leaf partition flag."""
    node = root_node
    for p in parts:
        if p not in node["children"]:
            node["children"][p] = new_node()
        node = node["children"][p]
    node["partitions"].append(has_partition)


def aggregate_color(node: Dict[str, Any]) -> Tuple[int, int, str]:
    """
    Recursively aggregate counts and determine node color.
    Returns (true_count, total_count, color)
      - green  : all true
      - red    : all false
      - orange : mixed
      - grey   : no data
    """
    true_count = sum(1 for v in node["partitions"] if v)
    total_count = len(node["partitions"])

    for child in node["children"].values():
        t, n, _ = aggregate_color(child)
        true_count += t
        total_count += n

    if total_count == 0:
        color = "grey"
    elif true_count == 0:
        color = "red"
    elif true_count == total_count:
        color = "green"
    else:
        color = "orange"

    node["_agg"] = {"true": true_count, "total": total_count, "color": color}
    return true_count, total_count, color


def convert_for_js(name: str, node: Dict[str, Any]) -> Dict[str, Any]:
    agg = node.get("_agg", {"true": 0, "total": 0, "color": "grey"})
    t = agg["true"]
    n = agg["total"]
    pct = 0 if n == 0 else round((t / n) * 100)

    return {
        "name": name,
        "color": agg["color"],
        "true": t,
        "total": n,
        "pct": pct,
        "children": [
            convert_for_js(child_name, child_node)
            for child_name, child_node in sorted(node["children"].items())
        ],
    }


# --------------------------- Charts ---------------------------------
def overall_pie(has_partition_series: pd.Series) -> str:
    """Return HTML snippet for the overall pie chart (with Plotly JS embedded)."""
    true_count = int(has_partition_series.sum())
    false_count = int((~has_partition_series).sum())
    df_counts = pd.DataFrame(
        {"status": ["Has partition", "No partition"], "count": [true_count, false_count]}
    )
    fig = px.pie(
        df_counts,
        names="status",
        values="count",
        color="status",
        color_discrete_map={"Has partition": "green", "No partition": "red"},
        title="Overall Partition Presence",
    )
    # Embed Plotly JS once for fully offline viewing:
    return fig.to_html(include_plotlyjs=True, full_html=False)


def platform_pies(df: pd.DataFrame) -> List[Tuple[str, str]]:
    """Return list of (platform, html_snippet) for each platform pie chart."""
    results = []
    for platform, grp in df.groupby("platform", sort=True):
        true_count = int(grp["has_partition"].sum())
        false_count = int((~grp["has_partition"]).sum())
        pdf = pd.DataFrame(
            {
                "status": ["Has partition", "No partition"],
                "count": [true_count, false_count],
            }
        )
        fig = px.pie(
            pdf,
            names="status",
            values="count",
            color="status",
            color_discrete_map={"Has partition": "green", "No partition": "red"},
            title=f"Platform: {platform}",
        )
        # No need to re-embed plotly (already included by overall pie):
        results.append((platform, fig.to_html(include_plotlyjs=False, full_html=False)))
    return results


# --------------------------- Template -------------------------------
HTML_TEMPLATE = Template(
    r"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Partition Report</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { margin-bottom: 0.2rem; }
        .meta { color: #666; margin-bottom: 1.5rem; }

        h2 { margin-top: 2rem; }

        /* Tree styles */
        .tree-node { cursor: pointer; user-select: none; }
        .green  { color: #2e7d32; font-weight: 600; }
        .red    { color: #c62828; font-weight: 600; }
        .orange { color: #ef6c00; font-weight: 600; }
        .grey   { color: #757575; }

        .children { display: none; margin-left: 18px; padding-left: 10px; border-left: 1px dashed #ccc; }
        .node-line { margin: 4px 0; }

        .controls { margin: 8px 0 16px 0; }
        .btn { padding: 6px 10px; margin-right: 8px; border: 1px solid #bbb; border-radius: 4px; background:#f7f7f7; cursor:pointer; }
        .btn:hover { background:#eee; }
        .progress-container {
            display: inline-block;
            width: 80px;
            height: 10px;
            background: #ddd;
            border-radius: 4px;
            margin-left: 8px;
            vertical-align: middle;
        }
        .progress-bar {
            height: 100%;
            background: #4caf50;
            border-radius: 4px;
        }
        .percent-label {
            margin-left: 6px;
            font-size: 0.85rem;
            color: #555;
        }
    </style>

    <script>
        // Simple toggle/expand/collapse helpers
        function toggle(id) {
            var elem = document.getElementById(id);
            if (!elem) return;
            elem.style.display = (elem.style.display === "none") ? "block" : "none";
        }
        function expandAll() {
            var elems = document.getElementsByClassName('children');
            for (var i = 0; i < elems.length; i++) elems[i].style.display = "block";
        }
        function collapseAll() {
            var elems = document.getElementsByClassName('children');
            for (var i = 0; i < elems.length; i++) elems[i].style.display = "none";
        }

        // Tree data injected from Python
        var treeData = {{ tree_json | safe }};

        function renderTree(node, id_prefix) {
            var node_id = id_prefix + "_children";
            var html = "";

            var barWidth = node.pct; // 0–100

            html += "<div class='node-line'>";
            html += "<span class='tree-node " + node.color + "' onclick='toggle(\"" + node_id + "\")'>";
            html += node.name + "</span>";

            // Progress bar
            html += "<div class='progress-container'>";
            html += "<div class='progress-bar' style='width:" + barWidth + "%;'></div>";
            html += "</div>";

            // Percentage label
            html += "<span class='percent-label'>" + node.pct + "%</span>";

            html += "</div>";

            if (node.children && node.children.length > 0) {
                html += "<div class='children' id='" + node_id + "'>";
                for (var i = 0; i < node.children.length; i++) {
                    html += renderTree(node.children[i], id_prefix + "_" + i);
                }
                html += "</div>";
            }
            return html;
        }

        window.onload = function() {
            var container = document.getElementById("tree-container");
            if (!treeData) {
                container.innerHTML = "<em>No tree data</em>";
                return;
            }
            container.innerHTML =
                "<div class='controls'>" +
                "<button class='btn' onclick='expandAll()'>Expand all</button>" +
                "<button class='btn' onclick='collapseAll()'>Collapse all</button>" +
                "</div>" +
                renderTree(treeData, "node");

            // By default: collapse all, then expand root for context
            collapseAll();
            var root = document.getElementById("node_children");
            if (root) root.style.display = "block";
        }
    </script>
</head>
<body>

<h1>Partition File Report</h1>
<div class="meta">Generated from <code>{{ csv_path }}</code></div>

<h2>Overall Partition Presence</h2>
{{ overall_chart | safe }}

<h2>Folder Tree (LCOV Style)</h2>
<div id="tree-container"></div>

<h2>Per‑Platform Partition Statistics</h2>
{% for platform, chart in platform_charts %}
    <h3>{{ platform }}</h3>
    {{ chart | safe }}
{% endfor %}

</body>
</html>
"""
)


# --------------------------- Main -----------------------------------
def main():
    args = parse_args()

    if not os.path.isfile(args.csv):
        raise FileNotFoundError(f"CSV file not found: {args.csv}")

    # Load and normalize CSV
    df = pd.read_csv(args.csv)
    df = normalize_columns(df)

    # Column resolution (robust to trailing spaces)
    col_map = {c.strip().lower(): c for c in df.columns}
    if "path" not in col_map or "partitions_present" not in col_map:
        raise ValueError(
            f"CSV must contain 'path' and 'partitions_present' columns. Found: {list(df.columns)}"
        )
    path_col = col_map["path"]
    part_col = col_map["partitions_present"]

    # Normalize partition flag to boolean
    df["has_partition"] = coerce_to_bool(df[part_col])

    # Extract platform
    df["platform"] = df[path_col].astype(str).apply(extract_platform_from_path)

    # -------- Build tree --------
    # Prefer root name 'twister-out' if present, else use the first segment of first path
    first_path = str(df[path_col].iloc[0]).strip("/")
    first_parts = first_path.split("/") if first_path else ["root"]
    preferred_root = "twister-out" if any(p.strip("/").startswith("twister-out") for p in df[path_col].astype(str)) else first_parts[0] or "root"

    tree_root = new_node()

    for _, row in df.iterrows():
        raw_path = str(row[path_col]).strip("/")
        parts = raw_path.split("/") if raw_path else []
        # Drop the preferred_root from insertion so the displayed root is that name
        if parts and parts[0] == preferred_root:
            parts = parts[1:]
        insert_path(tree_root, parts, bool(row["has_partition"]))

    # Aggregate colors & convert for JS
    aggregate_color(tree_root)
    tree_js = convert_for_js(preferred_root, tree_root)

    # -------- Charts --------
    overall_html = overall_pie(df["has_partition"])
    platform_charts = platform_pies(df)

    # -------- HTML out --------
    html = HTML_TEMPLATE.render(
        csv_path=os.path.abspath(args.csv),
        overall_chart=overall_html,
        platform_charts=platform_charts,
        tree_json=json.dumps(tree_js),
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Report generated: {args.output}")


if __name__ == "__main__":
    main()
