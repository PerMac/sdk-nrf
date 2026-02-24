
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import glob

import pandas as pd
import plotly.express as px
from jinja2 import Template

# ----------------------------- CLI ------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(
        description="Generate HTML report from all CSV files in a directory."
    )
    p.add_argument(
        "csv_dir",
        help="Path to a directory containing CSV reports"
    )
    p.add_argument(
        "-o", "--output",
        default="report.html",
        help="Output HTML file name"
    )
    return p.parse_args()

# ----------------------------- Helpers --------------------------------------
def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    return df

def coerce_to_bool(series: pd.Series) -> pd.Series:
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
        return False

    return series.apply(_to_bool)

# ----------------------------- Tree construction ----------------------------
def new_node():
    return {"children": {}, "partitions": []}

def insert_path(root_node, parts, has_partition):
    node = root_node
    for p in parts:
        if p not in node["children"]:
            node["children"][p] = new_node()
        node = node["children"][p]
    node["partitions"].append(has_partition)

def aggregate_color(node):
    true_count = sum(1 for v in node["partitions"] if v)
    total_count = len(node["partitions"])

    for child in node["children"].values():
        t, n, _ = aggregate_color(child)
        true_count += t
        total_count += n

    if total_count == 0:
        color = "grey"
    elif true_count == 0:
        color = "green"
    elif true_count == total_count:
        color = "red"
    else:
        color = "orange"

    node["_agg"] = {"true": true_count, "total": total_count, "color": color}
    return true_count, total_count, color

def convert_for_js(name, node):
    agg = node.get("_agg", {"true": 0, "total": 0, "color": "grey"})
    t = agg["true"]
    n = agg["total"]
    resolved = n - t
    pct = 0 if n == 0 else round((resolved / n) * 100)

    return {
        "name": name,
        "color": agg["color"],
        "true": t,
        "total": n,
        "resolved": resolved,
        "pct": pct,
        "children": [
            convert_for_js(child_name, child_node)
            for child_name, child_node in sorted(node["children"].items())
        ],
    }

# ----------------------------- Pie Chart ------------------------------------
def overall_pie(series):
    df_counts = pd.DataFrame(
        {
            "status": ["Has partition", "No partition"],
            "count": [int(series.sum()), int((~series).sum())],
        }
    )
    fig = px.pie(
        df_counts,
        names="status",
        values="count",
        color="status",
        color_discrete_map={"Has partition": "#c62828", "No partition": "#2e7d32"},
        title="Overall Partition Status (Red = still present, Green = removed)",
    )
    return fig.to_html(include_plotlyjs=True, full_html=False)


