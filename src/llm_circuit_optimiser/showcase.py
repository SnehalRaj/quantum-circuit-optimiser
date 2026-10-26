"""Matched proposal interfaces and controls for compiler-rule experiments."""
import itertools
import random
import time
from .rewrite_rules import alphabet, certify, cost, grammar_form, parse_program


def assess(task, text, form):
    result = {'program': text, 'form': form, 'accepted': False}
    try:
        gates = parse_program(text, form, task['wires'])
        result.update(gates=gates, **certify(task['before'], gates, task['wires']))
    except ValueError as error:
        result['error'] = str(error)
    return result


def task_prompt(task, form, history):
    syntax = ('Use a string of semicolon-separated gates: CX 0 1; X 2. The empty string is an empty program.'
              if form == 'grammar' else
              'Use Python source: def rewrite(q): return [CX(q[0], q[1]), X(q[2])]. '
              'Only this function and a literal list of gate constructors are accepted. Return [] for an empty program.')
    return {
        'instruction': 'Propose up to eight distinct cheaper gate programs. Use no tools. '
            'Every program must have the same action and phase on every input. '
            'Minimize CCX plus CCZ count, then gate count. Return JSON with only proposals, a list of program strings.',
        'task': {'wires': task['wires'], 'before': grammar_form(task['before']),
                 'maximum_gates': 7, 'maximum_candidates': 8},
        'gate_semantics': 'All arguments put the target first, followed by controls. '
            'X flips a bit; CX and CCX flip their target when all controls are one. '
            'Z, CZ, and CCZ flip the sign when all listed bits are one. '
            'SWAP exchanges two bits; NEG flips every sign. '
            'Use only the numbered existing wires, with distinct wires within each gate. No scratch or measurements.',
        'output_syntax': syntax,
        'previous_attempts': history,
    }


def control(task, arm, seed, budget):
    if arm not in {'random', 'evolution', 'enumeration'} or budget < 1:
        raise ValueError('Invalid control or budget')
    rng = random.Random(seed)
    letters = alphabet(task['wires'])
    start = time.monotonic()
    population = [task['before']]
    attempts = []
    enumeration = itertools.chain.from_iterable(itertools.product(letters, repeat=n) for n in range(8))
    target_cost = cost(task['before'])
    scores = {}
    verifier_calls = 0
    def rank(gates):
        nonlocal verifier_calls
        key = grammar_form(gates)
        if key not in scores:
            proof = certify(task['before'], gates, task['wires'])
            verifier_calls += 1
            scores[key] = proof['mismatched_states'], cost(gates), len(gates)
        return scores[key]
    for _ in range(budget):
        if arm == 'enumeration':
            gates = list(next(enumeration))
        elif arm == 'random':
            gates = [rng.choice(letters) for _ in range(rng.randrange(8))]
        else:
            parent = population[0] if rng.random() < 0.75 else rng.choice(population)
            gates = list(parent)
            operation = rng.choice(['delete', 'replace', 'insert', 'move'])
            if operation == 'delete' and gates:
                del gates[rng.randrange(len(gates))]
            elif operation == 'replace' and gates:
                gates[rng.randrange(len(gates))] = rng.choice(letters)
            elif operation == 'insert' and len(gates) < 7:
                gates.insert(rng.randrange(len(gates) + 1), rng.choice(letters))
            elif operation == 'move' and len(gates) > 1:
                gate = gates.pop(rng.randrange(len(gates)))
                gates.insert(rng.randrange(len(gates) + 1), gate)
        result = assess(task, grammar_form(gates), 'grammar')
        verifier_calls += 1
        scores[result['program']] = result['mismatched_states'], cost(gates), len(gates)
        attempts.append(result)
        if arm == 'evolution':
            if gates not in population:
                population.append(gates)
            population.sort(key=rank)
            population = population[:8]
    valid = [r for r in attempts if r['accepted']]
    return {'arm': arm, 'seed': seed, 'budget': budget, 'task': task['id'], 'attempts': attempts,
            'best_cost': min([cost(r['gates']) for r in valid] + [target_cost]),
            'verifier_calls': verifier_calls, 'unique_programs': len(scores),
            'wall_seconds': time.monotonic() - start}


def freeze_rules(tasks, attempts):
    rules = []
    for task in tasks:
        candidates = [a for a in attempts.get(task['id'], []) if a.get('accepted')]
        if not candidates:
            continue
        winner = min(candidates, key=lambda a: (cost(a['gates']), len(a['gates']), grammar_form(a['gates'])))
        if not certify(task['before'], winner['gates'], task['wires'])['accepted']:
            raise ValueError('Saved proof does not match the rule')
        rules.append({'task': task['id'], 'wires': task['wires'], 'before': task['before'],
                      'after': winner['gates']})
    return rules
