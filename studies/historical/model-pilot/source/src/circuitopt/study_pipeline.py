"""Execute the study stages once, with persisted phase records."""
from pathlib import Path
import time
from . import study,study_formats,study_report,study_transfer
from .cli import inventory


def run(root):
    root=Path(root);c=study.check(root)
    def phase(name,function):
        target=root/'phases'/f'{name}.json'
        if target.exists():
            old=study.load(target)
            if old['status']=='complete':return
            raise ValueError(f'Phase {name} has an unfinished execution. Inspect its saved state.')
        start=time.time();study.save(target,{'status':'running','started_unix':start},True)
        print('Start',name,flush=True)
        try:function()
        except Exception as exc:
            study.save(target,{'status':'failed','started_unix':start,'error':str(exc),'wall_seconds':time.time()-start})
            raise
        study.save(target,{'status':'complete','started_unix':start,'wall_seconds':time.time()-start})
        print('Complete',name,flush=True)
    def present():
        start=time.monotonic();cpu=time.process_time();study_report.report(root)
        study.append(root/'costs.jsonl',{'kind':'presentation','wall_seconds':time.monotonic()-start,'cpu_seconds':time.process_time()-cpu})
    if not c['calibration']:phase('2-calibration',lambda:study.calibrate(root))
    c=study.check(root)
    for bits in c['bits']:
        for seed in c['seeds']:
            for strategy in ['random','evolution']:
                if strategy not in c['strategies']:continue
                phase(f'3-{bits}-{strategy}-{seed}',lambda b=bits,s=strategy,k=seed:study.search(root,b,s,k))
    present()
    for bits in c['bits']:
        for seed in c['seeds']:
            if 'llm' not in c['strategies']:continue
            phase(f'4-{bits}-llm-{seed}',lambda b=bits,k=seed:study.search(root,b,'llm',k))
    present()
    if c['format_arms']:
        for seed in c['seeds']:
            for format_name in c['format_arms']:
                phase(f'6-format-{format_name}-{seed}',lambda f=format_name,k=seed:study.search(root,5,'llm',k,f))
        phase('6-schema',lambda:study_formats.compare_schemas(root))
    present()
    phase('5-freeze',lambda:study.freeze(root))
    phase('5-audit',lambda:study.audit(root))
    present()
    phase('5-transfer-256',lambda:study_transfer.run(root))
    present();inventory(root)
    print('Saved',root/'REPORT.md',flush=True)
