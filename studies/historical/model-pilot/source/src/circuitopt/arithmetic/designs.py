"""Division circuits lambda = y / x mod p, one design per reduction.

The walk: (u, v) = (p, x). Each step records b = [v odd] and s = b AND [u > v],
swaps if s, subtracts u from v if b, then halves v. It ends at (1, 0).
The payload keeps U = u*mu and V = v*mu (mod p) for a fixed mu.
Started at (0, y) it ends at (y/x, 0); started at (0, 1) it ends at (1/x, 0).
"""

from . import arith as A


# --- the classical walk -----------------------------------------------------------

def walk_trace(p, x):
    u, v, steps = p, x, []
    while v:
        b = v & 1
        s = b & (u > v)
        if s:
            u, v = v, u
        if b:
            v -= u
        v >>= 1
        steps.append((b, s, u, v))
    return steps


def walk_length(p):
    return max(len(walk_trace(p, x)) for x in range(1, p))


# --- one walk step and its inverse ------------------------------------------------

def walk_step(c, u, v, compare_bits=None):
    """Returns (new v, transcript bits b, s). compare_bits: compare only the top
    bits of the active width (an approximation: a tie in the window says u <= v)."""
    b = c.alloc1()
    c.cx(v[0], b)
    s = c.alloc1()
    k = len(u) if compare_bits is None else min(compare_bits, len(u))
    A.compare_gt(c, u[-k:], v[-k:], s, ctrl=b)  # s = b AND [u > v]
    A.cswap(c, s, u, v)
    A.ctrl_sub(c, b, u, v)                # v is now even
    return v[1:] + v[:1], b, s            # halving is a relabelling


def unwalk_step(c, u, v, b, s, compare_bits=None):
    v = v[-1:] + v[:-1]
    A.ctrl_add(c, b, u, v)
    A.cswap(c, s, u, v)
    k = len(u) if compare_bits is None else min(compare_bits, len(u))
    A.compare_gt(c, u[-k:], v[-k:], s, ctrl=b)  # clears s
    c.cx(v[0], b)                         # clears b
    c.free([s, b])
    return v


def payload_step(c, U, V, b, s, p):
    if getattr(c,'payload_program',None) is not None:
        from ..block_program import emit
        return emit(c,U,V,b,s,p,c.payload_program)
    A.cswap(c, s, U, V)
    A.ctrl_mod_sub(c, b, U, V, p)
    return A.mod_half(c, V, p)


def unpayload_step(c, U, V, b, s, p):
    if getattr(c,'payload_program',None) is not None:
        from ..block_program import emit
        return emit(c,U,V,b,s,p,c.payload_program,inverse=True)
    V = A.mod_double(c, V, p)
    A.ctrl_mod_add(c, b, U, V, p)
    A.cswap(c, s, U, V)
    return V


def load_const(c, reg, k):
    for i, q in enumerate(reg):
        if (k >> i) & 1:
            c.x(q)


# --- multiplication for the baseline ------------------------------------------------

def mod_mul_acc(c, target, a, bits, p, subtract=False):
    """target <- target +/- a * int(bits) mod p, by shift and add. a is restored."""
    for i, q in enumerate(bits):
        (A.ctrl_mod_sub if subtract else A.ctrl_mod_add)(c, q, a, target, p)
        if i < len(bits) - 1:
            a = A.mod_double(c, a, p)
    for _ in range(len(bits) - 1):
        a = A.mod_half(c, a, p)
    return a


# --- the designs -------------------------------------------------------------------
# Each takes registers x, y (n bits) and returns the register that holds y/x.
# x is restored. Every other qubit is freed clean.

