"""Model transports. Keep provider usage separate from reference text estimates."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

SCHEMA = {'type':'object','properties':{'proposals':{'type':'array','items':{
    'type':'object','properties':{'parent_id':{'type':'string'},'changes_json':{'type':'string'},
    'hypothesis':{'type':'string'}},'required':['parent_id','changes_json','hypothesis'],
    'additionalProperties':False}}},'required':['proposals'],'additionalProperties':False}


def _codex_events(stdout):
    if isinstance(stdout,bytes):stdout=stdout.decode(errors='replace')
    events=[]
    for line in (stdout or '').splitlines():
        try:events.append(json.loads(line))
        except ValueError:continue
    return events


def codex_usage(stdout):
    turns=[e for e in _codex_events(stdout) if e.get('type')=='turn.completed']
    fields={'input_tokens':'input_tokens','output_tokens':'output_tokens',
            'cached_input_tokens':'cached_input_tokens','reasoning_tokens':'reasoning_output_tokens'}
    if not turns:return None
    usages=[e.get('usage') if isinstance(e.get('usage'),dict) else {} for e in turns]
    result={};incomplete=[]
    for name,key in fields.items():
        values=[u.get(key) for u in usages];known=[v for v in values if type(v) is int and v>=0]
        result[name]=sum(known) if known else None
        if len(known)!=len(values):incomplete.append(name)
    if result['input_tokens'] is None and result['output_tokens'] is None:return None
    result['incomplete_fields']=incomplete
    return result


def parse_codex(stdout):
    events=_codex_events(stdout)
    messages=[e['item']['text'] for e in events if e.get('type')=='item.completed'
              and e.get('item',{}).get('type')=='agent_message']
    completions=[e for e in events if e.get('type')=='turn.completed']
    if not messages or not completions:
        raise ValueError('Codex returned no completed structured answer')
    return json.loads(messages[-1]),codex_usage(stdout)


def claude_usage(stdout):
    """Retain reported usage even when the answer or CLI call fails."""
    try:
        response=json.loads(stdout);u=response.get('usage')
    except (ValueError,TypeError,AttributeError):return None
    if not isinstance(u,dict) or not u:return None
    incoming=u.get('input_tokens')
    cache_read=u.get('cache_read_input_tokens',0)
    cache_write=u.get('cache_creation_input_tokens',0)
    parts=[incoming,cache_read,cache_write]
    return {'input_tokens':sum(parts) if all(type(x) is int and x>=0 for x in parts) else None,
            'cached_input_tokens':cache_read,'cache_creation_input_tokens':cache_write,
            'output_tokens':u.get('output_tokens'),
            'reasoning_tokens':u.get('reasoning_tokens',u.get('output_tokens_details',{}).get('thinking_tokens')),
            'cache_creation_1h_input_tokens':u.get('cache_creation',{}).get('ephemeral_1h_input_tokens'),
            'cache_creation_5m_input_tokens':u.get('cache_creation',{}).get('ephemeral_5m_input_tokens'),
            'provider_cost_estimate_usd':response.get('total_cost_usd')}


def parse_claude(stdout,structured=True):
    response=json.loads(stdout)
    if response.get('is_error'):raise ValueError('Claude returned an error')
    if structured:
        if 'structured_output' not in response:raise ValueError('Claude returned no completed structured answer')
        answer=response['structured_output']
    else:answer=json.loads(response.get('result',''))
    return answer,claude_usage(stdout)


def _post(url, payload, headers, timeout):
    request=urllib.request.Request(url,data=json.dumps(payload).encode(),
                                  headers={'Content-Type':'application/json',**headers})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.load(response)


def generate(provider, model, prompt, timeout=120, max_output_tokens=2048,
             reasoning_effort=None,isolated=False,structured=True,receipt_path=None):
    """Return structured proposals and a receipt. Failed attempts retain an explicit usage gap."""
    started=time.monotonic()
    receipt={'provider':provider,'model':model,'input_bytes':len(prompt.encode()),'call_id':uuid.uuid4().hex,'started_unix':time.time(),
             'usage':None,'status':'started','error':None,'structured_output':structured,
             'reasoning_effort':reasoning_effort,'isolated_cli':isolated}
    def persist():
        if receipt_path is None:return
        path=Path(receipt_path);path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_name(path.name+'.tmp')
        temp.write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n');temp.replace(path)
    persist()
    try:
        if provider=='offline':
            answer={'proposals':[]}
            receipt['usage']={'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'reasoning_tokens':0}
        elif provider in ('codex','claude'):
            with tempfile.TemporaryDirectory(prefix='circuitopt-model-') as tmp:
                if provider=='codex':
                    schema=Path(tmp)/'schema.json'
                    schema.write_text(json.dumps(SCHEMA))
                    command=['codex','exec','--sandbox','read-only','--skip-git-repo-check',
                             '--json','--output-schema',str(schema),'-']
                    if model: command[2:2]=['--model',model]
                    if not structured:
                        i=command.index('--output-schema');del command[i:i+2]
                    if isolated:command[2:2]=['--ephemeral','--ignore-user-config',
                        '--disable','shell_tool','--disable','unified_exec','--disable','apps',
                        '--disable','plugins','--disable','memories','--disable','hooks',
                        '--enable','skip_host_skill_discovery']
                    if reasoning_effort:command[2:2]=['-c','model_reasoning_effort='+json.dumps(reasoning_effort)]
                else:
                    command=['claude','-p','--output-format','json',
                             '--tools','','--disallowedTools','mcp__*','--no-session-persistence']
                    if structured:command+=['--json-schema',json.dumps(SCHEMA)]
                    if model: command+=['--model',model]
                    if reasoning_effort:command+=['--effort',reasoning_effort]
                    if isolated:command+=['--setting-sources','','--disable-slash-commands',
                        '--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
                        '--settings','{"disableAllHooks":true}',
                        '--system-prompt','Propose circuit recipe changes from the supplied packet. Use no tools. Return the requested JSON.']
                receipt['command']=command;persist()
                proc=subprocess.run(command,input=prompt,text=True,capture_output=True,
                                    timeout=timeout,cwd=tmp,check=False)
                receipt['raw_response']=proc.stdout
                if provider=='codex':
                    # Retain reported usage even when the final reply cannot be parsed.
                    receipt['usage']=codex_usage(proc.stdout)
                else:
                    receipt['usage']=claude_usage(proc.stdout)
                    try:
                        raw=json.loads(proc.stdout)
                        receipt['resolved_models']=sorted(raw.get('modelUsage',{}))
                    except (ValueError,AttributeError,TypeError):receipt['resolved_models']=[]
                    models=receipt['resolved_models']
                    receipt['model_identity_status']='verified' if model in models else 'unreported' if not models else 'alias_resolved'
                    if models and model and model.startswith('claude-') and set(models)!={model}:
                        receipt.update(model_identity_status='mismatch',error='RequestedModelMismatch')
                        raise ValueError('Requested Claude model differs from the reported model')
                if proc.returncode:
                    receipt['stderr_tail']=proc.stderr[-1000:]
                    failure=(proc.stdout+' '+proc.stderr).lower()
                    if any(term in failure for term in ['not logged in','authentication failed','invalid api key','login required','/login','oauth token has expired']):
                        receipt['error']='AuthenticationRequired'
                    raise RuntimeError(f'{provider} exited {proc.returncode}; check local login and CLI compatibility')
                answer,receipt['usage']=parse_codex(proc.stdout) if provider=='codex' else parse_claude(proc.stdout,structured)
        elif provider=='openai':
            if not model: raise ValueError('Set --model for an API provider')
            raw=_post('https://api.openai.com/v1/responses',
                      {'model':model,'input':prompt,'max_output_tokens':max_output_tokens,
                       'text':{'format':{'type':'json_schema','name':'proposals','strict':True,'schema':SCHEMA}}},
                      {'Authorization':'Bearer '+os.environ['OPENAI_API_KEY']},timeout)
            receipt['raw_response']=raw
            u=raw.get('usage',{})
            receipt['usage']={'input_tokens':u.get('input_tokens'),'output_tokens':u.get('output_tokens'),
              'cached_input_tokens':u.get('input_tokens_details',{}).get('cached_tokens',0),
              'reasoning_tokens':u.get('output_tokens_details',{}).get('reasoning_tokens')} if u else None
            answer=json.loads(''.join(c.get('text','') for item in raw.get('output',[])
                                     for c in item.get('content',[]) if c.get('type')=='output_text'))
        elif provider=='anthropic':
            if not model: raise ValueError('Set --model for an API provider')
            raw=_post('https://api.anthropic.com/v1/messages',
                      {'model':model,'max_tokens':max_output_tokens,
                       'messages':[{'role':'user','content':prompt+'\nReturn only JSON matching: '+json.dumps(SCHEMA)}]},
                      {'x-api-key':os.environ['ANTHROPIC_API_KEY'],'anthropic-version':'2023-06-01'},timeout)
            receipt['raw_response']=raw
            receipt['usage']=claude_usage(json.dumps(raw))
            answer=json.loads(''.join(c.get('text','') for c in raw['content'] if c.get('type')=='text'))
        else:
            raise ValueError('Unknown provider')
        if not isinstance(answer,dict) or set(answer)!={'proposals'} or not isinstance(answer['proposals'],list):
            raise ValueError('Invalid model proposal envelope')
        receipt['status']='completed'
        return answer,receipt
    except subprocess.TimeoutExpired as exc:
        raw=exc.stdout or ''
        if isinstance(raw,bytes):raw=raw.decode(errors='replace')
        receipt['raw_response']=raw
        if provider=='codex':receipt['usage']=codex_usage(raw)
        elif provider=='claude':receipt['usage']=claude_usage(raw)
        receipt['error']='TimeoutExpired'
        return {'proposals':[]},receipt
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError,urllib.error.URLError) as exc:
        receipt['error']=receipt['error'] or type(exc).__name__
        if isinstance(exc,urllib.error.HTTPError) and exc.code in [401,403]:receipt['error']='AuthenticationRequired'
        return {'proposals':[]},receipt
    finally:
        receipt['wall_seconds']=time.monotonic()-started
        if receipt['status']=='started':receipt['status']='failed'
        persist()
