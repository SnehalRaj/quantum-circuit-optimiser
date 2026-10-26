#!/usr/bin/env python3
"""Build paper figures from frozen records and completed studies."""
from pathlib import Path
import gzip
import json
import math

import base_figures as base
import build_pipeline
from base_figures import plt, BLUE, ORANGE, INK, GRAY, GREEN

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load(name):
    return json.loads((DATA / name).read_text())


def overview():
    raw = json.loads(gzip.decompress((DATA / "public-submissions.json.gz").read_bytes()))
    rows = [r for r in raw["submissions"] if base.eligible(r)]
    curve = base.frontier(rows)
    own = load("frontier-summary.json")["submissions"]
    current = [r for r in own if r["current_pareto"]]
    displaced = [r for r in own if r["historical_new_point"] and not r["current_pareto"]]
    fig, axes = plt.subplots(1,2,figsize=(7.15,2.9))
    for ax in axes[:2]:
        ax.scatter([r["officialMetrics"]["qubits"] for r in rows],
                   [r["officialMetrics"]["toffoli"]/1e6 for r in rows],s=4,
                   c="#cdd3d7",alpha=.35,linewidths=0)
        ax.plot([q for q,t in curve],[t/1e6 for q,t in curve],color=INK,lw=.9)
        ax.scatter([r["Q"] for r in current],[r["T"]/1e6 for r in current],
                   color=BLUE,s=18,edgecolors="white",linewidths=.3,zorder=4)
        ax.scatter([r["Q"] for r in displaced],[r["T"]/1e6 for r in displaced],
                   facecolors="none",edgecolors=BLUE,s=21,lw=.7,zorder=4)
        ax.set_xlabel("Allocated qubits Q")
    axes[0].scatter([1270],[.793798],marker="*",s=65,c=ORANGE,zorder=5)
    axes[0].set_xlim(min(q for q,t in curve)-15,max(q for q,t in curve)+15)
    axes[0].set_ylim(min(t for q,t in curve)/1e6*.9,max(t for q,t in curve)/1e6*1.15);axes[0].set_yscale("log")
    axes[0].set_ylabel("Mean Toffolis (millions)")
    axes[0].set_title("(a) Public Q/T curve",loc="left")
    low_width = [r for r in current if r['Q'] < 900]
    left = min(low_width, key=lambda r: r['Q'])
    right = max(low_width, key=lambda r: r['Q'])
    axes[1].set_xlim(left['Q']-3,right['Q']+4)
    axes[1].set_ylim(min(r['T'] for r in low_width)/1e6-.2,
                     max(r['T'] for r in low_width)/1e6+.4)
    axes[1].set_title("(b) Packed inversion region",loc="left")
    axes[1].set_ylabel("Mean Toffolis (millions)")
    axes[1].annotate(f"{left['Q']} qubits",(left['Q'],left['T']/1e6),
                     xytext=(left['Q']+6,left['T']/1e6+.1),fontsize=6.8,
                     arrowprops={"arrowstyle":"-","lw":.5})
    axes[1].annotate(f"{right['Q']} qubits",(right['Q'],right['T']/1e6),
                     xytext=(right['Q']-19,right['T']/1e6+.55),fontsize=6.8,
                     arrowprops={"arrowstyle":"-","lw":.5})
    handles=[base.Line2D([0],[0],color=INK,lw=1,label="Public curve"),
             base.Line2D([0],[0],marker="o",color=BLUE,lw=0,label=f"Our frontier points ({len(current)})"),
             base.Line2D([0],[0],marker="o",mfc="none",mec=BLUE,color=BLUE,lw=0,label=f"Our displaced points ({len(displaced)})"),
             base.Line2D([0],[0],marker="*",color=ORANGE,lw=0,label="Our lowest submitted QT")]
    fig.tight_layout(rect=(0,.16,1,1),w_pad=1.2)
    fig.legend(handles=handles,loc="lower center",ncol=2,frameon=False,fontsize=6.5,bbox_to_anchor=(.5,0))
    base.save(fig,"fig1_overview")

    fig,ax=plt.subplots(figsize=(3.4,2.2))
    common={r["id"]:r for r in load("common-reliability.json")["results"] if r["status"]=="complete"}
    labels=[("public-6b0327d60e40","24 Oct. comparator"),("public-ancestor","Raw ancestor"),
            ("local-repaired","Repaired control"),("local-selected","Our lowest QT")]
    for y,(key,label) in enumerate(labels):
        r=common[key];k=r["counts"]["failed_inputs"];n=r["pairs"];p=k/n*100
        lo,hi=r["bounds"]["pointwise_two_sided95"]
        ax.errorbar(p,y,xerr=[[p-lo*100],[hi*100-p]],fmt="o",ms=3.5,
                         c=ORANGE if key=="local-selected" else GRAY,capsize=2,lw=.8)
        ax.text(.405,y,str(k),fontsize=6.6,ha="right",va="center")
    ax.set_yticks(range(4),[x[1] for x in labels],fontsize=6.4)
    ax.set_xlim(-.01,.42);ax.set_xticks([0,.15,.3]);ax.invert_yaxis()
    ax.set_xlabel("Failed inputs (%)")
    ax.set_title("Matched reliability: 32,768 inputs each",loc="left")
    fig.tight_layout()
    base.save(fig,"fig_approximation_reliability")

    fig=plt.figure(figsize=(7.15,1.65))
    contributors=load('frontier-users.json')
    table_ax=fig.add_axes([.005,.015,.99,.97]);table_ax.axis('off')
    cells=[]
    for i,r in enumerate(contributors['rows'],1):
        qrange=f"Q{r['minimum_Q']}" if r['minimum_Q']==r['maximum_Q'] else f"Q{r['minimum_Q']}–Q{r['maximum_Q']}"
        cells.append([r['user'],f"{r['share_percent']:.1f}%",r['curve_points'],r['public_submissions'],f"{r['distinct_commit_yield_percent']:.1f}%",qrange])
    table=table_ax.table(cellText=cells,colLabels=['User','Frontier share','Curve points','Submissions','Successful\ncommits (%)','Q range on curve'],
                         cellLoc='center',colWidths=[.22,.14,.13,.14,.17,.20],bbox=[0,0,1,.96])
    table.auto_set_font_size(False);table.set_fontsize(7)
    for (ri,ci),cell in table.get_celld().items():
        cell.set_edgecolor('#c6d0d5');cell.set_linewidth(.35)
        if ri==0:
            cell.set_facecolor('#eef2f4');cell.get_text().set_weight('bold')
            cell.set_height(cell.get_height()*1.65)
        elif contributors['rows'][ri-1]['user']=='SnehalRaj':
            cell.set_facecolor('#e5f1f7');cell.get_text().set_color(BLUE);cell.get_text().set_weight('bold')
    base.save(fig,"table_frontier_contributors")