def d0_inverse_then_multiply(c, x, y, p, steps):
    """Baseline: compute 1/x, multiply, uncompute 1/x, then clear y with a second multiply."""
    n = len(x)
    u = c.alloc(n)
    load_const(c, u, p)
    U, V = c.alloc(n), c.alloc(n)
    c.x(V[0])
    v, record = x, []
    with c.block("walk"):
        for _ in range(steps):
            v, b, s = walk_step(c, u, v)
            record.append((b, s))
    with c.block("payload"):
        for b, s in record:
            V = payload_step(c, U, V, b, s, p)        # U = 1/x
    Z = c.alloc(n)
    with c.block("multiply"):
        y = mod_mul_acc(c, Z, y, U, p)                # Z = y/x
    with c.block("payload"):
        for b, s in reversed(record):
            V = unpayload_step(c, U, V, b, s, p)
    with c.block("unwalk"):
        for b, s in reversed(record):
            v = unwalk_step(c, u, v, b, s)
    with c.block("multiply"):
        Z = mod_mul_acc(c, y, Z, v, p, subtract=True)  # y = y - (y/x)*x = 0
    c.x(V[0])
    load_const(c, u, p)
    c.free(U + V + u + y)
    return v, Z


def width_bound(n, t):
    """Bits that hold max(u, v) after t steps. u*v at least halves each step,
    and u*v < 2^(2n) at the start, so max(u, v) < 2^(2n - t)."""
    return max(1, min(n, 2 * n - t))


def d2_trimmed_walk(c, x, y, p, steps, widths=None, compare_bits=None):
    """D1, but the walk works on provably non-zero bits only.

    After step n the bound drops one bit per step for each of u and v. Those
    two zero qubits are freed (the simulator checks they are 0 on every input),
    so the next transcript bits reuse them: the transcript lives in freed lanes.
    widths: optional per-step width schedule; a narrower one is an approximation.
    """
    n = len(x)
    widths = widths or [width_bound(n, t) for t in range(steps + 1)]
    u = c.alloc(n)
    load_const(c, u, p)
    v, record = x, []
    with c.block("walk"):
        for t in range(steps):
            w, w_next = widths[t], widths[t + 1]
            v, b, s = walk_step(c, u, v, compare_bits)  # v = (v - b u)/2; zero bit now on top
            for _ in range(w - w_next):
                c.free([v[-1], u[-1]])                # provably 0 under the bound
                u, v = u[:-1], v[:-1]
            record.append((b, s))
    U, V = c.alloc(n), y
    with c.block("payload"):
        for b, s in record:
            V = payload_step(c, U, V, b, s, p)
    with c.block("unwalk"):
        for t in reversed(range(steps)):
            b, s = record[t]
            w, w_next = widths[t], widths[t + 1]
            for _ in range(w - w_next):
                u, v = u + c.alloc(1), v + c.alloc(1)
            v = unwalk_step(c, u, v, b, s, compare_bits)
    load_const(c, u, p)
    c.free(u + V)
    return v, U


def d1_record_and_replay(c, x, y, p, steps):
    """Record the walk's decisions, replay them on (0, y), then unwalk."""
    n = len(x)
    u = c.alloc(n)
    load_const(c, u, p)
    v, record = x, []
    with c.block("walk"):
        for _ in range(steps):
            v, b, s = walk_step(c, u, v)
            record.append((b, s))
    U, V = c.alloc(n), y
    with c.block("payload"):
        for b, s in record:
            V = payload_step(c, U, V, b, s, p)        # (0, y) -> (y/x, 0)
    with c.block("unwalk"):
        for b, s in reversed(record):
            v = unwalk_step(c, u, v, b, s)
    load_const(c, u, p)
    c.free(u + V)
    return v, U


# --- D3: two halvings per step (Jump-2) ---------------------------------------------

def jump2_trace(p, x):
    """Macro-steps until (u, v) = (1, 0). Open Autoresearch Alg. 4."""
    u, v, out, i = p, x, [], 0
    while (u, v) != (1, 0):
        t1 = 1 - (v & 1) if i == 0 else 1
        if t1:
            v >>= 1
        s2 = 1 - (v & 1)
        if s2:
            v >>= 1
        b = v & 1
        s = b if i == 0 else b & (u > v)
        if s:
            u, v = v, u
        if b:
            v -= u
        out.append((b, s, s2))
        i += 1
    return out


def jump2_length(p):
    return max(len(jump2_trace(p, x)) for x in range(1, p))


def jump2_width(n, i):
    """Bits for u and v at the start of macro-step i. Every macro-step after the
    first halves u*v at least once, so max(u, v) < 2^(2n - i + 1)."""
    return n if i == 0 else max(2, min(n, 2 * n - i + 1))


