"""Bounded gate programs and exact signed-permutation certificates.

Wire arguments put the target first. Gate programs never allocate scratch.
Source proposals pass through an AST whitelist; the harness never executes them.
"""
import ast
import itertools

ARITY = {'NEG': 0, 'X': 1, 'Z': 1, 'CX': 2, 'CZ': 2, 'SWAP': 2, 'CCX': 3, 'CCZ': 3}
MAX_GATES = 7


def validate(gates, wires):
    if type(wires) is not int or not 1 <= wires <= 6:
        raise ValueError('Use one to six wires')
    if not isinstance(gates, list) or len(gates) > MAX_GATES:
        raise ValueError('Use at most seven gates')
    for g in gates:
        if not isinstance(g, list) or not g or not isinstance(g[0], str) or g[0] not in ARITY:
            raise ValueError('Unknown gate')
        qs = g[1:]
        if len(qs) != ARITY[g[0]] or len(set(qs)) != len(qs):
            raise ValueError('Gate arity or wire alias error')
        if any(type(q) is not int or not 0 <= q < wires for q in qs):
            raise ValueError('Wire outside declared range')
    return gates


def parse_program(text, form, wires):
    if not isinstance(text, str) or len(text) > 6000:
        raise ValueError('Program must be a bounded string')
    if form == 'grammar':
        if not text.strip():
            return []
        gates = []
        for statement in text.strip().split(';'):
            parts = statement.split()
            if not parts or any(not q.isascii() or not q.isdecimal() for q in parts[1:]):
                raise ValueError('Use GATE target control; GATE target')
            gates.append([parts[0], *map(int, parts[1:])])
    elif form == 'source':
        try:
            tree = ast.parse(text)
        except (SyntaxError, RecursionError) as error:
            raise ValueError('Invalid source syntax') from error
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            raise ValueError('Source must define only rewrite(q)')
        f = tree.body[0]
        a = f.args
        if (f.name != 'rewrite' or f.decorator_list or f.returns or f.type_comment or
                getattr(f, 'type_params', []) or a.posonlyargs or a.kwonlyargs or
                a.defaults or a.kw_defaults or a.vararg or a.kwarg or len(a.args) != 1 or
                a.args[0].arg != 'q' or a.args[0].annotation or
                len(f.body) != 1 or not isinstance(f.body[0], ast.Return) or
                not isinstance(f.body[0].value, ast.List)):
            raise ValueError('Source must return a literal list of gate constructors')
        gates = []
        for call in f.body[0].value.elts:
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.keywords:
                raise ValueError('Only gate constructor calls are allowed')
            qs = []
            for arg in call.args:
                if (not isinstance(arg, ast.Subscript) or not isinstance(arg.value, ast.Name) or
                        arg.value.id != 'q' or not isinstance(arg.slice, ast.Constant) or
                        type(arg.slice.value) is not int):
                    raise ValueError('Use literal q[index] arguments')
                qs.append(arg.slice.value)
            gates.append([call.func.id, *qs])
    else:
        raise ValueError('Unknown proposal format')
    return validate(gates, wires)


def source_form(gates):
    calls = [g[0] + '(' + ', '.join(f'q[{q}]' for q in g[1:]) + ')' for g in gates]
    return 'def rewrite(q):\n    return [' + ', '.join(calls) + ']'


def grammar_form(gates):
    return '; '.join(' '.join(map(str, g)) for g in gates)


def signature(gates, wires):
    validate(gates, wires)
    values = []
    for initial in range(1 << wires):
        x, phase = initial, 0
        for kind, *qs in gates:
            bits = [(x >> q) & 1 for q in qs]
            if kind == 'NEG':
                phase ^= 1
            elif kind == 'X':
                x ^= 1 << qs[0]
            elif kind in {'Z', 'CZ', 'CCZ'}:
                phase ^= int(all(bits))
            elif kind in {'CX', 'CCX'}:
                if all(bits[1:]):
                    x ^= 1 << qs[0]
            elif kind == 'SWAP' and bits[0] != bits[1]:
                x ^= (1 << qs[0]) | (1 << qs[1])
        values.append((x << 1) | phase)
    return tuple(values)


def cost(gates):
    return sum(g[0] in {'CCX', 'CCZ'} for g in gates)


def certify(before, after, wires):
    a, b = signature(before, wires), signature(after, wires)
    mismatch = [i for i in range(len(a)) if a[i] != b[i]]
    saved = cost(before) - cost(after)
    return {'equivalent': not mismatch, 'accepted': not mismatch and saved > 0,
            'toffoli_saved': saved, 'checked_states': len(a), 'mismatched_states': len(mismatch),
            'counterexample': None if not mismatch else {'input': mismatch[0],
                'expected_state_and_sign': a[mismatch[0]], 'actual_state_and_sign': b[mismatch[0]]}}


def alphabet(wires):
    gates = [['NEG']]
    for kind, arity in ARITY.items():
        if not arity:
            continue
        for qs in itertools.permutations(range(wires), arity):
            if kind in {'CZ', 'CCZ', 'SWAP'} and tuple(sorted(qs)) != qs:
                continue
            if kind == 'CCX' and qs[1] > qs[2]:
                continue
            gates.append([kind, *qs])
    return gates


def nonlinear_rank(gates, wires):
    """Lower-bound CCX count by the output ANF rank modulo affine functions.

    Each CCX introduces at most one product into the span of output functions.
    Affine gates preserve that span. Phase gates do not change output bits.
    """
    values = signature(gates, wires)
    pivots = {}
    for bit in range(wires):
        coefficients = [(value >> (bit + 1)) & 1 for value in values]
        for j in range(wires):
            for x in range(1 << wires):
                if x & (1 << j):
                    coefficients[x] ^= coefficients[x ^ (1 << j)]
        row = sum(c << x for x, c in enumerate(coefficients) if x.bit_count() >= 2)
        while row:
            pivot = row.bit_length() - 1
            if pivot not in pivots:
                pivots[pivot] = row
                break
            row ^= pivots[pivot]
    return len(pivots)
