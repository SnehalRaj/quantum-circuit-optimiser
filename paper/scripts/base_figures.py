#!/usr/bin/env python3
"""Build the paper figures from frozen records. Make no external calls."""
from pathlib import Path
import csv
import gzip
import json
import math
import os

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "build/mpl-cache"))
os.environ.setdefault("XDG_CACHE_HOME", str(Path(__file__).resolve().parents[1] / "build/cache"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.lines import Line2D
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIG = ROOT / "figures"
BLUE = "#176b91"
ORANGE = "#c66b24"
INK = "#243544"
GRAY = "#78848b"
GREEN = "#36806a"
ANALYSIS_CUTOFF = json.loads((DATA / "frontier-summary.json").read_text()).get("analysis_cutoff_utc", "9999")


def read(name):
    return json.loads((DATA / name).read_text())


def save(fig, name):
    for ext in ("pdf", "svg", "png"):
        target = FIG / (name + "." + ext)
        fig.savefig(target, dpi=180, bbox_inches="tight")
        if ext == 'svg':
            target.write_text('\n'.join(line.rstrip() for line in target.read_text().splitlines()) + '\n')
    plt.close(fig)


def eligible(r):
    from freeze_snapshot import eligible as at_cutoff
    return at_cutoff(r, ANALYSIS_CUTOFF)


def frontier(rows):
    points = sorted(set((r["officialMetrics"]["qubits"],
                         r["officialMetrics"]["toffoli"]) for r in rows))
    out, best = [], math.inf
    for q, t in points:
        if t < best:
            out.append((q, t))
            best = t
    return out


def public_figure():
    doc = json.loads(gzip.decompress((DATA / "public-submissions.json.gz").read_bytes()))
    rows = [r for r in doc["submissions"] if eligible(r)]
    curve = frontier(rows)
    own = read("frontier-summary.json")["submissions"]
    current = [r for r in own if r["current_pareto"]]
    old = [r for r in own if r["historical_new_point"] and not r["current_pareto"]]
    best = min(own, key=lambda r: r["S"])
    summary = read("frontier-summary.json")
    assert (len(rows), len(curve), len(current), len(old)) == (summary['eligible'], summary['frontier_coordinates'], summary['own_current_pareto'], summary['own_historical_pareto']-summary['own_current_pareto'])
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 3.25), gridspec_kw={"width_ratios": [1.1, 1]})
    for ax in axes:
        ax.scatter([r["officialMetrics"]["qubits"] for r in rows],
                   [r["officialMetrics"]["toffoli"] / 1e6 for r in rows],
                   s=6, c="#cbd1d5", alpha=.38, linewidths=0, rasterized=False)
        ax.plot([p[0] for p in curve], [p[1] / 1e6 for p in curve],
                c=INK, lw=1, marker=".", ms=2, label="Public Q/T curve")
        ax.scatter([r["Q"] for r in current], [r["T"] / 1e6 for r in current],
                   c=BLUE, edgecolors="white", linewidths=.4, s=25, zorder=4,
                   label=f"Our current points ({len(current)})")
        ax.scatter([r["Q"] for r in old], [r["T"] / 1e6 for r in old],
                   facecolors="none", edgecolors=BLUE, s=27, linewidths=.9,
                   zorder=4, label=f"Our displaced points ({len(old)})")
        ax.set_xlabel("Allocated qubits Q")
    axes[0].scatter([best["Q"]], [best["T"] / 1e6], c=ORANGE, marker="*", s=75,
                    zorder=5, label="Our lowest submitted Q × T")
    axes[0].set_yscale("log")
    axes[0].set_xlim(min(q for q,t in curve)-15, max(q for q,t in curve)+15)
    axes[0].set_ylim(min(t for q,t in curve)/1e6*.9, max(t for q,t in curve)/1e6*1.15)
    axes[0].set_ylabel("Mean executed Toffolis (millions)")
    axes[0].set_title("(a) Full frontier range", loc="left")
    axes[0].legend(loc="upper right", frameon=False, fontsize=6.3)
    axes[1].set_xlim(796, 842)
    axes[1].set_ylim(8.3, 13.1)
    axes[1].set_title("(b) Low-qubit detail", loc="left")
    axes[1].set_ylabel("Toffolis (millions)")
    axes[1].annotate("Q799", (799, 12.710488), xytext=(803, 12.85), fontsize=7,
                     arrowprops={"arrowstyle": "-", "lw": .5})
    axes[1].annotate("Q839", (839, 8.564543), xytext=(824, 8.65), fontsize=7,
                     arrowprops={"arrowstyle": "-", "lw": .5})
    fig.tight_layout(w_pad=1.1)
    save(fig, "fig1_frontier")
    public_table()


