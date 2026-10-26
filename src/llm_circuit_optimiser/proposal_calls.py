"""Tool-free bounded program proposals with receipts and explicit failures."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .common import digest, load, save, sha256
from .models import arguments, cli_environment

SCHEMA = {'type': 'object', 'properties': {'proposals': {'type': 'array',
          'items': {'type': 'string', 'maxLength': 6000}, 'maxItems': 8}},
          'required': ['proposals'], 'additionalProperties': False}


def metadata(provider, stdout):
    """Keep reported usage even when the proposal or model identity is invalid."""
    try:
        if provider == 'claude':
            raw = json.loads(stdout)
            if not isinstance(raw, dict):
                return {}
            models = raw.get('modelUsage')
            return {'usage': raw.get('usage'), 'provider_estimate_usd': raw.get('total_cost_usd'),
                    'resolved_models': sorted(models) if isinstance(models, dict) and models else None}
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        return {'usage': next((e.get('usage') for e in reversed(events) if isinstance(e, dict)
                               and e.get('type') == 'turn.completed'), None)}
    except (ValueError, TypeError):
        return {}


def decode(provider, stdout, model):
    if provider == 'claude':
        raw = json.loads(stdout)
        if not isinstance(raw, dict) or raw.get('is_error'):
            raise ValueError('Provider reported a failed call')
        models = raw.get('modelUsage')
        resolved = sorted(models) if isinstance(models, dict) and models else None
        if resolved and resolved != [model]:
            raise ValueError('Resolved model differs from requested model')
        answer = raw.get('structured_output')
        if answer is None:
            answer = json.loads(raw.get('result', ''))
        meta = {'resolved_models': resolved, 'usage': raw.get('usage'),
                'provider_estimate_usd': raw.get('total_cost_usd')}
    else:
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if not events or any(not isinstance(e, dict) or not isinstance(e.get('type'), str) or
                (e.get('type', '').startswith('item.') and not isinstance(e.get('item'), dict))
                for e in events):
            raise ValueError('Malformed provider envelope')
        allowed = {'agent_message', 'reasoning'}
        if any(e.get('type') in {'error', 'turn.failed'} or
               (e.get('type', '').startswith('item.') and e.get('item', {}).get('type') not in allowed)
               for e in events):
            raise ValueError('Failed call or tool use')
        messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                    and e.get('item', {}).get('type') == 'agent_message']
        if not messages:
            raise ValueError('No model answer')
        answer = json.loads(messages[-1])
        meta = {'resolved_models': None, 'usage': next((e.get('usage') for e in reversed(events)
                if e.get('type') == 'turn.completed'), None), 'provider_estimate_usd': None}
    if (not isinstance(answer, dict) or set(answer) != {'proposals'} or
            not isinstance(answer['proposals'], list) or len(answer['proposals']) > 8 or
            any(not isinstance(p, str) or len(p) > 6000 for p in answer['proposals'])):
        raise ValueError('Answer must contain at most eight program strings')
    return answer, meta


def request(prompt, config, folder, call_index):
    if config.get('provider') not in {'claude', 'codex'}:
        raise ValueError('A live proposal needs a provider configuration')
    if call_index >= config.get('max_calls', 40):
        raise ValueError('Model-call allowance exhausted')
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    request_hash = digest({'prompt': prompt, 'config': config, 'schema': SCHEMA})
    receipt = folder / 'receipt.json'
    if receipt.exists():
        prior = load(receipt)
        if not prior.get('success'):
            raise ValueError('A failed or interrupted call consumed this slot')
        answer = load(folder / 'answer.json')
        if prior['request_hash'] != request_hash or prior['answer_hash'] != digest(answer):
            raise ValueError('Saved call differs from this request')
        return answer, {**prior, 'reused': True}
    command = arguments(config, SCHEMA)
    record = {'success': False, 'request_hash': request_hash, 'provider': config['provider'],
              'requested_model': config['model'], 'call_index': call_index,
              'started_unix': time.time(), 'executable_sha256': sha256(command[0]),
              'usage': None, 'command': command, 'api_keys_removed': True}
    save(folder / 'prompt.json', prompt)
    # Exclusive creation reserves the slot before any provider call.
    with receipt.open('x') as stream:
        json.dump(record, stream, indent=2)
    process = None
    try:
        process = subprocess.Popen(command, cwd=folder, env=cli_environment(), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        stdout, stderr = process.communicate(json.dumps(prompt), timeout=config.get('timeout_seconds', 240))
        (folder / 'stdout.txt').write_text(stdout)
        (folder / 'stderr.txt').write_text(stderr)
        record['returncode'] = process.returncode
        record.update(metadata(config['provider'], stdout))
        if process.returncode:
            raise ValueError('Provider process failed; inspect stderr.txt')
        answer, meta = decode(config['provider'], stdout, config['model'])
        record.update(meta)
        save(folder / 'answer.json', answer)
        record.update(success=True, answer_hash=digest(answer))
        return answer, record
    except subprocess.TimeoutExpired as error:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        (folder / 'stdout.txt').write_text(stdout)
        (folder / 'stderr.txt').write_text(stderr)
        record.update(metadata(config['provider'], stdout))
        record['error'] = 'timeout'
        raise ValueError('Model call timed out') from error
    except (ValueError, OSError, TypeError, KeyError) as error:
        record['error'] = str(error)
        raise ValueError(str(error)) from error
    finally:
        record['wall_seconds'] = time.time() - record['started_unix']
        save(receipt, record)