# --------------------------- Template -------------------------------
HTML_TEMPLATE = Template(
r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Partition Removal Report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">

<style>
    :root{
        --bg:#0b0d10;
        --panel:#151a21;
        --text:#e6e8ea;
        --muted:#a0a8b2;
        --border:#20262e;
        --green:#2e7d32;
        --red:#c62828;
        --orange:#ef6c00;
        --grey:#757575;
    }
    body{
        margin:0; padding:0;
        font-family: system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, "Helvetica Neue", Arial;
        background:var(--bg); color:var(--text);
    }
    .content{
        max-width:1100px; margin:26px auto; padding:0 16px;
        display:flex; flex-direction:column; gap:26px;
    }
    .card{
        background:var(--panel);
        padding:18px 20px;
        border-radius:14px;
        border:1px solid var(--border);
        box-shadow:0 6px 18px rgba(0,0,0,0.25);
    }
    h2{ margin:0 0 10px 0; font-size:20px; }
    .muted{ color:var(--muted); }

    /* Tree */
    .tree-controls{
        display:flex; gap:8px; align-items:center; margin-bottom:10px;
        flex-wrap:wrap;
    }
    .btn{
        padding:6px 12px; border-radius:8px;
        background:transparent; border:1px solid var(--border);
        color:var(--text); cursor:pointer;
    }
    .btn:hover{ background:rgba(255,255,255,0.05); }

    .tree-search{
        padding:6px 10px; border-radius:8px;
        border:1px solid var(--border);
        background:transparent; color:var(--text);
        width:260px;
    }

    .node-line{ display:flex; align-items:center; gap:10px; margin:6px 0; }
    .tree-node{ cursor:pointer; user-select:none; }

    .green{ color:var(--green); font-weight:700; }
    .red{ color:var(--red); font-weight:700; }
    .orange{ color:var(--orange); font-weight:700; }
    .grey{ color:var(--grey); }

    .children{
        display:none; margin-left:18px; padding-left:12px;
        border-left:1px dashed var(--border);
    }

    /* Progress bar: green = resolved */
    .progress-container{
        width:120px; height:10px; background:#293039;
        border-radius:6px; overflow:hidden;
    }
    .progress-bar{
        height:100%; background:var(--green);
        border-radius:6px;
    }
    .percent-label{
        min-width:40px; text-align:right;
        font-variant-numeric: tabular-nums;
    }
</style>


<script>
    function toggle(id){
        var e=document.getElementById(id);
        if(e) e.style.display = (e.style.display==="none")?"block":"none";
    }
    function expandAll(){
        var e=document.getElementsByClassName('children');
        for(var i=0;i<e.length;i++) e[i].style.display="block";
    }
    function collapseAll(){
        var e=document.getElementsByClassName('children');
        for(var i=0;i<e.length;i++) e[i].style.display="none";
    }

    function normalize(s){ return (s||"").toLowerCase(); }

    function searchTree(){
        var q = normalize(document.getElementById("tree-search").value);
        var container = document.getElementById("tree-container");
        var lines = container.querySelectorAll(".node-line");

        if(!q){
            lines.forEach(l => l.style.background="transparent");
            collapseAll();
            let r = document.getElementById("node_children");
            if(r) r.style.display="block";
            return;
        }

        expandAll();
        lines.forEach(l => {
            let txt = normalize(l.textContent);
            l.style.background = txt.includes(q) ? "rgba(62,166,255,0.18)" : "transparent";
        });
    }

    var treeData = {{ tree_json | safe }};

    function renderTree(node, prefix){
        var id = prefix + "_children";
        var html = "";

        html += "<div class='node-line'>";
        html += "<span class='tree-node " + node.color +
                "' onclick='toggle(\"" + id + "\")'>" +
                node.name + "</span>";

        html += "<div class='progress-container'><div class='progress-bar' style='width:" +
                node.pct + "%;'></div></div>";

        html += "<span class='percent-label'>" + node.pct + "%</span>";
        html += "<span class='muted'>(" + node.resolved + "/" + node.total + ")</span>";

        html += "</div>";

        if(node.children && node.children.length>0){
            html += "<div class='children' id='" + id + "'>";
            for(var i=0;i<node.children.length;i++){
                html += renderTree(node.children[i], prefix + "_" + i);
            }
            html += "</div>";
        }
        return html;
    }

    window.onload = function(){
        var container=document.getElementById("tree-container");
        container.innerHTML = renderTree(treeData, "node");

        collapseAll();
        var root=document.getElementById("node_children");
        if(root) root.style.display="block";
    }
</script>

</head>
<body>

<div class="content">

    <div class="card">
        <h2>Overall Partition Status</h2>
        <div class="muted">CSV: <code>{{ csv_path }}</code></div>
        {{ overall_chart | safe }}
    </div>

    <div class="card">
        <h2>Folder Tree (platform at the end)</h2>
        <div class="muted">
            Paths are reorganized so the <strong>platform</strong> appears as the <strong>last segment</strong>.
            Bars show the percentage of directories already fixed (green = resolved).
        </div>

        <div class="tree-controls">
            <input id="tree-search" class="tree-search" placeholder="Search…" oninput="searchTree()">
            <button class="btn" onclick="expandAll()">Expand all</button>
            <button class="btn" onclick="collapseAll()">Collapse all</button>
        </div>

        <div id="tree-container"></div>
    </div>

</div>

</body>
</html>
"""
)


# --------------------------- Main -----------------------------------

def main():
    args = parse_args()

    # Collect CSVs in directory (NON-RECURSIVE)
    csv_paths = sorted(glob.glob(os.path.join(args.csv_dir, "*.csv")))
    if not csv_paths:
        raise FileNotFoundError(f"No CSV files found in directory: {args.csv_dir}")

    # Load and merge
    frames = []
    for p in csv_paths:
        df = pd.read_csv(p)
        df = normalize_columns(df)
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    # Normalize columns
    col_map = {c.strip().lower(): c for c in df.columns}
    if "path" not in col_map or "partitions_present" not in col_map:
        raise ValueError(
            f"CSVs must contain 'path' and 'partitions_present' columns. Found: {list(df.columns)}"
        )

    path_col = col_map["path"]
    part_col = col_map["partitions_present"]

    # Coerce partition bool
    df["has_partition"] = coerce_to_bool(df[part_col])

    # Determine root
    any_contains_twister = any("twister-out" in str(p) for p in df[path_col].astype(str))
    preferred_root = "twister-out" if any_contains_twister else "NCS"

    # Build tree
    tree_root = new_node()
    for _, row in df.iterrows():
        raw = str(row[path_col]).strip("/")
        parts = [p for p in raw.split("/") if p]
        parts[0] = "NCS"
        #remove obsolete segments from the structure
        if parts[2] in ["zephyr", "host"]:
            parts.pop(2)
        if preferred_root in parts:
            ridx = parts.index(preferred_root)
            platform = parts[ridx + 1] if (ridx + 1) < len(parts) else "(unknown-platform)"
            middle = parts[ridx + 2:] if (ridx + 2) <= len(parts) else []
            reordered = [preferred_root] + middle + [platform]
        else:
            platform = parts[0] if parts else "(unknown-platform)"
            middle = parts[1:] if len(parts) > 1 else []
            reordered = [preferred_root] + middle + [platform]

        insert_parts = reordered[1:] if reordered and reordered[0] == preferred_root else reordered
        insert_path(tree_root, insert_parts, bool(row["has_partition"]))

    aggregate_color(tree_root)
    tree_js = convert_for_js(preferred_root, tree_root)

    # Render HTML
    html = HTML_TEMPLATE.render(
        input_dir=os.path.abspath(args.csv_dir),
        csv_files=[os.path.abspath(p) for p in csv_paths],
        n_files=len(csv_paths),
        overall_chart=overall_pie(df["has_partition"]),
        tree_json=json.dumps(tree_js),
    )

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print("Report generated:", args.output)


if __name__ == "__main__":
    main()
