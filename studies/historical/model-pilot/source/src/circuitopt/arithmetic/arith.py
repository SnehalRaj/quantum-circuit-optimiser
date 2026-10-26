"""Reversible integer and modular arithmetic on little-endian qubit registers.

Every carry is a temporary logical-AND. With measured uncomputation the carry
chain costs one Toffoli per bit; without it, two.
"""

from contextlib import contextmanager


# --- carry chains -----------------------------------------------------------

def _carry_chain(c, a, b, cin=None):
    """Compute carries 1..n-1 of a + b + cin. Leaves a_i, b_i XORed with carry_i."""
    n = len(b)
    carries = [None] * n
    carries[0] = cin
    for i in range(n - 1):
        ci = carries[i]
        if ci is not None:
            c.cx(ci, a[i])
            c.cx(ci, b[i])
        t = c.and_new(a[i], b[i])
        if ci is not None:
            c.cx(ci, t)
        carries[i + 1] = t
    return carries


def _carry_out(c, a, b, carries, flag, ctrl=None):
    """flag ^= carry-out of a + b (AND ctrl, if given). Restores the top bits."""
    i = len(b) - 1
    ci = carries[i]
    if ci is not None:
        c.cx(ci, a[i])
        c.cx(ci, b[i])
    if ctrl is None:
        c.ccx(a[i], b[i], flag)
        if ci is not None:
            c.cx(ci, flag)
    else:
        k = c.and_new(a[i], b[i])
        if ci is not None:
            c.cx(ci, k)
        c.ccx(ctrl, k, flag)
        if ci is not None:
            c.cx(ci, k)
        c.and_erase(a[i], b[i], k)
    if ci is not None:
        c.cx(ci, a[i])
        c.cx(ci, b[i])


def _uncarry(c, a, b, carries, write_sum):
    """Erase carries n-1..1. If write_sum, b becomes the sum bits."""
    for i in range(len(b) - 2, -1, -1):
        ci, t = carries[i], carries[i + 1]
        if ci is not None:
            c.cx(ci, t)
        c.and_erase(a[i], b[i], t)
        if ci is not None:
            c.cx(ci, a[i])
            if not write_sum:
                c.cx(ci, b[i])
        if write_sum:
            c.cx(a[i], b[i])


def add(c, a, b, cout=None, cin=None):
    """b <- a + b (+ cin) mod 2^n; cout ^= carry-out. n-1 Toffolis (+1 with cout)."""
    assert len(a) == len(b)
    carries = _carry_chain(c, a, b, cin)
    if cout is not None:
        _carry_out(c, a, b, carries, cout)
    i = len(b) - 1
    c.cx(a[i], b[i])
    if carries[i] is not None:
        c.cx(carries[i], b[i])
    _uncarry(c, a, b, carries, write_sum=True)


def sub(c, a, b, cout=None, cin=None):
    """b <- b - a (- cin) mod 2^n; cout ^= [a + cin > b]."""
    for q in b:
        c.x(q)
    add(c, a, b, cout, cin)
    for q in b:
        c.x(q)


def compare_gt(c, a, b, flag, ctrl=None):
    """flag ^= [a > b] (AND ctrl, if given). a and b are unchanged."""
    assert len(a) == len(b)
    for q in b:
        c.x(q)
    carries = _carry_chain(c, a, b)
    _carry_out(c, a, b, carries, flag, ctrl)
    _uncarry(c, a, b, carries, write_sum=False)
    for q in b:
        c.x(q)


def cswap(c, ctrl, a, b):
    """Swap registers a and b if ctrl. One Toffoli per bit."""
    for qa, qb in zip(a, b):
        c.cx(qb, qa)
        c.ccx(ctrl, qa, qb)
        c.cx(qb, qa)


# --- constants and controls -------------------------------------------------

@contextmanager
def const_reg(c, k, n):
    """A temporary register holding the classical constant k."""
    r = c.alloc(n)
    for i in range(n):
        if (k >> i) & 1:
            c.x(r[i])
    yield r
    for i in range(n):
        if (k >> i) & 1:
            c.x(r[i])
    c.free(r)


