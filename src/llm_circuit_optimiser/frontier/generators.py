"""Generate campaign jobs from fixed source evidence and a public curve.

The expected costs remain inferences from the campaign's recorded trees.
Only live thresholds change when the public curve changes.
"""
from .public import best_at_or_below
from ..common import save

CATALOG_FILES = {  # byte copies from our public commit 4073f09c (also ae2a603a), path src/point_add/frontier_rewrite/
    'mod.rs': '6b94ac22f2dfa224ab1fcc27c7ec2a54da09563b769ad87c35e16c95f753be53',
    'catalog.rs': 'e5b1b248c301ef26202afff7f380f96ff806a075f279b06809c51b7b808ca052'}
CATALOG_EDITS = [  # the two mod.rs lines that switch the catalog on (plus the two files above)
    ['src/point_add/mod.rs', 'mod cond_pass;', 'mod cond_pass;\nmod frontier_rewrite;'],
    ['src/point_add/mod.rs', '    ops.extend(tail);', '    ops = frontier_rewrite::apply(ops);\n    ops.extend(tail);']]
GUARD = ['src/point_add/froghop_one.rs', 'let gp = self.gp.clone();',
         'let gp: Vec<QubitId> = Vec::new(); // S5 keeps ordinary MAJ lanes: its rolling prefix and inverse repair need them']
IDS = {  # short id -> full public submission id
    'b9a40957': 'b9a40957-b59c-4d8a-90eb-d65831365ec5', 'd2583a60': 'd2583a60-f676-44b6-9e25-399dbdd273d7',
    '59359890': '59359890-410f-49b1-b4dc-c5e54ed528f0', '6d08713c': '6d08713c-4a11-4384-9fe4-53fb9f5aca02',
    'd419f804': 'd419f804-80ed-4ddd-914c-c1e1d149a706', '11613a51': '11613a51-1243-4f91-801a-9e71600d1f99',
    '7a7367a3': '7a7367a3-3cf5-43d4-94a7-0a6189d1c39c', '2c478cc4': None, 'a45bcccd': None, 'a3b6c26c': None}
TREES = {
    '818': dict(base_commit='4073f09cfa7fe733fdeb64841193282aaff293d9', base_row='b9a40957', base_desc="our Q818 row: odinfree's 818 tree (d2583a60, commit b618b19c) + levers u,r (mfsur) + the catalog, inherited nonce",
                has_catalog=True, pool_name='POOL_1', pool_file='src/point_add/froghop_one.rs', pool_now=18, q0=800, catalog_saving=20206,
                rate='0 failing of 16,384 (dev) and 0 of 40,960 (audit) on the Q818 child; odinfree measured the base at 0.27 per 9,024',
                pass_chance=0.9, deps=['d2583a60', '59359890', '6d08713c', 'd419f804', '11613a51', 'b9a40957'], lane_step=13900, pool_step=10090),
    '839': dict(base_commit='2cca36f958f6e5a818e9f693a2f95f7a2e3ece25', base_row='7a7367a3', base_desc="our Q839 row: gnuchev's 839 tree (a45bcccd) + levers u,r (mfsur); no catalog yet",
                has_catalog=False, pool_name='POOL_1', pool_file='src/point_add/froghop_one.rs', pool_now=15, q0=824, catalog_saving=894,
                rate='0 failing of 40,960 (our audit of the base)', pass_chance=0.95, deps=['a45bcccd', '59359890', '7a7367a3', '11613a51'], lane_step=7000, pool_step=5400),
    '803': dict(base_commit='8233380fbf34f30bee75dae47a567720ba4db97a', base_row='2c478cc4', base_desc="our Q803 row: odinfree's froghop-double 803 tree (a3b6c26c) + levers u,r; the catalog saves only 5 T here",
                has_catalog=False, pool_name='POOL_D', pool_file='src/point_add/froghop_double.rs', pool_now=12, q0=791, catalog_saving=0,
                rate='0 failing of 16,384 (dev)', pass_chance=0.95, deps=['a3b6c26c', '59359890', '7a7367a3', '2c478cc4'], lane_step=None, pool_step=1500)}
