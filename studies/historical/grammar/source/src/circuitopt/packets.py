"""Round-trip context formats and honest reference-token profiles."""
import copy
import json
from .engine import canonical,packet_profile

FORMATS=['text','compact_json','columnar_json']


def encode(value,format_name):
    if format_name=='text':
        return '\n'.join(json.dumps(k)+' = '+canonical(v) for k,v in value.items())
    if format_name=='compact_json':return json.dumps(value,separators=(',',':'))
    if format_name!='columnar_json':raise ValueError('Unknown packet format')
    out=copy.deepcopy(value)
    converted=[]
    for key in ['parents','rules','diagnostics']:
        rows=out.get(key)
        if isinstance(rows,list) and rows and all(isinstance(r,dict) and set(r)==set(rows[0]) for r in rows):
            cols=sorted(rows[0]);out[key]={'columns':cols,'rows':[[r[c] for c in cols] for r in rows]}
            converted.append(key)
    return json.dumps({'tables':converted,'packet':out},separators=(',',':'))


def decode(text,format_name):
    if format_name=='text':
        out={};decoder=json.JSONDecoder()
        for line in text.splitlines():
            key,end=decoder.raw_decode(line)
            if line[end:end+3]!=' = ':raise ValueError('Invalid text field')
            out[key]=json.loads(line[end+3:])
        return out
    out=json.loads(text)
    if format_name=='columnar_json':
        tables=out['tables'];out=out['packet']
        for key in tables:
            table=out[key];out[key]=[dict(zip(table['columns'],r)) for r in table['rows']]
    elif format_name!='compact_json':raise ValueError('Unknown packet format')
    return out


def profile(value):
    result={}
    try:
        import tiktoken
        import os
        from pathlib import Path
        previous=os.environ.get('TIKTOKEN_CACHE_DIR');os.environ['TIKTOKEN_CACHE_DIR']=str(Path(__file__).parent/'data/tokenizer')
        try:enc=tiktoken.get_encoding('o200k_base')
        finally:
            if previous is None:os.environ.pop('TIKTOKEN_CACHE_DIR',None)
            else:os.environ['TIKTOKEN_CACHE_DIR']=previous
    except ImportError:enc=None
    for name in FORMATS:
        text=encode(value,name)
        if decode(text,name)!=value:raise ValueError('Packet failed its exact round trip')
        result[name]={'bytes':len(text.encode()),'reference_tokens':len(enc.encode(text)) if enc else None}
    return {'formats':result,'method':'o200k_base' if enc else 'bytes_only_no_tokenizer',
            'equivalent_information':True,'provider_usage':None}
