"""Native point-addition search with frozen binaries and a fresh final audit."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import time

from scipy.stats import beta

from . import engine
from .research_policy import DEFAULT_EPSILON


ALLOWED = {
    'PP_WALK_MAX_QUBITS', 'PP_REPLAY_FOLD_WINDOW', 'PP_REPLAY_FOLD_WINDOW_MUL',
    'PP_REPLAY_CHUNK_COMPARE', 'PP_REPLAY_FLAG_COMPARE', 'PP_ROUNDS_MUL', 'PP_R2',
    'FOLD_GUARD', 'ERASE_COMPARE', 'PP_FOLD_WIDEN', 'PP_FOLD_PROFILE',
    'PP_CHUNK_SHAPE', 'PP_FLAG_SHAPE', 'PP_WIDTH_SCHEDULE', 'fold_layout',
}


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def clean_env(settings=None):
    env = {k:os.environ[k] for k in ['PATH', 'TMPDIR', 'SYSTEMROOT'] if k in os.environ}
    env.update(settings or {})
    return env


def validate_settings(settings, grammar):
    if set(grammar) - ALLOWED or set(settings) - set(grammar):
        raise ValueError('The native grammar contains an unsupported field')
    for key,value in settings.items():
        if str(value) not in {str(v) for v in grammar[key]}:
            raise ValueError('The native grammar rejects ' + key)


def mutate(parent, rng, grammar):
    """Apply one edit to the complete parent recipe and retain its history."""
    validate_settings(parent['settings'], grammar)
    keys = [k for k,vs in grammar.items()
            if any(str(v) != parent['settings'].get(k) for v in vs)]
    if not keys:
        raise ValueError('The native grammar has no different value')
    key = rng.choice(keys)
    values = [str(v) for v in grammar[key] if str(v) != parent['settings'].get(key)]
    value = rng.choice(values)
    settings = {**parent['settings'], key:value}
    history = copy.deepcopy(parent['history'])
    history.append({'parent':parent['id'], 'field':key,
                    'before':parent['settings'].get(key), 'after':value})
    return {'settings':settings, 'history':history}


def check_receipt(row, label, pairs, streams, ops_sha3, finalists=1):
    expected = {'label':label, 'mode':'random', 'pairs':pairs, 'streams':streams,
                'shots':pairs*streams, 'ops_sha3_256':ops_sha3}
    if any(row.get(k) != v for k,v in expected.items()):
        raise ValueError('The native checker receipt does not match the requested test')
    for key,limit in [('failed_inputs',pairs), ('failed_shots',pairs*streams),
                      ('coord_failures',pairs*streams), ('phase_failures',pairs*streams),
                      ('ancilla_failures',pairs*streams)]:
        if type(row.get(key)) is not int or not 0 <= row[key] <= limit:
            raise ValueError('Invalid native failure count: ' + key)
    channels = [row[k] for k in ['coord_failures','phase_failures','ancilla_failures']]
    if not row['failed_inputs'] <= row['failed_shots'] <= streams*row['failed_inputs']:
        raise ValueError('The native input and shot failure counts disagree')
    if max(channels) > row['failed_shots'] or sum(channels) < row['failed_shots']:
        raise ValueError('The native failure channels disagree')
    mean = row.get('mean_toffoli')
    if not isinstance(mean,(int,float)) or not math.isfinite(mean) or mean <= 0:
        raise ValueError('Invalid native Toffoli count')
    if type(row.get('qubits')) is not int or row['qubits'] < 512:
        raise ValueError('Invalid native qubit count')
    failed = row['failed_inputs']
    upper = 1.0 if failed == pairs else float(beta.ppf(1-.05/finalists, failed+1, pairs-failed))
    return {'Q':row['qubits'], 'T':math.floor(mean+.5), 'mean_T':mean,
            'S':row['qubits']*math.floor(mean+.5), 'failed_inputs':failed,
            'failed_shots':row['failed_shots'], 'upper':upper, 'alpha':.05/finalists,
            'pairs':pairs, 'streams':streams,
            'channels':{k:row[k] for k in ['coord_failures','phase_failures','ancilla_failures']}}


def select_survivor(rows, epsilon):
    eligible = [r for r in rows if r.get('audit',{}).get('upper',1) <= epsilon
                and r.get('official',{}).get('passed') is True]
    return min(eligible,key=lambda r:(r['official']['S'],r['id'])) if eligible else None


def _call(command, cwd, log, deadline, env=None, accepted_codes=(0,)):
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise TimeoutError('The native campaign reached its time cap')
    start = time.monotonic()
    result = subprocess.run([str(x) for x in command],cwd=cwd,
                            env=clean_env(env),capture_output=True,text=True,
                            timeout=remaining)
    Path(log).write_text(result.stdout+'\n'+result.stderr)
    if result.returncode not in accepted_codes:
        raise ValueError(f'Native process failed ({result.returncode}); see {log}')
    return result, time.monotonic()-start


def _make_input_exporter(root, workspace):
    """Compile a data exporter from the checker's unchanged input functions."""
    native = root/'native'
    original = (workspace/'tools/indep-check/src/main.rs').read_text()
    prefix = original.split('fn main() {',1)[0]
    suffix = r'''
fn main() {
    use std::io::Write;
    let args: Vec<String> = std::env::args().collect();
    let label = &args[1];
    let n: usize = args[2].parse().unwrap();
    let threads: usize = args[3].parse().unwrap();
    let pairs = make_pairs(&secp256k1(), label, "random", n, threads);
    let mut out = std::io::BufWriter::new(std::fs::File::create(&args[4]).unwrap());
    for pair in pairs {
        for word in [pair.t.0, pair.t.1, pair.o.0, pair.o.1, pair.e.0, pair.e.1] {
            out.write_all(&word.to_le_bytes::<32>()).unwrap();
        }
    }
    out.flush().unwrap();
}
'''
    source = native/'input-exporter.rs'; source.write_text(prefix+suffix)
    deps = workspace/'tools/indep-check/target-v2/release/deps'
    rustc = next((workspace/'.tools/rustup/toolchains').glob('1.93.0-*/bin/rustc'))
    command = [str(rustc),'--edition=2021','-O','-Awarnings','-C','lto=thin',
               '-C','codegen-units=1',str(source),
               '-L','dependency='+str(deps),'-C','linker=/usr/bin/cc',
               '-o',str(native/'input-exporter.bin')]
    for name in ['alloy_primitives','quantum_ecc','sha3','zstd']:
        library = sorted(deps.glob('lib'+name+'-*.rlib'))[0]
        command.extend(['--extern',name+'='+str(library)])
    result = subprocess.run(command,capture_output=True,text=True,env=clean_env(),timeout=120)
    (native/'input-exporter-build.log').write_text(result.stdout+result.stderr)
    if result.returncode:
        raise ValueError('The exact native input exporter did not compile')