def families():
    data=load("family-census.json")["summary"]
    fig,axes=plt.subplots(1,2,figsize=(7.15,3.0),gridspec_kw={"width_ratios":[1,1]})
    for ax,key,title in zip(axes,["eligible_rows","frontier_coordinates"],
                            ["(a) Classified public submissions","(b) Frontier coordinates at cutoff"]):
        selected = [r for r in data if r['family'] != 'Unclassified' or key == 'frontier_coordinates']
        ordered=sorted(selected,key=lambda r:(-r[key],r["family"]))
        labels=[r["family"].replace(" and "," & ") for r in ordered]
        vals=[r[key] for r in ordered];ys=list(range(len(ordered)))
        ax.barh(ys,vals,color=BLUE,height=.65)
        ax.set_yticks(ys,labels,fontsize=6.5);ax.invert_yaxis()
        maxv=max(vals);ax.set_xlim(0,maxv*1.18)
        for y,v in enumerate(vals):ax.text(v+maxv*.02,y,str(v),fontsize=6.7,va="center")
        ax.set_title(title,loc="left");ax.set_xlabel("Count")
    fig.tight_layout(w_pad=1.5);base.save(fig,"fig3_families")


def ablations():
    if (DATA / 'point-addition-search.json').exists():
        return point_addition_search()
    gp=DATA/"grammar-ablation.json";mp=DATA/"model-pilot.json"
    if not gp.exists():return
    grammar=load("grammar-ablation.json")
    if grammar.get("status")!="complete":return
    fig,axes=plt.subplots(1,3,figsize=(7.15,2.5))
    for x,r in enumerate(grammar["arms"]):
        vals=[v["remaining"] for v in r["seed_scores"]]
        axes[0].scatter([x-.08,x,x+.08],vals,c=BLUE,s=15,zorder=3)
        axes[0].plot([x-.24,x+.24],[r["median_remaining"]]*2,c=INK,lw=1.5)
        if r["arm"] in grammar["best_arms"]:
            axes[0].plot(x,r["median_remaining"]-.009,"*",c=ORANGE,ms=8)
    axes[0].set_xticks(range(5),[r["arm"] for r in grammar["arms"]])
    axes[0].set_ylabel("Weighted remaining cost R")
    axes[0].set_title("(a) Grammar components",loc="left")
    axes[0].set_ylim(.43,.53)
    axes[0].set_xlabel("Version (60 slots per start)")
    if mp.exists():
        model=load("model-pilot.json")
    else:model={"arms":[]}
    all_values=[s['remaining'] for a in grammar['arms'] for s in a['seed_scores']]
    all_values += [s['remaining'] for a in model.get('arms',[]) for s in a.get('seed_scores',[])]
    ylimits=(min(all_values)-.018,max(all_values)+.018)
    axes[0].set_ylim(*ylimits)
    providers=["offline","claude","codex"]
    for ax,condition,title in zip(axes[1:],["guarded-seeds-v1","guarded-cleanup-only-v1"],
                                  ["(b) Full shared path","(c) Cleanup shared only"]):
        for x,provider in enumerate(providers):
            matches=[r for r in model.get("arms",[]) if r["condition"]==condition and r["provider"]==provider]
            if not matches:continue
            r=matches[0]
            if r.get("median_remaining") is None:continue
            vals=[v["remaining"] for v in r["seed_scores"]]
            ax.scatter([x-.08,x,x+.08],vals,c=[GRAY,BLUE,ORANGE][x],s=15,zorder=3)
            ax.plot([x-.23,x+.23],[r["median_remaining"]]*2,c=INK,lw=1.5)
        ax.set_xticks(range(3),["No LLM","Opus 5.5","GPT 6.1\nSol"],fontsize=6.5)
        ax.set_title(title,loc="left");ax.set_xlabel("Proposer (24 slots per start)")
        ax.set_ylim(*ylimits)
        if not model.get("arms"):ax.text(.5,.5,"Study running",transform=ax.transAxes,ha="center",color=GRAY)
    fig.tight_layout(w_pad=1.2);base.save(fig,"fig4_ablations")