def d3_jump2(c, x, y, p, steps, trim=False, widths=None):
    """Record-and-replay with macro-steps: up to two halvings, then at most one
    compare, swap and subtraction. Fewer steps means fewer comparators and swaps.
    trim: shrink u and v by the product bound and free the zero bits (exact).
    widths: optional per-step schedule; a narrower one is an approximation."""
    n = len(x)
    widths = widths or [jump2_width(n, i) if trim else n for i in range(steps + 1)]
    u = c.alloc(n)
    load_const(c, u, p)
    v, record = x, []
    with c.block("walk"):
        for i in range(steps):
            t1 = None
            if i == 0:
                t1 = c.alloc1()
                c.cx(v[0], t1)
                c.x(t1)                                  # t1 = [x even]
                A.ctrl_rotate_right(c, t1, v)
            else:
                v = v[1:] + v[:1]                        # deferred halving: v is even here
            s2 = c.alloc1()
            c.cx(v[0], s2)
            c.x(s2)
            A.ctrl_rotate_right(c, s2, v)
            b = c.alloc1()
            c.cx(v[0], b)
            s = c.alloc1()
            if i == 0:
                c.cx(b, s)                               # u = p > v at step 0
            else:
                A.compare_gt(c, u, v, s, ctrl=b)
            A.cswap(c, s, u, v)
            A.ctrl_sub(c, b, u, v)
            record.append((t1, s2, b, s))
            drop = widths[i] - widths[i + 1]
            if drop > 0:
                c.free(u[-drop:] + v[-drop:])            # provably 0: checked on every input
                u, v = u[:-drop], v[:-drop]
    U, V = c.alloc(n), y
    with c.block("payload"):
        for i, (t1, s2, b, s) in enumerate(record):
            if i == 0:
                A.ctrl_mod_half(c, t1, V, p)
            else:
                V = A.mod_half(c, V, p)
            A.ctrl_mod_half(c, s2, V, p)
            A.cswap(c, s, U, V)
            A.ctrl_mod_sub(c, b, U, V, p)
    with c.block("unwalk"):
        for i in reversed(range(steps)):
            t1, s2, b, s = record[i]
            drop = widths[i] - widths[i + 1]
            if drop > 0:
                u, v = u + c.alloc(drop), v + c.alloc(drop)
            A.ctrl_add(c, b, u, v)
            A.cswap(c, s, u, v)
            if i == 0:
                c.cx(b, s)
            else:
                A.compare_gt(c, u, v, s, ctrl=b)
            c.cx(v[0], b)
            A.ctrl_rotate_left(c, s2, v)
            c.cx(v[0], s2)
            c.x(s2)
            c.free([s, b, s2])
            if i == 0:
                A.ctrl_rotate_left(c, t1, v)
                c.cx(v[0], t1)
                c.x(t1)
                c.free([t1])
            else:
                v = v[-1:] + v[:-1]
    load_const(c, u, p)
    c.free(u + V)
    return v, U


# --- D4: ping-pong (no comparison, no swap) -----------------------------------------

def pingpong_trace(p, x):
    """Rounds until both signed rails are +-1. Record-and-replay paper, Alg. 3."""
    r = [p, x if x & 1 else x - p]
    out, i = [], 0
    while not (abs(r[0]) == 1 and abs(r[1]) == 1):
        t = 1 if i % 2 == 0 else 0
        s = 1 - t
        e = ((r[t] >> 1) & 1) ^ ((r[s] >> 1) & 1)
        r[t] = (r[t] + (-1) ** e * r[s]) // 2
        out.append(e)
        i += 1
    return out, r


def pingpong_length(p):
    return max(len(pingpong_trace(p, x)[0]) for x in range(1, p))