ROW_AT = {819: 'cac0bf90', 820: 'a406c175', 821: '1c0943d8', 822: 'aa3a77d2', 823: 'f1152e4d', 824: '38c3a08f', 825: '96c27779', 826: '8486e4c7', 827: 'c32f8901',
          828: 'e2194147', 834: '73ba61c3', 835: '289d9410', 836: '6dfce123', 837: 'c5e0f3a5', 838: 'c145c257', 847: 'c23e92b1', 852: '5e131113',
          840: '1cb98519', 841: 'e9b02548', 842: 'ddc91f06', 843: '30059028', 844: 'b8cfe493', 845: '7ba8b197', 846: 'd418a62e'}
SPEC = []  # (job id, tree, Q, kind, owner, rank)
for q in (819, 820): SPEC.append((f'q{q}-p{q-800}-cat', '818', q, 'pool', 'mac', 1))
SPEC += [('q815-p15-cat', '818', 815, 'pool', 'cluster', 3), ('q817-p17-cat', '818', 817, 'pool', 'cluster', 3)]
for q in (804, 805): SPEC.append((f'q{q}-d{q-791}', '803', q, 'pool', 'mac', 5))
for q in range(821, 828): SPEC.append((f'q{q}-p{q-800}-cat', '818', q, 'pool', 'cluster', 1))
for q in range(828, 839): SPEC.append((f'q{q}-g{q-818}-cat', '818', q, 'lane', 'cluster', 2))
for q in range(840, 847): SPEC.append((f'q{q}-p{q-824}-cat', '839', q, 'pool', 'cluster', 4))
for q in range(847, 853): SPEC.append((f'q{q}-g{q-839}-cat', '839', q, 'lane', 'cluster', 4))


REFERENCE_T = {821: 9719483, 822: 9709016, 823: 9699265, 824: 9689279, 825: 9679044, 826: 9669147, 827: 9659099, 828: 9606009, 834: 9522739, 835: 9510713, 836: 9496165, 837: 9486687, 838: 9477015, 840: 8559193, 841: 8553750, 842: 8548298, 843: 8542972, 844: 8537544, 845: 8532248, 846: 8523736, 847: 8486346, 852: 8451491}

def generate_jobs(snapshot):
    """Compare historical pool and lane jobs with a curve; keep execution blocked."""
    curve = sorted(snapshot['curve'] if isinstance(snapshot, dict) else snapshot, key=lambda r: r['Q'])
    reference = REFERENCE_T
    def at(q):
        return next((r for r in curve if r['Q'] == q), None)
    jobs = []
    for jid, tkey, q, kind, owner, rank in SPEC:
        t = TREES[tkey]
        edits = []
        if kind == 'pool':
            edits.append([t['pool_file'], f"pub const {t['pool_name']}: usize = {t['pool_now']};", f"pub const {t['pool_name']}: usize = {q - t['q0']};"])
        else:
            edits.append(['src/point_add/mod.rs', 'pub const GPOOL: usize = 0;', f'pub const GPOOL: usize = {q - t["q0"] - (18 if tkey == "818" else 15)};'])
            edits.append(GUARD)
        files = {}
        if t['catalog_saving'] and not t['has_catalog']:
            edits += CATALOG_EDITS; files = {f'src/point_add/frontier_rewrite/{k}': v for k, v in CATALOG_FILES.items()}
        # expected T (inference). Where a public row exists for the same tree and setting, the child is that row
        # minus the catalog saving (odinfree's and gopikannappan's rows carry no catalog). Gaps interpolate between
        # the neighbouring rows of the same dial. Q815/Q817/Q804/Q805 extrapolate from our own base row.
        saving = t['catalog_saving']
        known = {819: 9739151, 820: 9729122}  # odinfree's dropped rows (cac0bf90, a406c175), same tree and POOL_1
        same = known.get(q) or reference.get(q)
        if same is not None:
            expected = same - saving
        elif kind == 'lane':
            lo, hi = ((828, 834) if tkey == '818' else (847, 852))
            tlo, thi = (known.get(lo) or reference[lo]), (known.get(hi) or reference[hi])
            expected = tlo + (thi - tlo) * (q - lo) / (hi - lo) - saving
        else:
            base_T = {'818': 9728780, '839': 8564543, '803': 12578098}[tkey]
            steps = q - {'818': 818, '839': 839, '803': 803}[tkey]
            expected = base_T - t['pool_step'] * steps - (0 if t['has_catalog'] else saving)
        row = at(q); best = best_at_or_below(curve, q)
        deps = list(t['deps'])
        if q in ROW_AT and ROW_AT[q] not in deps: deps.append(ROW_AT[q])
        live = best is not None and expected < best['T']
        jobs.append({'id': jid, 'rank': rank, 'owner': owner, 'Q': q, 'tree': tkey, 'kind': kind,
                     'state': 'blocked',
                     'blocked_reasons': ['Historical pareto5 candidate: reconcile prior screen receipts before execution; do not repeat a search screen.'],
                     'metadata': {'origin': 'pareto5-handoff', 'origin_commit': 'da182c4',
                                  'purpose': 'historical-comparison', 'screen_history': 'unreconciled'},
                     'base_commit': t['base_commit'], 'base_row': t['base_row'], 'base_desc': t['base_desc'],
                     'edits': edits, 'files_from_commit_4073f09c': files,
                     'expected_T_inference': int(expected), 'curve_T_at_Q': row and row['T'], 'curve_holder_at_Q': row and row['user'],
                     'best_T_at_or_below_Q': best and best['T'], 'must_beat': best and best['T'], 'margin_inference': best and int(best['T'] - expected),
                     'live': live, 'parent_rate': t['rate'], 'pass_chance_inference': t['pass_chance'],
                     'declare': [IDS.get(d) or d for d in deps],
                     'setting_source_row': ROW_AT.get(q), 'claimed_score_formula': 'Q x T (T = the emitter\'s static expected Toffoli; or the screen\'s measured T)'})
    jobs.sort(key=lambda j: (j['rank'], j['Q']))
    return jobs