def point_addition_search():
    study = load('point-addition-search.json')
    assert study['status'] == 'complete'
    fig, axes = plt.subplots(1, 2, figsize=(7.15, 2.7),
                             gridspec_kw={'width_ratios': [1, 1.5]})
    rows = study['grammar_capacity']
    axes[0].bar(range(len(rows)), [r['QT'] / 1e6 for r in rows], color=BLUE, width=.55)
    axes[0].axhline(study['prepared_parent']['QT'] / 1e6, color=GRAY, ls='--', lw=.8,
                   label='Prepared parent')
    axes[0].set_xticks(range(len(rows)), ['Cleanup', '+ register\nrelease', '+ coordinate\nconstruction'])
    axes[0].set_ylabel(r'Best $Q\times T$ (millions)')
    axes[0].set_title('(a) Cost reachable with each grammar', loc='left')
    axes[0].legend(frameon=False, fontsize=6.5, loc='lower left')
    styles = [('random', 'Random', GRAY, 's'), ('evolution', 'Evolution', GREEN, 'o'),
              ('claude', 'Claude', ORANGE, '^'), ('codex', 'GPT', BLUE, 'D')]
    for index, (arm, label, color, marker) in enumerate(styles):
        points = sorted((r for r in study['search_summaries'] if r['arm'] == arm),
                        key=lambda r: r['proposals'])
        x = [r['proposals'] for r in points]
        y = [r['median_gain_percent'] for r in points]
        axes[1].plot(x, y, color=color, marker=marker, markersize=3.5, lw=1, label=label)
        for row in points:
            values = row['seed_gains_percent']
            jitter = [(index - 1.5) * .06 + (j - 2) * .015 for j in range(len(values))]
            axes[1].scatter([row['proposals'] + j for j in jitter], values,
                            color=color, s=8, alpha=.35, linewidths=0)
    axes[1].set_xticks([1, 2, 4, 8])
    axes[1].set_ylim(bottom=0)
    axes[1].set_xlabel('Proposals from the same prepared parent')
    axes[1].set_ylabel(r'Reduction in $Q\times T$ (%)')
    axes[1].set_title('(b) Search within the full grammar', loc='left')
    axes[1].legend(frameon=False, fontsize=6.5, ncol=2, loc='lower right')
    fig.tight_layout(w_pad=1.8)
    base.save(fig, 'fig4_ablations')


def main():
    base.FIG.mkdir(exist_ok=True)
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":7.1,
                         "axes.labelsize":7,"axes.titlesize":7.6,
                         "xtick.labelsize":6.4,"ytick.labelsize":6.6,
                         "axes.spines.top":False,"axes.spines.right":False,
                         "axes.edgecolor":GRAY,"axes.linewidth":.6,
                         "pdf.fonttype":42,"svg.fonttype":"none"})
    overview();families();ablations();build_pipeline.main();base.public_table();base.strategy_figure();base.reliability_figure()


if __name__=="__main__":main()