def d4_pingpong(c, x, y, p, steps, widths=None):
    """Two odd signed rails. Round i updates rail (i+1) mod 2:
    e = bit1(t) XOR bit1(s); t <- (t + (-1)^e s) / 2. One recorded bit per round,
    no comparator, no controlled swap. The payload follows: c_t <- (c_t +/- c_s)/2.
    widths: optional rail widths before each round; the top bits that copy the sign
    are cleared and freed (exact only if every input fits the schedule)."""
    n = len(x)
    W = n + 2
    widths = widths or [W] * (steps + 1)
    r0 = c.alloc(W)
    load_const(c, r0, p)
    r1 = x + c.alloc(2)
    with c.block("walk"):
        g = c.alloc1()                                   # lift: x even -> x - p
        c.cx(r1[0], g)
        c.x(g)
        A.ctrl_sub_const(c, g, p, r1)
        c.cx(r1[W - 1], g)                               # g equals the sign: clear it
        c.free([g])
        rails, record = [r0, r1], []
        for i in range(steps):
            t = 1 if i % 2 == 0 else 0
            T, S = rails[t], rails[1 - t]
            e = c.alloc1()
            c.cx(T[1], e)
            c.cx(S[1], e)
            A.signed_add(c, e, S, T)                     # T = 2 mod 4: halve exactly
            T = T[1:] + T[:1]
            c.cx(T[-2], T[-1])                           # sign-extend into the freed bit
            rails[t] = T
            record.append(e)
            _narrow(c, rails, widths[i] - widths[i + 1])
    C = [c.alloc(n), y]
    with c.block("payload"):
        for i, e in enumerate(record):
            t = 1 if i % 2 == 0 else 0
            A.signed_mod_add(c, e, C[1 - t], C[t], p)
            C[t] = A.mod_half(c, C[t], p)
        for k in (0, 1):                                 # c_k = rho_k * lambda, rho_k = +-1
            A.ctrl_mod_negate(c, rails[k][-1], C[k], p)
        for q0, q1 in zip(C[0], C[1]):
            c.cx(q0, q1)                                 # both copies equal lambda
    with c.block("unwalk"):
        for i in reversed(range(steps)):
            _widen(c, rails, widths[i] - widths[i + 1])
            t = 1 if i % 2 == 0 else 0
            T, S, e = rails[t], rails[1 - t], record[i]
            c.cx(T[-2], T[-1])
            T = T[-1:] + T[:-1]
            A.signed_unadd(c, e, S, T)
            c.cx(T[1], e)
            c.cx(S[1], e)
            c.free([e])
            rails[t] = T
        r0, r1 = rails
        g = c.alloc1()
        c.cx(r1[W - 1], g)
        A.ctrl_add_const(c, g, p, r1)
        c.x(g)
        c.cx(r1[0], g)
        c.free([g])
    load_const(c, r0, p)
    c.free(r0 + r1[n:] + C[1])
    return r1[:n], C[0]


def _narrow(c, rails, drop):
    """Free `drop` top bits of each signed rail. Each must equal the sign bit below it."""
    for _ in range(drop):
        for k in (0, 1):
            R = rails[k]
            c.cx(R[-2], R[-1])
            c.free([R[-1]])
            rails[k] = R[:-1]


def _widen(c, rails, grow):
    for _ in range(grow):
        for k in (1, 0):
            q = c.alloc1()
            c.cx(rails[k][-1], q)                        # sign-extend
            rails[k] = rails[k] + [q]


def d5_jump2_trimmed(c, x, y, p, steps):
    """D3 with the exact width trim of D2."""
    return d3_jump2(c, x, y, p, steps, trim=True)


# --- D6: Leapfrog (two forced ping-pong steps, one chosen step, a 0-3 bit shift) ----

def _v2(n):
    return 99 if n == 0 else (n & -n).bit_length() - 1