@contextmanager
def ctrl_const_reg(c, ctrl, k, n):
    """A temporary register holding k if ctrl, else 0. CNOT copies only."""
    r = c.alloc(n)
    for i in range(n):
        if (k >> i) & 1:
            c.cx(ctrl, r[i])
    yield r
    for i in range(n):
        if (k >> i) & 1:
            c.cx(ctrl, r[i])
    c.free(r)


@contextmanager
def masked(c, ctrl, a):
    """A temporary register m = ctrl AND a, bit by bit. n Toffolis, erased by measurement."""
    m = [c.and_new(ctrl, q) for q in a]
    yield m
    for q, t in zip(a, m):
        c.and_erase(ctrl, q, t)


def add_const(c, k, b):
    if getattr(c,'constant_lowering','legacy')=='direct':
        from .constant_kernel import add_constant
        return add_constant(c,k,b)
    with const_reg(c, k, len(b)) as r:
        add(c, r, b)


def sub_const(c, k, b):
    if getattr(c,'constant_lowering','legacy')=='direct':
        from .constant_kernel import sub_constant
        return sub_constant(c,k,b)
    with const_reg(c, k, len(b)) as r:
        sub(c, r, b)


def ctrl_add_const(c, ctrl, k, b):
    if getattr(c,'constant_lowering','legacy')=='direct':
        from .constant_kernel import add_constant
        return add_constant(c,k,b,control=ctrl)
    with ctrl_const_reg(c, ctrl, k, len(b)) as r:
        add(c, r, b)


def ctrl_sub_const(c, ctrl, k, b):
    if getattr(c,'constant_lowering','legacy')=='direct':
        from .constant_kernel import sub_constant
        return sub_constant(c,k,b,control=ctrl)
    with ctrl_const_reg(c, ctrl, k, len(b)) as r:
        sub(c, r, b)


def gt_const(c, a, k, flag):
    if getattr(c,'constant_lowering','legacy')=='direct':
        from .constant_kernel import greater_than_constant
        return greater_than_constant(c,a,k,flag)
    """flag ^= [a > k]."""
    with const_reg(c, k, len(a)) as r:
        compare_gt(c, a, r, flag)


def ctrl_add(c, ctrl, a, b):
    """b <- b + ctrl*a mod 2^n."""
    with masked(c, ctrl, a) as m:
        add(c, m, b)


def ctrl_sub(c, ctrl, a, b):
    """b <- b - ctrl*a mod 2^n."""
    with masked(c, ctrl, a) as m:
        sub(c, m, b)


# --- modular arithmetic, values in [0, p), registers of n bits, p < 2^n ---------

def mod_add(c, a, b, p):
    """b <- (a + b) mod p."""
    n = len(b)
    e = c.alloc1()
    B = b + [e]
    add(c, a, b, cout=e)                  # B = a + b, n+1 bits
    sub_const(c, p, B)                    # B = a + b - p mod 2^(n+1)
    f = c.alloc1()
    c.cx(B[n], f)                         # f = [a + b < p]
    ctrl_add_const(c, f, p, B)            # B = (a + b) mod p
    c.x(f)
    compare_gt(c, a, b, f)                # f ^= 1 ^ [a > result] clears f
    c.free([f])
    c.free([e])


def mod_sub(c, a, b, p):
    """b <- (b - a) mod p. The exact inverse of mod_add."""
    n = len(b)
    f = c.alloc1()
    c.x(f)
    compare_gt(c, a, b, f)                # f = [b >= a]
    e = c.alloc1()
    B = b + [e]
    ctrl_sub_const(c, f, p, B)
    c.cx(B[n], f)
    c.free([f])
    add_const(c, p, B)
    sub(c, a, b, cout=e)
    c.free([e])


def ctrl_mod_add(c, ctrl, a, b, p):
    with masked(c, ctrl, a) as m:
        mod_add(c, m, b, p)


def ctrl_mod_sub(c, ctrl, a, b, p):
    with masked(c, ctrl, a) as m:
        mod_sub(c, m, b, p)


