"""Optional subscription CLI proposals with explicit process receipts."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from .common import load, save, sha256


class ModelError(ValueError):
    pass


def load_config(path=None):
    config = {'provider': 'offline'} if path is None else load(path)
    fields = {'provider', 'model', 'timeout_seconds', 'max_calls', 'executable', 'effort'}
    if not isinstance(config, dict) or set(config) - fields:
        raise ModelError('Unknown model configuration field')
    if config.get('provider') not in {'offline', 'claude', 'codex'}:
        raise ModelError('Provider must be offline, claude, or codex')
    if config['provider'] != 'offline' and not isinstance(config.get('model'), str):
        raise ModelError('The CLI provider needs an explicit model identifier')
    for key, default in [('timeout_seconds', 240), ('max_calls', 32)]:
        value = config.get(key, default)
        if type(value) is not int or value < 1:
            raise ModelError(f'{key} must be a positive integer')
    if config.get('effort', 'medium') not in {'low', 'medium', 'high'}:
        raise ModelError('Effort must be low, medium, or high')
    return config


def cli_environment():
    """Keep CLI login access and exclude API-key routing."""
    prefixes = ('ANTHROPIC_', 'OPENAI_', 'OPENROUTER_', 'AWS_', 'AZURE_', 'CLAUDE_CODE_USE_')
    return {k: v for k, v in os.environ.items()
            if not k.endswith('API_KEY') and not k.startswith(prefixes)
            and k not in {'GOOGLE_APPLICATION_CREDENTIALS', 'CLAUDECODE'}}


def parse_response(provider, stdout, requested_model):
    try:
        if provider == 'claude':
            raw = json.loads(stdout)
            if not isinstance(raw, dict) or raw.get('is_error'):
                raise ModelError('The Claude CLI reported a failed call')
            ids = sorted(raw.get('modelUsage', {})) or None
            if ids and ids != [requested_model]:
                raise ModelError(f'Resolved model differs: {ids}')
            answer = raw.get('structured_output')
            if answer is None:
                answer = json.loads(raw.get('result', ''))
            meta = {'resolved_models': ids, 'usage': raw.get('usage'),
                    'provider_estimate_usd': raw.get('total_cost_usd')}
        else:
            events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            if not events or any(not isinstance(event, dict) for event in events):
                raise ModelError('The Codex CLI returned malformed event records')
            if any(e.get('type') == 'item.completed' and not isinstance(e.get('item'), dict)
                   for e in events):
                raise ModelError('The Codex CLI returned a malformed completed item')
            messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                        and e.get('item', {}).get('type') == 'agent_message']
            if not messages or any(e.get('type') in {'error', 'turn.failed'} for e in events):
                raise ModelError('The Codex CLI did not complete its recommendation')
            answer = json.loads(messages[-1])
            usage = next((e.get('usage') for e in reversed(events)
                          if e.get('type') == 'turn.completed'), None)
            meta = {'resolved_models': None, 'usage': usage, 'provider_estimate_usd': None}
        if not isinstance(answer, dict) or set(answer) != {'action_index', 'reason'}:
            raise ModelError('The recommendation must contain action_index and reason')
        if type(answer['action_index']) is not int or not isinstance(answer['reason'], str):
            raise ModelError('The recommendation has invalid field types')
        return answer, meta
    except (ValueError, TypeError, KeyError) as error:
        if isinstance(error, ModelError):
            raise
        raise ModelError('The CLI returned invalid JSON') from error


def arguments(config, schema):
    provider = config['provider']
    exe = shutil.which(config.get('executable', provider))
    if exe is None:
        raise ModelError(f'{provider} CLI is absent; install and log in before this mode')
    if provider == 'claude':
        return [exe, '-p', '--output-format', 'json', '--tools', '',
                '--disallowedTools', 'mcp__*', '--no-session-persistence',
                '--setting-sources', '', '--disable-slash-commands',
                '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--settings', '{"disableAllHooks":true}',
                '--model', config['model'], '--effort', config.get('effort', 'medium'),
                '--json-schema', json.dumps(schema)]
    return [exe, 'exec', '--sandbox', 'read-only', '--skip-git-repo-check',
            '--ephemeral', '--json', '--model', config['model'],
            '-c', 'model_reasoning_effort="' + config.get('effort', 'medium') + '"', '-']


def select(actions, config, output, *, context, call_index=0):
    """Choose one admitted action. Failed calls never become offline proposals."""
    if not actions:
        raise ModelError('The action menu is empty')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if config['provider'] == 'offline':
        receipt = {'model_called': False, 'provider': 'offline', 'action_index': 0}
        save(output / 'receipt.json', receipt)
        return actions[0], receipt
    if call_index >= config.get('max_calls', 32):
        raise ModelError('The configured model-call allowance is exhausted')
    prompt = {'task': 'Select one circuit recipe from the admitted menu. Use no tools.',
              'response': {'action_index': 'integer menu index', 'reason': 'brief mechanism-based reason'},
              'context': context, 'actions': actions}
    prior_path = output / 'receipt.json'
    if prior_path.exists():
        prior = load(prior_path)
        if not prior.get('success'):
            raise ModelError('This directory records a failed or interrupted model call. Inspect it and use a new run directory.')
        if prior.get('requested_model') != config['model'] or load(output / 'prompt.json') != prompt:
            raise ModelError('This model directory belongs to a different recommendation request')
        answer = load(output / 'answer.json')
        index = answer.get('action_index')
        if type(index) is not int or not 0 <= index < len(actions):
            raise ModelError('The saved recommendation no longer matches the admitted menu')
        return actions[index], {**prior, 'recommendation_reused': True}
    schema = {'type': 'object', 'properties': {
        'action_index': {'type': 'integer', 'minimum': 0, 'maximum': len(actions) - 1},
        'reason': {'type': 'string', 'maxLength': 800}},
        'required': ['action_index', 'reason'], 'additionalProperties': False}
    command = arguments(config, schema)
    record = {'model_called': True, 'provider': config['provider'],
              'requested_model': config['model'], 'command': command,
              'executable_sha256': sha256(command[0]), 'started_unix': time.time(),
              'success': False, 'api_keys_removed': True,
              'billing_scope': 'CLI estimate; subscription invoice unavailable'}
    save(output / 'prompt.json', prompt)
    save(output / 'receipt.json', record)
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=output, env=cli_environment(),
                                   text=True, start_new_session=True)
        stdout, stderr = process.communicate(json.dumps(prompt), timeout=config.get('timeout_seconds', 240))
        (output / 'stdout.txt').write_text(stdout)
        (output / 'stderr.txt').write_text(stderr)
        record['returncode'] = process.returncode
        if process.returncode:
            raise ModelError(f'The CLI failed; see {output / "stderr.txt"}')
        answer, meta = parse_response(config['provider'], stdout, config['model'])
        record.update(meta)
        index = answer['action_index']
        if index < 0 or index >= len(actions):
            raise ModelError('The model selected an action outside the menu')
        record.update(success=True, action_index=index, reason=answer['reason'])
        save(output / 'answer.json', answer)
        return actions[index], record
    except subprocess.TimeoutExpired as error:
        if process is not None:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
            (output / 'stdout.txt').write_text(stdout)
            (output / 'stderr.txt').write_text(stderr)
        record['error'] = 'timeout'
        raise ModelError('The CLI exceeded timeout_seconds') from error
    except (OSError, ModelError) as error:
        record['error'] = str(error)
        raise ModelError(str(error)) from error
    finally:
        record['wall_seconds'] = time.time() - record['started_unix']
        save(output / 'receipt.json', record)
