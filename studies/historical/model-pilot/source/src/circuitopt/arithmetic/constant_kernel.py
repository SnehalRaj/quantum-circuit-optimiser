"""Exact little-endian constant arithmetic without a constant register.

Only carry wires use scratch. Each temporary AND has its exact predicate
restored before erasure, for both measurement and Toffoli cleanup.
"""

from operator import index


def _validate(data, extra=None):
    if len(set(data)) != len(data) or (extra is not None and extra in data):
        raise ValueError('The data register and control or flag must not alias')


def _carry_new(c, bit, incoming, one, control):
    """Compute carry from an unchanged data bit and a known constant bit."""
    if incoming is None:
        if not one:
            return None
        if control is not None:
            return c.and_new(control, bit)
        target = c.alloc1()
        c.cx(bit, target)
        return target
    if not one:
        return c.and_new(incoming, bit)
    if control is None:
        # incoming OR bit = 1 XOR ((NOT incoming) AND (NOT bit)).
        c.x(incoming)
        c.x(bit)
        target = c.and_new(incoming, bit)
        c.x(bit)
        c.x(incoming)
        c.x(target)
        return target
    # majority(control, bit, incoming).
    c.cx(incoming, control)
    c.cx(incoming, bit)
    target = c.and_new(control, bit)
    c.cx(incoming, target)
    c.cx(incoming, bit)
    c.cx(incoming, control)
    return target


def _sum_bit(c, bit, incoming, one, control):
    if one:
        if control is None:
            c.x(bit)
        else:
            c.cx(control, bit)
    if incoming is not None:
        c.cx(incoming, bit)


def _erase_sum_carry(c, bit, incoming, target, one, control):
    """Erase carry using the final sum bit; preserve that sum bit."""
    if target is None:
        return
    if incoming is None:
        if control is None:
            # The first carry equals the complement of this sum bit.
            c.cx(bit, target)
            c.x(target)
            c.free([target])
        else:
            c.cx(control, bit)
            c.and_erase(control, bit, target)
            c.cx(control, bit)
    elif not one:
        # carry = incoming AND NOT sum.
        c.x(bit)
        c.and_erase(incoming, bit, target)
        c.x(bit)
    elif control is None:
        # carry = 1 XOR (sum AND NOT incoming).
        c.x(target)
        c.x(incoming)
        c.and_erase(bit, incoming, target)
        c.x(incoming)
    else:
        # carry XOR incoming = (control XOR incoming) AND (sum XOR control).
        c.cx(incoming, target)
        c.cx(control, bit)
        c.cx(incoming, control)
        c.and_erase(control, bit, target)
        c.cx(incoming, control)
        c.cx(control, bit)


def add_constant(c, k, b, control=None):
    """Set b <- b + k*control modulo 2**len(b), with an implicit control of one."""
    b = list(b)
    _validate(b, control)
    k = index(k) % (1 << len(b))
    if not k:
        return
    carries = [None]
    for i, bit in enumerate(b):
        incoming = carries[i]
        one = (k >> i) & 1
        if i + 1 < len(b):
            carries.append(_carry_new(c, bit, incoming, one, control))
        _sum_bit(c, bit, incoming, one, control)
    for i in range(len(b) - 2, -1, -1):
        _erase_sum_carry(c, b[i], carries[i], carries[i + 1],
                         (k >> i) & 1, control)


def sub_constant(c, k, b, control=None):
    """Set b <- b - k*control modulo 2**len(b), preserving the control."""
    b = list(b)
    _validate(b, control)
    k = index(k) % (1 << len(b))
    if not k:
        return
    # N - ((N - b) + control*k) = b - control*k, where N = 2**n - 1.
    for bit in b:
        c.x(bit)
    add_constant(c, k, b, control=control)
    for bit in b:
        c.x(bit)


def _erase_unchanged_carry(c, bit, incoming, target, one):
    if target is None:
        return
    if incoming is None:
        c.cx(bit, target)
        c.free([target])
    elif not one:
        c.and_erase(incoming, bit, target)
    else:
        c.x(target)
        c.x(incoming)
        c.x(bit)
        c.and_erase(incoming, bit, target)
        c.x(bit)
        c.x(incoming)


def greater_than_constant(c, a, k, flag):
    """Set flag <- flag XOR [a > k], for unsigned a and any integer k."""
    a = list(a)
    _validate(a, flag)
    k = index(k)
    maximum = (1 << len(a)) - 1
    if k < 0:
        c.x(flag)
        return
    if k >= maximum:
        return
    # a > k iff a + (2**n - 1 - k) has a carry-out.
    complement = maximum - k
    carries = [None]
    for i, bit in enumerate(a):
        carries.append(_carry_new(c, bit, carries[i], (complement >> i) & 1, None))
    if carries[-1] is not None:
        c.cx(carries[-1], flag)
    for i in range(len(a) - 1, -1, -1):
        _erase_unchanged_carry(c, a[i], carries[i], carries[i + 1],
                               (complement >> i) & 1)