def leapfrog_trace(p, x):
    """Ticks until both rails are +-1. Greedy toy rule: take the step whose sum is
    0 mod 4 ("deep") when its 2-adic valuation is at most 4, so the shift fits in 0-3.
    The frontier circuit (0c3a0751) uses a tuned rule with magnitude tests."""
    r0 = x if x & 1 else x - p
    e = ((p >> 1) ^ (r0 >> 1)) & 1
    R = [r0, (p + (-1) ** e * r0) // 2]            # seed: one ping-pong round on the p rail
    out, t = [], 0
    while not (abs(R[0]) == 1 and abs(R[1]) == 1):
        ti, si = t % 2, 1 - t % 2
        T, B, letter = R[ti], R[si], []
        for _ in range(2):
            sg = ((T >> 1) ^ (B >> 1)) & 1
            T = (T - B) // 2 if sg else (T + B) // 2
            letter.append(sg)
        dq = ((T >> 1) ^ (B >> 1)) & 1
        deep = _v2(T + (B if dq else -B)) <= 4
        s3 = int(deep) ^ dq
        T = (T - B) // 2 if s3 else (T + B) // 2
        k = _v2(T)
        R[ti] = T >> k
        out.append(letter + [s3, k & 1, k >> 1])
        t += 1
    return out, R


def leapfrog_length(p):
    return max(len(leapfrog_trace(p, x)[0]) for x in range(1, p))


def _halve_signed(c, T):
    """T is even: drop the zero LSB (relabel) and sign-extend into it."""
    W = len(T)
    T = T[1:] + T[:1]
    c.cx(T[W - 2], T[W - 1])
    return T


def _unhalve_signed(c, T):
    W = len(T)
    c.cx(T[W - 2], T[W - 1])
    return T[-1:] + T[:-1]


def _ctrl_shift_signed(c, k, T):
    """If k, arithmetic shift right by one (bit 0 must be 0)."""
    W = len(T)
    A.ctrl_rotate_right(c, k, T)
    c.ccx(k, T[W - 2], T[W - 1])


def _ctrl_unshift_signed(c, k, T):
    W = len(T)
    c.ccx(k, T[W - 2], T[W - 1])
    A.ctrl_rotate_left(c, k, T)


def _pp_step(c, T, B):
    """One ping-pong step on target T. Returns (T, recorded sign bit)."""
    e = c.alloc1()
    c.cx(T[1], e)
    c.cx(B[1], e)
    A.signed_add(c, e, B, T)
    return _halve_signed(c, T), e


def _pp_unstep(c, T, B, e):
    T = _unhalve_signed(c, T)
    A.signed_unadd(c, e, B, T)
    c.cx(T[1], e)
    c.cx(B[1], e)
    c.free([e])
    return T


def _choice_bit(c, T, B, out):
    """out ^= s3 = deep XOR dq, from the low five bits of T +/- B."""
    dq = c.alloc1()
    c.cx(T[1], dq)
    c.cx(B[1], dq)
    nd = dq                                          # deep candidate: T + B if dq else T - B
    c.x(nd)                                          # nd = 1 - dq: subtract when dq = 0
    z = c.alloc(5)
    for i in range(5):
        c.cx(T[i], z[i])
    A.signed_add(c, nd, B[:5], z)                    # z = (T +/- B) mod 32, and z0 = z1 = 0
    for q in z[2:]:
        c.x(q)
    a = c.and_new(z[2], z[3])
    flat = c.and_new(a, z[4])                        # flat = [z2 = z3 = z4 = 0] = not deep
    c.cx(flat, out)
    c.cx(nd, out)                                    # out = flat XOR (1 - dq) = deep XOR dq
    c.and_erase(a, z[4], flat)
    c.and_erase(z[2], z[3], a)
    for q in z[2:]:
        c.x(q)
    A.signed_unadd(c, nd, B[:5], z)
    for i in range(5):
        c.cx(T[i], z[i])
    c.free(z)
    c.x(nd)
    c.cx(T[1], dq)
    c.cx(B[1], dq)
    c.free([dq])


def _shift_bits(c, T, k1, k2):
    """k1 ^= [v2(T) odd], k2 ^= [v2(T) >= 2], for v2(T) in 0..3."""
    for q in T[:3]:
        c.x(q)                                       # now t0, t1, t2 hold their complements
    c.ccx(T[0], T[1], k2)                            # k2 = not t0 and not t1
    w = c.and_new(T[1], T[2])                        # w = not t1 and t2... complemented: (not t1) and (not t2)
    c.x(T[2])                                        # T[2] = t2 again
    w2 = c.and_new(T[1], T[2])                       # w2 = (not t1) and t2
    c.x(w2)                                          # w2 = t1 or not t2
    c.ccx(T[0], w2, k1)                              # k1 = not t0 and (t1 or not t2)
    c.x(w2)
    c.and_erase(T[1], T[2], w2)
    c.x(T[2])
    c.and_erase(T[1], T[2], w)
    for q in T[:3]:
        c.x(q)


def d6_leapfrog(c, x, y, p, steps, widths=None):
    """Leapfrog: each tick makes two forced ping-pong steps, one chosen step, and
    shifts by v2 in 0..3. Five recorded bits per tick; no full-width comparison.
    widths: optional rail widths before each tick (see d4_pingpong)."""
    n = len(x)
    W = n + 2
    widths = widths or [W] * (steps + 1)
    rails = [x + c.alloc(2), c.alloc(W)]
    load_const(c, rails[1], p)
    with c.block("walk"):
        g = c.alloc1()                                   # lift x to an odd rail
        c.cx(rails[0][0], g)
        c.x(g)
        A.ctrl_sub_const(c, g, p, rails[0])
        c.cx(rails[0][W - 1], g)
        c.free([g])
        rails[1], seed = _pp_step(c, rails[1], rails[0])
        record = []
        for t in range(steps):
            ti = t % 2
            T, B = rails[ti], rails[1 - ti]
            T, s0 = _pp_step(c, T, B)
            T, s1 = _pp_step(c, T, B)
            s3 = c.alloc1()
            _choice_bit(c, T, B, s3)
            A.signed_add(c, s3, B, T)
            T = _halve_signed(c, T)
            k1, k2 = c.alloc1(), c.alloc1()
            _shift_bits(c, T, k1, k2)
            _ctrl_shift_signed(c, k2, T)
            _ctrl_shift_signed(c, k2, T)
            _ctrl_shift_signed(c, k1, T)
            rails[ti] = T
            record.append((s0, s1, s3, k1, k2))
            _narrow(c, rails, widths[t] - widths[t + 1])
    P = [y, c.alloc(n)]
    with c.block("payload"):
        A.signed_mod_add(c, seed, P[0], P[1], p)         # P1 = (0 +/- y) / 2
        P[1] = A.mod_half(c, P[1], p)
        for t, (s0, s1, s3, k1, k2) in enumerate(record):
            ti = t % 2
            for sg in (s0, s1, s3):
                A.signed_mod_add(c, sg, P[1 - ti], P[ti], p)
                P[ti] = A.mod_half(c, P[ti], p)
            A.ctrl_mod_half(c, k2, P[ti], p)
            A.ctrl_mod_half(c, k2, P[ti], p)
            A.ctrl_mod_half(c, k1, P[ti], p)
        for k in (0, 1):
            A.ctrl_mod_negate(c, rails[k][-1], P[k], p)
        for q0, q1 in zip(P[0], P[1]):
            c.cx(q0, q1)
    with c.block("unwalk"):
        for t in reversed(range(steps)):
            _widen(c, rails, widths[t] - widths[t + 1])
            ti = t % 2
            T, B = rails[ti], rails[1 - ti]
            s0, s1, s3, k1, k2 = record[t]
            _ctrl_unshift_signed(c, k1, T)
            _ctrl_unshift_signed(c, k2, T)
            _ctrl_unshift_signed(c, k2, T)
            _shift_bits(c, T, k1, k2)
            c.free([k1, k2])
            T = _unhalve_signed(c, T)
            A.signed_unadd(c, s3, B, T)
            _choice_bit(c, T, B, s3)
            c.free([s3])
            T = _pp_unstep(c, T, B, s1)
            T = _pp_unstep(c, T, B, s0)
            rails[ti] = T
        rails[1] = _pp_unstep(c, rails[1], rails[0], seed)
        g = c.alloc1()
        c.cx(rails[0][W - 1], g)
        A.ctrl_add_const(c, g, p, rails[0])
        c.x(g)
        c.cx(rails[0][0], g)
        c.free([g])
    load_const(c, rails[1], p)
    c.free(rails[1] + rails[0][n:] + P[1])
    return rails[0][:n], P[0]
