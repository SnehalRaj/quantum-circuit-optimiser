"""Select ordered replay programs by their exact classical value map.

A value map is the linear action on the two field registers, u and v.
The native registers u and v must be disjoint.
The canonical cell maps (u, v) to (u, (v + sign*u)/2). The sign is
+1 or -1, and the selector checks both with exact field arithmetic.

This selector is a proposal heuristic. The fixed native evaluator must
still check phase, scratch cleanup, approximate lowerings, and the full
point-addition circuit. A matching value map proves none of those duties.
The grammar can still express programs whose value maps differ.
"""
from functools import lru_cache


SECP256K1_PRIME = 2**256 - 2**32 - 977
_OPERATIONS = ('add', 'half', 'sub', 'double', 'half_u', 'double_u', 'swap')
_IDENTITY = (1, 0, 0, 1)


def _multiply(left, right, prime):
    a, b, c, d = left
    e, f, g, h = right
    return ((a*e+b*g) % prime, (a*f+b*h) % prime,
            (c*e+d*g) % prime, (c*f+d*h) % prime)


@lru_cache(maxsize=32768)
def _inverse(matrix, prime):
    a, b, c, d = matrix
    scale = pow((a*d-b*c) % prime, -1, prime)
    return tuple(value*scale % prime for value in (d, -b, -c, a))


def _append(maps, operation, prime):
    result = []
    half = (prime+1)//2
    for matrix, sign in zip(maps, (1, -1)):
        a, b, c, d = matrix
        if operation == 'swap':
            matrix = (c, d, a, b)
        elif operation == 'add':
            matrix = (a, b, (c+sign*a) % prime, (d+sign*b) % prime)
        elif operation == 'sub':
            matrix = (a, b, (c-sign*a) % prime, (d-sign*b) % prime)
        elif operation == 'half':
            matrix = (a, b, c*half % prime, d*half % prime)
        elif operation == 'double':
            matrix = (a, b, c*2 % prime, d*2 % prime)
        elif operation == 'half_u':
            matrix = (a*half % prime, b*half % prime, c, d)
        elif operation == 'double_u':
            matrix = (a*2 % prime, b*2 % prime, c, d)
        else:
            raise ValueError('Unknown replay operation')
        result.append(matrix)
    return tuple(result)


def _program_maps(program, prime=SECP256K1_PRIME):
    """Return the exact 2x2 matrices for sign +1 and sign -1."""
    if type(prime) is not int or prime < 3 or prime % 2 == 0:
        raise ValueError('The field modulus must be an odd prime')
    if not isinstance(program, (list, tuple)):
        raise ValueError('A replay program must be a list of instructions')
    maps = (_IDENTITY, _IDENTITY)
    for instruction in program:
        if (not isinstance(instruction, dict)
                or set(instruction) != {'op', 'lowering'}
                or instruction['op'] not in _OPERATIONS
                or instruction['lowering'] not in ('direct', 'power2')):
            raise ValueError('Invalid replay instruction')
        maps = _append(maps, instruction['op'], prime)
    return maps


def preserves_replay_map(program):
    """Check classical values over secp256k1 for both sign values.

    Lowering choices do not change the symbolic target. Their emitted
    operations and any approximation remain native evaluator obligations.
    """
    try:
        maps = _program_maps(program)
    except (ValueError, TypeError):
        return False
    half = (SECP256K1_PRIME+1)//2
    return maps == ((1, 0, half, half),
                    (1, 0, (-half) % SECP256K1_PRIME, half))


def _next_layer(previous, beam_width):
    """Group equal maps, then retain distinct ordered paths in rounds."""
    groups = {}
    for maps, paths in previous.items():
        for path in paths:
            for operation in _OPERATIONS:
                key = _append(maps, operation, SECP256K1_PRIME)
                group = groups.setdefault(key, [])
                if len(group) < 8:
                    group.append(path + (operation,))
    retained = {}
    count = 0
    for round_index in range(8):
        for maps, paths in groups.items():
            if round_index < len(paths):
                retained.setdefault(maps, []).append(paths[round_index])
                count += 1
                if count == beam_width:
                    return retained
    return retained


@lru_cache(maxsize=32)
def _equivalent_sequences(max_length, limit, beam_width):
    if max_length < 2 or limit == 0:
        return ()
    prime = SECP256K1_PRIME
    half = (prime+1)//2
    target = ((1, 0, half, half), (1, 0, -half % prime, half))
    layers = [{(_IDENTITY, _IDENTITY): [()]}]
    found = set()
    for length in range(2, max_length+1):
        left_length = length//2
        right_length = length-left_length
        while len(layers) <= right_length:
            layers.append(_next_layer(layers[-1], beam_width))
        for maps, left_paths in layers[left_length].items():
            required = tuple(_multiply(goal, _inverse(matrix, prime), prime)
                             for goal, matrix in zip(target, maps))
            right_paths = layers[right_length].get(required, ())
            for left in left_paths:
                for right in right_paths:
                    found.add(left+right)
        if len(found) >= limit:
            break
    return tuple(sorted(found, key=lambda path: (len(path), path))[:limit])


def equivalent_programs(max_length=6, limit=64, beam_width=4096):
    """Find equivalent programs through bounded deterministic enumeration.

    Each layer retains at most beam_width paths, with up to eight ordered
    paths per map. Meet-in-the-middle matching joins exact maps for both
    signs. Pruned searches need not return every equivalent program.
    Returned instructions use direct lowering. Every call returns new
    lists and dictionaries, including when the search result is cached.
    """
    for name, value in (('max_length', max_length), ('limit', limit),
                        ('beam_width', beam_width)):
        if type(value) is not int or value < (1 if name == 'beam_width' else 0):
            raise ValueError(name+' must be a positive integer' if name == 'beam_width'
                             else name+' must be a nonnegative integer')
    return [[{'op': operation, 'lowering': 'direct'} for operation in path]
            for path in _equivalent_sequences(max_length, limit, beam_width)]