def public_table():
    own = read("frontier-summary.json")["submissions"]
    current = [r for r in own if r["current_pareto"]]
    audits = {r["public_id"]: r for r in read("reliability-evidence.json")["independent_audits"]}
    lines = [r"\begin{tabular}{rrrr}", r"\toprule", r"Q & Official T & $QT/10^9$ & Failures \\", r"\midrule"]
    for r in sorted(current, key=lambda x: x["Q"]):
        a = audits.get(r["id"], {})
        lines.append(f"{r['Q']:,} & {r['T']:,} & {r['S']/1e9:.4f} & {a.get('failed_inputs', 'Outside cohort')} " + chr(92) * 2)
    lines += [r"\bottomrule", r"\end{tabular}"]
    (DATA / "public-table.tex").write_text("\n".join(lines) + "\n")
    with (DATA / "public-table.csv").open("w") as f:
        writer = csv.DictWriter(f, lineterminator='\n', fieldnames=["id", "Q", "T", "S", "source", "published",
                                              "historical_new_point", "current_pareto"])
        writer.writeheader()
        writer.writerows(own)


def pipeline_figure():
    fig, ax = plt.subplots(figsize=(7.1, 3.25))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4.5)
    ax.axis("off")
    def box(x, y, title, detail, color=BLUE, width=2.1):
        p = FancyBboxPatch((x, y), width, .88, boxstyle="round,pad=.07,rounding_size=.08",
                           edgecolor=color, facecolor="#f3f7f8", linewidth=.9)
        ax.add_patch(p)
        ax.text(x + width / 2, y + .65, title, ha="center", va="center",
                color=INK, fontsize=7.7, weight="bold")
        ax.text(x + width / 2, y + .26, detail, ha="center", va="center",
                color=INK, fontsize=6.6, linespacing=1.25)
    xs = [.12, 2.66, 5.2, 7.74]
    top, bottom = 2.5, .72
    entries = [
        (xs[0], top, "1  Reconstruct", "Public sources and notes\nAncestry and notices"),
        (xs[1], top, "2  Encode grammar", "Blocks and guards\nActive settings"),
        (xs[2], top, "3  Propose edits", "Deterministic search\nOptional model proposals"),
        (xs[3], top, "4  Develop", "Build and check\nMeasure Q and T"),
        (xs[3], bottom, "5  Freeze", "Recipe and source\nOperation hash"),
        (xs[2], bottom, "6  Reference", "Sealed operations\nFaithful public checker"),
        (xs[1], bottom, "7  Audit", "Fresh inputs\nAdjusted failure bound"),
        (xs[0], bottom, "8  Submit", "Service verification\nRetain curve and record"),
    ]
    for x, y, title, detail in entries:
        box(x, y, title, detail, GREEN if y == bottom else BLUE)
    def arrow(start, end, dashed=False, rad=0):
        ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=9,
                                    lw=.9, color=GRAY, linestyle="--" if dashed else "-",
                                    connectionstyle=f"arc3,rad={rad}"))
    for j in range(3):
        arrow((xs[j] + 2.2, top + .44), (xs[j+1] - .1, top + .44))
        arrow((xs[j+1] - .1, bottom + .44), (xs[j] + 2.2, bottom + .44))
    arrow((8.79, top - .1), (8.79, bottom + 1))
    ax.text(8.8, 1.98, "Real-variant screen\nwhere contract permits", ha="center", fontsize=6.9)
    arrow((8.5, 3.5), (3.9, 3.5), dashed=True, rad=.25)
    ax.text(6.15, 4.13, "Development evidence updates the next study's grammar", ha="center", fontsize=7)
    ax.text(5, .12, "Frozen evaluator  ·  Recorded budgets  ·  Original source credit", ha="center", color=INK, fontsize=7.3)
    save(fig, "fig2_pipeline")


def strategy_figure():
    d = read("strategy-evidence.json")
    modes = ["cancel-algebraic", "factor", "pack", "guard", "catalog"]
    names = ["Algebraic", "Factor", "Pack", "Guard", "Catalog"]
    rows = {r["mode"]: r for r in d["strategies"]}
    fig, axes = plt.subplots(1, 3, figsize=(7.1, 2.55), gridspec_kw={"width_ratios": [1, 1, 1]})
    y = list(range(len(modes)))
    values = [rows[m]["improved_sources"] for m in modes]
    axes[0].barh(y, values, color=BLUE, height=.6)
    axes[0].set_yticks(y, names)
    axes[0].set_xlim(0, 36)
    for j, v in enumerate(values):
        axes[0].text(v + .7, j, f"{v}/36", va="center", fontsize=7)
    axes[0].set_title("(a) Sources improved", loc="left")
    axes[0].set_xlabel("Count (same 36 sources)")
    percents = [rows[m]["mean_percent_reduction"] for m in modes]
    axes[1].barh(y, percents, color=GREEN, height=.6)
    axes[1].set_yticks(y, names)
    axes[1].set_xlim(0, .12)
    for j, v in enumerate(percents):
        axes[1].text(v + .002, j, f"{v:.3f}", va="center", fontsize=6.6)
    axes[1].set_title("(b) Mean reduction", loc="left")
    axes[1].set_xlabel("Mean T reduction (%)")
    levers = [r for r in d["paired_levers"] if r["parent"] == "q839-parent"]
    axes[2].bar(["r", "u", "u + r"], [r["saving_T"] for r in levers], color=[BLUE, GREEN, ORANGE], width=.6)
    axes[2].set_ylim(0, 6200)
    for j, r in enumerate(levers):
        axes[2].text(j, r["saving_T"] + 120, f"{r['saving_T']:,.0f}", ha="center", fontsize=7)
    axes[2].set_title("(c) Pass interaction", loc="left")
    axes[2].set_xlabel("Enabled lever")
    axes[2].set_ylabel("Mean Toffolis saved")
    for ax in axes[:2]:
        ax.invert_yaxis()
    fig.tight_layout(w_pad=1.2)
    save(fig, "fig3_strategies")