def mod_half(c, v, p):
    """v <- v / 2 mod p. Returns the relabelled register."""
    h = (p + 1) // 2
    g = c.alloc1()
    c.cx(v[0], g)
    c.cx(g, v[0])                         # v0 = 0, g = old parity
    w = v[1:] + v[:1]                     # floor(v/2); the zero bit becomes the top
    ctrl_add_const(c, g, h, w)            # + g*(p+1)/2
    gt_const(c, w, h - 1, g)              # g ^= [w >= h] clears g
    c.free([g])
    return w


def mod_double(c, w, p):
    """w <- 2w mod p. The exact inverse of mod_half."""
    h = (p + 1) // 2
    g = c.alloc1()
    gt_const(c, w, h - 1, g)
    ctrl_sub_const(c, g, h, w)
    v = w[-1:] + w[:-1]
    c.cx(g, v[0])
    c.cx(v[0], g)
    c.free([g])
    return v


# --- shifts, signs and signed updates ---------------------------------------------

def ctrl_rotate_right(c, ctrl, v):
    """If ctrl, move every bit one place down and bit 0 to the top. n-1 Toffolis."""
    for j in range(len(v) - 1):
        cswap(c, ctrl, [v[j]], [v[j + 1]])


def ctrl_rotate_left(c, ctrl, v):
    for j in range(len(v) - 2, -1, -1):
        cswap(c, ctrl, [v[j]], [v[j + 1]])


def ctrl_mod_half(c, ctrl, v, p):
    """If ctrl, v <- v / 2 mod p. The register keeps its qubit order."""
    h = (p + 1) // 2
    g = c.alloc1()
    c.ccx(ctrl, v[0], g)                  # g = ctrl AND parity
    c.cx(g, v[0])
    ctrl_rotate_right(c, ctrl, v)
    ctrl_add_const(c, g, h, v)
    with const_reg(c, h - 1, len(v)) as r:
        compare_gt(c, v, r, g, ctrl=ctrl)  # clears g
    c.free([g])


def ctrl_mod_double(c, ctrl, v, p):
    h = (p + 1) // 2
    g = c.alloc1()
    with const_reg(c, h - 1, len(v)) as r:
        compare_gt(c, v, r, g, ctrl=ctrl)
    ctrl_sub_const(c, g, h, v)
    ctrl_rotate_left(c, ctrl, v)
    c.cx(g, v[0])
    c.ccx(ctrl, v[0], g)
    c.free([g])


def signed_add(c, e, s, t):
    """t <- t + (-1)^e s mod 2^W (two's complement). No Toffoli for the sign."""
    for q in s:
        c.cx(e, q)
    add(c, s, t, cin=e)
    for q in s:
        c.cx(e, q)


def signed_unadd(c, e, s, t):
    """The inverse of signed_add: t <- t - (-1)^e s."""
    for q in s:
        c.cx(e, q)
    sub(c, s, t, cin=e)
    for q in s:
        c.cx(e, q)


def ctrl_mod_neg_raw(c, e, a, p):
    """If e, a <- p - a (so 0 becomes p). Undo with ctrl_mod_unneg_raw."""
    for q in a:
        c.cx(e, q)
    ctrl_add_const(c, e, (p + 1) % (1 << len(a)), a)


def ctrl_mod_unneg_raw(c, e, a, p):
    ctrl_sub_const(c, e, (p + 1) % (1 << len(a)), a)
    for q in a:
        c.cx(e, q)


def signed_mod_add(c, e, a, b, p):
    """b <- b + (-1)^e a mod p."""
    ctrl_mod_neg_raw(c, e, a, p)
    mod_add(c, a, b, p)                   # mod_add also accepts a = p
    ctrl_mod_unneg_raw(c, e, a, p)


def signed_mod_unadd(c, e, a, b, p):
    ctrl_mod_neg_raw(c, e, a, p)
    mod_sub(c, a, b, p)
    ctrl_mod_unneg_raw(c, e, a, p)


def ctrl_mod_negate(c, e, a, p):
    """If e, a <- -a mod p, canonical (0 stays 0)."""
    z = c.alloc(len(a))
    mod_sub(c, a, z, p)                   # z = -a mod p
    cswap(c, e, a, z)
    mod_add(c, a, z, p)                   # z = 0
    c.free(z)
