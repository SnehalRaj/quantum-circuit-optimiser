"""Use the authenticated Claude CLI and keep its complete process receipt."""
import json
import os
from pathlib import Path
import subprocess
import time

from . import full_native as native, providers


SYSTEM='Choose one native circuit action from the supplied menu. Use no tools. Return only the requested JSON.'


class ModelContractError(RuntimeError):
    """Stop the study when the requested model or CLI identity differs."""


def cli_environment():
    return {k:v for k,v in os.environ.items() if not k.endswith('API_KEY')
            and not k.startswith(('ANTHROPIC_','OPENAI_','OPENROUTER_','AWS_','AZURE_','CLAUDE_CODE_USE_'))
            and k!='GOOGLE_APPLICATION_CREDENTIALS'}


def cli_arguments(model):
    return ['claude','-p','--output-format','json','--tools','','--disallowedTools','mcp__*',
            '--no-session-persistence','--setting-sources','','--disable-slash-commands',
            '--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
            '--settings','{"disableAllHooks":true}','--system-prompt',SYSTEM,
            '--model',model,'--effort','medium']


def inspect_response(stdout,returncode):
    try:raw=json.loads(stdout)
    except (ValueError,TypeError):raw={}
    if not isinstance(raw,dict):raw={}
    models=raw.get('modelUsage',{})
    return {'usage':providers.claude_usage(stdout),'provider_cost_estimate_usd':raw.get('total_cost_usd'),
            'resolved_models':sorted(models) if isinstance(models,dict) else [], 'num_turns':raw.get('num_turns'),
            'success':bool(raw) and returncode==0 and not raw.get('is_error',False)}


def propose(prompt,output,model='claude-opus-5-5',*,executable=None,executable_sha256=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/'prompt.json').write_text(json.dumps(prompt,indent=2)+'\n')
    args=cli_arguments(model)
    if executable is not None:
        try:native.assert_hash(executable,executable_sha256)
        except (ValueError,OSError) as error:raise ModelContractError('The frozen Claude executable changed') from error
        args[0]=str(executable)
    schema={'type':'object','properties':{'action_index':{'type':'integer','minimum':0,
            'maximum':len(prompt['actions'])-1},'reason':{'type':'string','maxLength':400}},
            'required':['action_index','reason'],'additionalProperties':False}
    args+=['--json-schema',json.dumps(schema)]
    receipt={'command':args,'provider':'claude-cli','requested_model':model,'started_unix':time.time(),
             'api_keys_removed':True,'billing_scope':'CLI provider estimate; subscription invoice unavailable'}
    native.save(output/'receipt.json',receipt)
    try:
        proc=subprocess.run(args,input=json.dumps(prompt),capture_output=True,text=True,
                            cwd=output,env=cli_environment())
        (output/'stdout.json').write_text(proc.stdout);(output/'stderr.txt').write_text(proc.stderr)
        receipt.update(returncode=proc.returncode,**inspect_response(proc.stdout,proc.returncode))
        failure=(proc.stdout+' '+proc.stderr).lower()
        if not receipt['success']:
            if any(t in failure for t in ('not logged in','authentication','oauth token','usage limit','quota','rate limit','limit reached','hit your limit','out of extra usage')):
                raise PermissionError('Claude authentication or usage allowance requires attention')
            if any(t in failure for t in ('invalid model','unknown model','model not found','model does not exist','access to model')):
                raise ModelContractError('The requested Claude model is unavailable')
            raise ValueError('Claude invocation failed; retain its process receipt')
        raw=json.loads(proc.stdout)
        if not isinstance(raw,dict):raise ValueError('Claude returned a non-object reply')
        if receipt['resolved_models']!=[model]:raise ModelContractError('Claude did not report the requested model identity')
        answer=raw.get('structured_output')
        if answer is None:answer=json.loads(raw.get('result',''))
        native.save(output/'answer.json',answer)
        return answer,receipt
    except subprocess.TimeoutExpired as error:
        for name in ('stdout','stderr'):
            value=getattr(error,name,None) or ''
            if isinstance(value,bytes):value=value.decode(errors='replace')
            (output/(name+'.txt')).write_text(value)
        receipt.update(returncode=None,success=False,error='TimeoutExpired')
        raise ValueError('Claude exceeded its per-call timeout') from error
    except (ValueError,PermissionError,ModelContractError) as error:
        receipt.update(error=type(error).__name__,success=False)
        raise
    finally:
        receipt['wall_seconds']=time.time()-receipt['started_unix']
        native.save(output/'receipt.json',receipt)