def export_inputs(root, label, pairs, config, deadline):
    target = root/'inputs'/label
    target.mkdir(parents=True,exist_ok=True)
    if not (target/'pairs.bin').exists():
        _call([root/'native/input-exporter.bin',label,str(pairs),str(config['threads']),target/'pairs.bin'],
              root,target/'export.log',deadline)
    if (target/'pairs.bin').stat().st_size != pairs*192:
        raise ValueError('The saved native input count changed')
    protocol = b'gu-track-indep-check-v1'
    purpose = b'measurements'
    seed_prefix = protocol+len(purpose).to_bytes(8,'little')+purpose
    with (target/'measurement-seeds.jsonl').open('w') as f:
        for stream in range(2):
            for batch in range((pairs+63)//64):
                parts = [label.encode(),stream.to_bytes(8,'little'),batch.to_bytes(8,'little')]
                seed = seed_prefix+b''.join(len(p).to_bytes(8,'little')+p for p in parts)
                f.write(json.dumps({'stream':stream,'batch':batch,'shake256_input_hex':seed.hex()})+'\n')
    engine.save(target/'manifest.json',{'pairs':pairs,'streams':2,'label':label,
                'format':'Six little-endian U256 words per pair: target x,y; addend x,y; expected x,y.',
                'pairs_sha256':digest(target/'pairs.bin'),
                'measurement_seeds_sha256':digest(target/'measurement-seeds.jsonl')})
    return str(target.relative_to(root))


def measure(root, candidate, stage, label, pairs, config, deadline, finalists=1):
    started = time.monotonic()
    directory = root/'trials'/candidate['id']
    ops = directory/'ops.bin'
    sha3 = digest(ops,'sha3_256')
    if sha3 != candidate['ops_sha3']:
        raise ValueError('The saved native circuit changed')
    _verify_binary(root,config,'checker.bin')
    _verify_binary(root,config,'input-exporter.bin')
    inputs = export_inputs(root,label,pairs,config,deadline)
    receipt = directory/(stage+'.json')
    if receipt.exists():
        raise ValueError('Each native check needs an unused receipt path')
    command = [root/'native/checker.bin','--ops',ops,'--mode','random',
               '--label',label,'--pairs',str(pairs),'--streams','2',
               '--threads',str(config['threads']),'--out',receipt]
    # Exit 1 denotes a rejected circuit with a complete failure receipt.
    process, checker_seconds = _call(command, root,directory/(stage+'.log'),deadline,accepted_codes=(0,1))
    row = engine.load(receipt)
    result = check_receipt(row,label,pairs,2,sha3,finalists)
    if (process.returncode == 1) != (result['failed_inputs'] > 0):
        raise ValueError('The native exit status and failure receipt disagree')
    result.update(seconds=time.monotonic()-started, checker_seconds=checker_seconds,
                  label=label, inputs=inputs, receipt=str(receipt.relative_to(root)))
    return result


def emit(root, candidate, config, deadline):
    directory = root/'trials'/candidate['id']
    directory.mkdir(parents=True)
    validate_settings(candidate['settings'],config['grammar'])
    settings = {k:v for k,v in candidate['settings'].items() if k != 'fold_layout'}
    binary = root/'native'/(candidate['settings']['fold_layout']+'.bin')
    _verify_binary(root,config,binary.name)
    _, seconds = _call([binary],directory,directory/'emit.log',deadline,settings)
    ops = directory/'ops.bin'
    if not ops.is_file():
        raise ValueError('The native emitter produced no circuit')
    candidate.update(ops_sha3=digest(ops,'sha3_256'),emit_seconds=seconds)
    engine.save(directory/'candidate.json',candidate,True)


def _pins(text):
    values = dict(re.findall(r'set_default_env\(\s*"([A-Z0-9_]+)"\s*,\s*"([^"]*)"',text))
    for name,body in re.findall(r'set_default_env\(\s*"([A-Z0-9_]+)"\s*,\s*concat!\((.*?)\)\s*,?\s*\)',text,re.S):
        values[name] = ''.join(re.findall(r'"([^"]*)"',body))
    return values



def clean_source_pins(text):
    """Reject an inherited nonce before native setup or evaluation."""
    pins = _pins(text)
    if 'TAIL_NONCE' in pins:
        raise ValueError('The admitted native source contains an identity nonce tail. Use the clean generator backend.')
    return pins


def initialise(root, workspace, *, trials=256, max_depth=20, wall_seconds=7200,
               finalists=3, audit_pairs=1048576, seed=20261023):
    """Freeze a portable native campaign from the admitted S3 implementation."""
    root,workspace = Path(root).resolve(),Path(workspace).resolve()
    if root.exists():
        raise ValueError('Use a fresh native campaign directory')
    if trials < 1 or max_depth < 1 or not 1 <= finalists <= 3 or audit_pairs < 1 or wall_seconds <= 0:
        raise ValueError('Native campaign limits must be positive; use one to three finalists')
    records = workspace/'qopt/runs'
    admission_path = workspace/'experiments/2026-10-21-grammar-search/baseline-lock.json'
    admission = engine.load(admission_path)
    if digest(workspace/admission['checker']['binary_path']) != admission['checker']['binary_sha256']:
        raise ValueError('The admitted native checker binary changed')
    for name,sha in admission['checker']['source_sha256'].items():
        if digest(workspace/name) != sha:
            raise ValueError('The admitted native checker source changed: '+name)
    base = engine.load(records/'S3/build.json')
    split = engine.load(records/'PC001/build.json')
    if base['parent_commit'] != 'b92c0302' or split['parent_commit'] != 'b92c0302':
        raise ValueError('The admitted native parent changed')
    repo = workspace/'vendor/gu-candidate4'
    text = subprocess.run(['git','show','b92c0302:src/point_add/mod.rs'],
                          cwd=repo,capture_output=True,text=True,check=True).stdout
    pins = clean_source_pins(text)
    grammar = {
        'fold_layout':['S3','PC001'],
        'PP_WALK_MAX_QUBITS':list(range(1290,1341,2)),
        'PP_REPLAY_FOLD_WINDOW':list(range(52,69)),
        'PP_REPLAY_FOLD_WINDOW_MUL':list(range(52,69)),
        'PP_REPLAY_CHUNK_COMPARE':list(range(26,39)),
        'PP_REPLAY_FLAG_COMPARE':list(range(28,41)),
        'PP_ROUNDS_MUL':list(range(728,741)), 'PP_R2':list(range(700,740)),
        'FOLD_GUARD':list(range(26,37)), 'ERASE_COMPARE':list(range(24,35)),
        'PP_FOLD_WIDEN':[0,16,32,64,128],
        'PP_FOLD_PROFILE':['0:0','64:0,32:-1,0:-2','64:0,32:1,0:2'],
        'PP_CHUNK_SHAPE':['0:0','64:0,32:-1,0:-2','64:0,32:1,0:2'],
        'PP_FLAG_SHAPE':['0:0','64:0,32:-1,0:-2','64:0,32:1,0:2'],
    }
    original = {k:str(pins[k]) for k in grammar if k != 'fold_layout'}
    original['fold_layout'] = 'S3'
    validate_settings(original,grammar)
    root.mkdir(parents=True)
    native = root/'native'; native.mkdir()
    shutil.copy2(admission_path,native/'baseline-admission.json')
    shutil.copy2(records/'S3/build.json',native/'S3-build.json')
    shutil.copy2(records/'PC001/build.json',native/'PC001-build.json')
    shutil.copy2(workspace/'autoresearch/official/S3/score.json',native/'S3-official-score.json')
    for source,name in [(records/'S3/build_circuit','S3.bin'),
                        (records/'PC001/build_circuit','PC001.bin'),
                        (workspace/'tools/indep-check/target-v2/release/indep-check','checker.bin'),
                        (workspace/'vendor/gu-candidate4/target/release/eval_circuit','official.bin')]:
        shutil.copy2(source,native/name)
    shutil.copy2(records/'PC001/patch.diff',native/'PC001.patch')
    shutil.copytree(workspace/'tools/indep-check/src',native/'checker-source')
    shutil.copytree(workspace/'tools/indep-check/harness',native/'checker-harness',
                    ignore=shutil.ignore_patterns('target','.git'))
    shutil.copy2(workspace/'tools/indep-check/Cargo.toml',native/'checker-Cargo.toml')
    shutil.copy2(workspace/'tools/indep-check/Cargo.lock',native/'checker-Cargo.lock')
    _make_input_exporter(root,workspace)
    with (native/'S3-source.tar').open('wb') as f:
        subprocess.run(['git','archive','b92c0302'],cwd=repo,stdout=f,check=True)
    source = root/'source'; source.mkdir()
    package = Path(__file__).parent
    shutil.copytree(package,source/'src/circuitopt',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ['pyproject.toml','requirements.lock']:
        shutil.copy2(package.parent.parent/name,source/name)
    protected = {str(p.relative_to(root)):digest(p)
                 for d in [native,source] for p in sorted(d.rglob('*')) if p.is_file()}
    engine.save(root/'source-manifest.json',protected,True)
    config = {'schema':1,'interface':'complete_secp256k1_point_addition',
              'metric':'Q * round_half_up(mean_executed_Toffoli)',
              'selection':'Lowest individual audited cost among finalists that also pass the official evaluator',
              'provider':'offline','max_model_calls':0,'parent_commit':'b92c0302',
              'parent_ops_sha3':base['ops_sha3'],'structural_ops_sha3':split['ops_sha3'],
              'trials':trials,'max_depth':max_depth,'wall_seconds':wall_seconds,
              'finalists':finalists,'audit_pairs':audit_pairs,'epsilon':DEFAULT_EPSILON,'threads':4,
              'smoke_pairs':1024,'development_pairs':16384,'seed':seed,
              'search_fraction':.45,'grammar':grammar,'baseline_settings':original,
              'native_grammar_scope':'Native fold layout and approximation settings. Arbitrary Python payload programs are not native instructions.',
              'nonce':pins['TAIL_NONCE'],
              'admitted_official_score':engine.load(native/'S3-official-score.json')['score'],
              'input_protocol':'Frozen gu-track-indep-check-v1 source; labels and two measurement streams per pair.',
              'protected':protected}
    engine.save(root/'experiment.json',config,True)
    (root/'experiment.sha256').write_text(engine.digest(config)+'\n')
    return config


def _verify(root, config):
    if engine.digest(config) != (root/'experiment.sha256').read_text().strip():
        raise ValueError('The native experiment configuration changed')
    for name,sha in config['protected'].items():
        if digest(root/name) != sha:
            raise ValueError('The protected native source changed: '+name)


def _verify_binary(root,config,name):
    key = 'native/'+name
    if digest(root/key) != config['protected'][key]:
        raise ValueError('The protected native binary changed: '+name)


def official_score(text):
    total = re.search(r'total Toffoli \(sum\)\s*:\s*(\d+) over (\d+) shots',text)
    qubits = re.search(r'^\s*qubits\s*:\s*(\d+)',text,re.M)
    if total is None or qubits is None or int(total.group(2)) != 9024:
        raise ValueError('The trusted evaluator returned no complete score')
    return ((2*int(total.group(1))+9024)//(2*9024))*int(qubits.group(1))


def official(root, row, config, deadline):
    """Run the unchanged trusted evaluator and confine its output writes."""
    directory = root/'trials'/row['id']
    if digest(directory/'ops.bin','sha3_256') != row['ops_sha3']:
        raise ValueError('The official circuit differs from the frozen circuit')
    _verify_binary(root,config,'official.bin')
    if sys.platform != 'darwin':
        raise ValueError('Official output confinement currently requires macOS sandbox-exec')
    profile = '(version 1)(allow default)(deny file-write*)(allow file-write* (subpath "'+str(directory)+'"))(allow file-write* (subpath "/dev"))(deny network*)'
    command = ['sandbox-exec','-p',profile,root/'native/official.bin']
    try:
        process,seconds = _call(command,directory,directory/'official.log',deadline)
    except ValueError:
        return {'passed':False,'receipt':str((directory/'official.log').relative_to(root))}
    text = process.stdout
    passed = 'all 9024 shots OK' in text and '=== experiment OK ===' in text
    if digest(directory/'ops.bin','sha3_256') != row['ops_sha3']:
        raise ValueError('The official circuit changed during evaluation')
    return {'passed':passed,'seconds':seconds,'shots':9024,
            'S':official_score(text) if passed else None,
            'receipt':str((directory/'official.log').relative_to(root))}


def run(root):
    root = Path(root).resolve()
    config = engine.load(root/'experiment.json'); _verify(root,config)
    if (root/'started.json').exists():
        raise ValueError('This native campaign already started; preserve its frozen audit labels')
    start = time.monotonic(); deadline = start+config['wall_seconds']
    search_deadline = start+config['wall_seconds']*config['search_fraction']
    campaign_id = root.name
    engine.save(root/'started.json',{'unix':time.time(),'wall_seconds':config['wall_seconds']},True)
    rng = random.Random(config['seed']); rows = []; seen = set(); seen_ops = set()
    baseline = {'id':'baseline','settings':config['baseline_settings'],'history':[]}

    def persist():
        record = {'status':'searching','elapsed_seconds':time.monotonic()-start,
                    'completed':len(rows),'max_depth_observed':max([len(r['history']) for r in rows] or [0]),
                    'best_development_S':min([r['development']['S'] for r in rows if r.get('development',{}).get('failed_inputs') == 0] or [0]),
                    'trials':rows}
        engine.save(root/'search.json',record)
        engine.save(root/'progress.json',record)

    def evaluate(row, limit):
        try:
            emit(root,row,config,limit)
            if row['ops_sha3'] in seen_ops:
                row['status']='duplicate_circuit'; return
            seen_ops.add(row['ops_sha3'])
            row['smoke'] = measure(root,row,'smoke',campaign_id+'-smoke',config['smoke_pairs'],config,limit)
            row['status'] = 'smoke_failed' if row['smoke']['failed_inputs'] else 'smoke_passed'
            if not row['smoke']['failed_inputs'] and row['id']=='baseline':
                row['development'] = measure(root,row,'development',campaign_id+'-development',config['development_pairs'],config,limit)
                row['status'] = 'development_failed' if row['development']['failed_inputs'] else 'development_passed'
        except (ValueError,subprocess.TimeoutExpired,TimeoutError) as error:
            row.update(status='error',error=str(error))
        finally:
            engine.save(root/'trials'/row['id']/'result.json',row)

    evaluate(baseline,deadline); rows.append(baseline); seen.add(engine.digest(baseline['settings']));persist()
    if baseline['status'] != 'development_passed' or baseline['ops_sha3'] != config['parent_ops_sha3']:
        raise ValueError('The full native baseline did not reproduce and pass development')
    baseline['official'] = official(root,baseline,config,deadline)
    if not baseline['official']['passed'] or baseline['official']['S'] != config['admitted_official_score']:
        raise ValueError('The admitted native official score did not reproduce')
    print('Native baseline S='+str(baseline['development']['S']),flush=True)
    small,big = baseline['smoke'],baseline['development']
    slope = max(0,(big['seconds']-small['seconds'])/(config['development_pairs']-config['smoke_pairs']))
    audit_estimate = (small['seconds']+slope*config['audit_pairs'])*1.25+120
    planned = max(1,min(config['finalists'],int(config['wall_seconds']*.7/audit_estimate)))
    reserve = planned*audit_estimate+min(12,config['trials'])*(big['seconds']+5)
    search_deadline = min(search_deadline,deadline-reserve)
    engine.save(root/'allocation.json',{'audit_seconds_estimate':audit_estimate,
                'planned_finalists':planned,'reserved_seconds':reserve,
                'search_seconds':max(0,search_deadline-start)},True)
    for trial in range(config['trials']):
        if time.monotonic() >= search_deadline:
            break
        # The first chain buys lower width through the existing exact fold split.
        if trial < 11:
            parent = rows[-1] if trial else baseline
            key,value = ('fold_layout','PC001') if trial == 0 else ('PP_WALK_MAX_QUBITS',str(1332-2*trial))
            change = {'settings':{**parent['settings'],key:value},
                      'history':copy.deepcopy(parent['history'])+[{'parent':parent['id'],'field':key,'before':parent['settings'].get(key),'after':value}]}
        else:
            viable = [r for r in rows if r.get('smoke',{}).get('failed_inputs') == 0
                      and len(r['history']) < config['max_depth']]
            ranked = sorted(viable,key=lambda r:r['smoke']['S'])[:24]
            # Deep and low-cost paths both receive proposals. Intermediate regressions remain available.
            parent = rng.choice(ranked) if rng.random() < .7 else max(viable,key=lambda r:len(r['history']))
            change = mutate(parent,rng,config['grammar'])
        if len(change['history']) > config['max_depth']:
            continue
        rid = 't'+str(trial+1).zfill(4)
        identity = engine.digest(change['settings'])
        if identity in seen:
            rows.append({'id':rid,**change,'status':'duplicate_recipe'});persist();continue
        seen.add(identity)
        row = {'id':rid,**change}
        evaluate(row,search_deadline);rows.append(row);persist()
        if trial == 0 and row.get('ops_sha3') != config['structural_ops_sha3']:
            raise ValueError('The admitted native fold split did not reproduce')
        print(f"{rid} depth={len(row['history'])} {row['status']} S={row.get('development',row.get('smoke',{})).get('S')}",flush=True)

    candidates = []
    screened = sorted([r for r in rows[1:] if r.get('smoke',{}).get('failed_inputs') == 0],
                      key=lambda r:(r['smoke']['S'],r['id']))[:12]
    for row in screened:
        try:
            row['development'] = measure(root,row,'development',campaign_id+'-development',
                                         config['development_pairs'],config,deadline)
            if not row['development']['failed_inputs']:
                candidates.append(row)
            else:
                row['status'] = 'development_failed'
        except (ValueError,subprocess.TimeoutExpired,TimeoutError) as error:
            row['development_error'] = str(error)
        engine.save(root/'trials'/row['id']/'result.json',row)
    candidates = sorted(candidates,key=lambda r:(r['development']['S'],r['id']))[:planned]
    engine.save(root/'finalists.json',{'frozen_unix':time.time(),'m':len(candidates),'candidates':candidates,
                'rule':'Each frozen finalist receives one fresh audit and one official run. Lowest cost passing both wins.'},True)
    for row in candidates:
        label = campaign_id+'-audit-'+row['id']
        engine.save(root/'trials'/row['id']/'audit-prereg.json',
                    {'label':label,'ops_sha3':row['ops_sha3'],'pairs':config['audit_pairs'],
                     'streams':2,'m':len(candidates),'alpha':.05/len(candidates),'epsilon':config['epsilon']},True)
        print('Audit '+row['id'],flush=True)
        try:
            row['audit'] = measure(root,row,'audit',label,config['audit_pairs'],config,deadline,len(candidates))
            row['official'] = official(root,row,config,deadline)
        except (TimeoutError,subprocess.TimeoutExpired,ValueError) as error:
            row['final_error'] = str(error)
        engine.save(root/'trials'/row['id']/'final.json',row)
        print('Final '+row['id']+' '+json.dumps({'audit':row.get('audit'),'official':row.get('official')}),flush=True)
    _verify(root,config)
    winner = select_survivor(candidates,config['epsilon'])
    result = {'status':'qualified' if winner else 'no_qualified_candidate','trials_completed':len(rows)-1,
              'unique_circuits':len(seen_ops),'max_depth_observed':max(len(r['history']) for r in rows),
              'baseline':baseline,'finalists':candidates,'winner':winner,
              'wall_seconds':time.monotonic()-start,'model_calls':0,
              'score':winner['official']['S'] if winner else None,
              'scope':'Complete secp256k1 generic-domain point addition. Native grammar only; no public submission.'}
    engine.save(root/'result.json',result,True)
    engine.save(root/'progress.json',result)
    from .cli import inventory
    inventory(root)
    return result


def register(sub):
    parser = sub.add_parser('point-add',help='Create and run a complete native point-addition campaign without a model')
    parser.add_argument('directory',type=Path)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--trials',type=int,default=256)
    parser.add_argument('--max-depth',type=int,default=20)
    parser.add_argument('--wall-seconds',type=float,default=7200)
    parser.add_argument('--finalists',type=int,default=3)
    parser.add_argument('--audit-pairs',type=int,default=1048576)
    parser.add_argument('--seed',type=int,default=20261023)


def command(args):
    initialise(args.directory,args.workspace,trials=args.trials,max_depth=args.max_depth,
               wall_seconds=args.wall_seconds,finalists=args.finalists,
               audit_pairs=args.audit_pairs,seed=args.seed)
    result = run(args.directory)
    print(json.dumps({k:result[k] for k in ['status','score','trials_completed','max_depth_observed','wall_seconds','model_calls']},indent=2))