BASE = {'commit': 'f6cee264', 'row': 'b1f90e3a-?', 'desc': "gopikannappan's Q1000 row (f50ee007 line: jackylee0424's 870 Lilypad-G b2e963a + gnuchev's ten exact edits), PEAK0 = 1000, inherited nonce kept"}
TABLE = {935: 2073651.5, 940: 2065244.5, 945: 2058242.0, 950: 2050573.5, 955: 2044224.5, 959: 2039637.5, 960: 2038596.5, 962: 2036567.5, 963: 2035563.5,
         965: 2033564.0, 970: 2028640.5, 980: 2019135.5, 990: 2010083.5, 999: 2002394.5, 1000: 2001560.5, 1001: 2000730.5, 1010: 1993586.5,
         1020: 1986241.5, 1030: 1980116.0, 1040: 1975107.5, 1050: 1970565.0, 1058: 1967237.0, 1059: 1966847.0}
POSTPASS = 4400.0


def lilypad_expected(w):
    ks = sorted(TABLE)
    if w in TABLE: return TABLE[w] - POSTPASS
    lo = max(k for k in ks if k < w); hi = min(k for k in ks if k > w)
    return TABLE[lo] + (TABLE[hi] - TABLE[lo]) * (w - lo) / (hi - lo) - POSTPASS


def generate_lilypad_sweep(snapshot):
    """List width candidates that remain blocked pending a reliability probe."""
    curve = sorted(snapshot['curve'] if isinstance(snapshot, dict) else snapshot, key=lambda r: r['Q'])
    held = {r['Q'] for r in curve}
    jobs = []
    for w in range(936, 1059):
        if w in held: continue
        e = lilypad_expected(w); b = best_at_or_below(curve, w)
        if b is None:
            continue
        jobs.append({'id': f'lily-w{w}', 'Q': w, 'base_commit': BASE['commit'], 'base_desc': BASE['desc'],
                     'edits': [['src/point_add/frogtail_batch/pointadd_lilypad.rs', 'PEAK0 literal 1000 (HALF_COFACTOR_G arm)', f'PEAK0 literal {w}']],
                     'expected_T_inference': int(e), 'must_beat': b['T'], 'holder_below': f"{b['user']} Q{b['Q']}", 'margin_inference': int(b['T'] - e),
                     'live': e < b['T'], 'parent_rate_sourced': '3.1-3.7 failing shots per 9,024 (classical mismatches)', 'pass_chance_inference': 0.035,
                     'gate': 'probe first (see README section 9); audit bound not met at the sourced rate'})
    return jobs

def write_jobs(output, snapshot, *, family='froghop', source='provided snapshot'):
    """Write historical candidates for comparison, with unresolved execution blocked."""
    if family == 'froghop':
        document = {'curve': source, 'jobs': generate_jobs(snapshot)}
    elif family == 'lilypad':
        document = {'curve': source, 'base': BASE, 'jobs': generate_lilypad_sweep(snapshot)}
    else:
        raise ValueError('Unknown job generator')
    save(output, document)
    return document