def reliability_figure():
    d = read("reliability-evidence.json")
    common = {r["id"]: r for r in read("common-reliability.json")["results"] if r["status"] == "complete"}
    fig, axes = plt.subplots(1, 3, figsize=(7.1, 4.15), gridspec_kw={"width_ratios": [1.1, 1, 1.1]})
    ids = ["public-6b0327d60e40", "public-ancestor", "local-repaired", "local-selected"]
    labels = ["24 Oct. comparator", "Q1236 ancestor", "Q1292 repaired", "Q1270 selected"]
    for j, ident in enumerate(ids):
        r = common[ident]
        k, n = r["counts"]["failed_inputs"], r["pairs"]
        mean = k / n * 100
        lo, hi = r["bounds"]["pointwise_two_sided95"]
        axes[0].errorbar(mean, j, xerr=[[mean-lo*100], [hi*100-mean]], fmt="o", ms=3.5,
                         color=BLUE if ident.startswith("local") else GRAY, capsize=2, lw=.9)
        axes[0].plot(r["bounds"]["family_upper95"]*100, j, ">", ms=4, color=ORANGE)
        axes[0].text(.42, j, str(k), ha="right", va="center", fontsize=7)
    axes[0].set_yticks(range(4), labels)
    axes[0].set_xlim(-.007, .435)
    axes[0].set_title("(a) Independent panel\nn = 32,768", loc="left")
    axes[0].set_xlabel("Failed inputs (%)")
    axes[0].axvline(.1, color="#b14c48", ls="--", lw=.8)
    axes[0].invert_yaxis()
    diag = d["cof_diagnostic_comparison"]
    ys = list(range(len(diag)))
    for j, r in enumerate(diag):
        axes[1].plot(r["failed_inputs"]/r["n"]*100, j, "o", color=GRAY if j == 0 else BLUE, ms=4)
        axes[1].text(.032, j, str(r["failed_inputs"]), ha="right", va="center", fontsize=7)
    axes[1].set_yticks(ys, ["Q1065 parent", "Q1059 COF", "Q1063 COF", "Q1064 COF"])
    axes[1].set_xlim(0, .034)
    axes[1].set_xticks([0, .01, .02, .03])
    axes[1].set_title("(b) Design diagnostic\nn = 262,144", loc="left")
    axes[1].set_xlabel("Failed inputs (%)")
    axes[1].invert_yaxis()
    current = sorted([r for r in d["independent_audits"] if r["on_current_curve"]], key=lambda r:r["service_Q"])
    for j, r in enumerate(current):
        axes[2].plot(r["failed_inputs"]/r["n"]*100, j, "o", ms=3.5, mfc="white", mec=BLUE)
        axes[2].plot(r["upper"]*100, j, ">", ms=4, color=ORANGE)
        axes[2].text(.113, j, str(r["failed_inputs"]), ha="right", va="center", fontsize=6.8)
    axes[2].set_yticks(range(14), [f"Q{r['service_Q']}" for r in current])
    axes[2].set_xlim(-.004, .117)
    axes[2].set_xticks([0, .05, .1])
    axes[2].set_title("(c) 25 October audit cohort\nn = 20,480 each", loc="left")
    axes[2].set_xlabel("Failed inputs (%)")
    axes[2].axvline(.1, color="#b14c48", ls="--", lw=.8)
    axes[2].invert_yaxis()
    handles = [Line2D([0],[0],marker="o",color=BLUE,lw=0,label="Observed rate"),
               Line2D([0],[0],marker=">",color=ORANGE,lw=0,label="Family upper bound"),
               Line2D([0],[0],color="#b14c48",ls="--",lw=.8,label="Cutoff 0.1%")]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(.5,-.035), fontsize=7)
    fig.tight_layout(w_pad=1)
    save(fig, "fig4_reliability")


def main():
    FIG.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":7.3,
                         "axes.labelsize":7.3, "axes.titlesize":8,
                         "xtick.labelsize":6.8, "ytick.labelsize":6.8,
                         "axes.spines.top":False, "axes.spines.right":False,
                         "axes.edgecolor":GRAY, "axes.linewidth":.6,
                         "pdf.fonttype":42, "svg.fonttype":"none"})
    public_figure()
    pipeline_figure()
    strategy_figure()
    reliability_figure()


if __name__ == "__main__":
    main()
