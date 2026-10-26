"""Bit-parallel reversible circuits with the ECDSA.fail gate set.

A Circuit records gates. simulate() runs every shot at once: each qubit holds
an int whose bit s is the qubit's value on shot s. The checks follow the
benchmark: a freed qubit must be 0 on every shot, and the phase must be clean.
Executed Toffolis count CCX and CCZ gates only on shots whose condition holds.
"""

import heapq
import random
from collections import Counter
from contextlib import contextmanager


class Circuit:
    def __init__(self, measured_uncompute=True, record=True):
        self.record = record          # False: count Toffolis and width only
        self.counts = Counter()       # static Toffolis by label
        self.ops = []                 # (kind, qubits, cond, label)
        self.free_ids = []
        self.next_id = 0
        self.live = 0
        self.peak = 0
        self.nbits = 0
        self.cond = None
        self.labels = ["main"]
        self.mbu = measured_uncompute

    # --- qubits and bits ---
    def alloc(self, k=1):
        out = []
        for _ in range(k):
            if self.free_ids:
                q = heapq.heappop(self.free_ids)
            else:
                q = self.next_id
                self.next_id += 1
            out.append(q)
        self.live += k
        self.peak = max(self.peak, self.live)
        return out

    def alloc1(self):
        return self.alloc(1)[0]

    def free(self, qs):
        for q in qs:
            self._emit("FREE", (q,))
            heapq.heappush(self.free_ids, q)
        self.live -= len(qs)

    def newbit(self):
        self.nbits += 1
        return self.nbits - 1

    # --- context ---
    @contextmanager
    def when(self, bit):
        assert self.cond is None, "nested conditions are not needed here"
        self.cond = bit
        yield
        self.cond = None

    @contextmanager
    def block(self, name):
        self.labels.append(name)
        yield
        self.labels.pop()

    def _emit(self, kind, qs):
        if kind in ("CCX", "CCZ"):
            self.counts[self.labels[-1]] += 1
        if self.record:
            self.ops.append((kind, qs, self.cond, self.labels[-1]))

    # --- gates ---
    def x(self, t): self._emit("X", (t,))
    def z(self, t): self._emit("Z", (t,))
    def cx(self, c, t): self._emit("CX", (c, t))
    def cz(self, a, b): self._emit("CZ", (a, b))
    def ccx(self, a, b, t): self._emit("CCX", (a, b, t))
    def ccz(self, a, b, c): self._emit("CCZ", (a, b, c))
    def swap(self, a, b): self._emit("SWAP", (a, b))

    def hmr(self, t):
        bit = self.newbit()
        if self.record:
            self.ops.append(("HMR", (t, bit), self.cond, self.labels[-1]))
        return bit

    # --- the temporary logical-AND (Gidney 2018) ---
    def and_new(self, a, b):
        """Allocate t and set t = a AND b. One Toffoli."""
        t = self.alloc1()
        self.ccx(a, b, t)
        return t

    def and_erase(self, a, b, t):
        """Clear t = a AND b and free it.

        Measured: X-basis measurement, then CZ(a, b) if the outcome is 1. No Toffoli.
        Otherwise: a second Toffoli.
        """
        if self.mbu:
            bit = self.hmr(t)
            with self.when(bit):
                self.cz(a, b)
        else:
            self.ccx(a, b, t)
        self.free([t])

    def static_toffolis(self):
        return sum(self.counts.values())


class Result:
    def __init__(self, state, bits, phase, toff, toff_by_label, dirty, shots, dirty_mask=0):
        self.state, self.bits, self.phase = state, bits, phase
        self.dirty_mask = dirty_mask          # shots on which some freed qubit was not 0
        self.toffoli_mean = toff / shots
        self.toffoli_by_label = {k: v / shots for k, v in toff_by_label.items()}
        self.dirty = dirty
        self.shots = shots

    def read(self, reg):
        """Per-shot integer values of a register (LSB first)."""
        vals = [0] * self.shots
        for i, q in enumerate(reg):
            m = self.state.get(q, 0)
            for s in range(self.shots):
                if (m >> s) & 1:
                    vals[s] |= 1 << i
        return vals

    @property
    def phase_clean(self):
        return self.phase == 0


def load(state, reg, values):
    for s, v in enumerate(values):
        for i, q in enumerate(reg):
            if (v >> i) & 1:
                state[q] = state.get(q, 0) | (1 << s)


def simulate(circ, inputs, shots, seed=0):
    """inputs: list of (register, per-shot values). Returns a Result."""
    rng = random.Random(seed)
    full = (1 << shots) - 1
    state, bits = {}, {}
    for reg, vals in inputs:
        load(state, reg, vals)
    phase = 0
    toff = 0
    by_label = Counter()
    dirty = []
    dirty_mask = 0
    for kind, qs, cond, label in circ.ops:
        m = full if cond is None else bits.get(cond, 0)
        if kind == "CCX":
            a, b, t = qs
            state[t] = state.get(t, 0) ^ (m & state.get(a, 0) & state.get(b, 0))
            k = bin(m).count("1")
            toff += k
            by_label[label] += k
        elif kind == "CX":
            c, t = qs
            state[t] = state.get(t, 0) ^ (m & state.get(c, 0))
        elif kind == "X":
            (t,) = qs
            state[t] = state.get(t, 0) ^ m
        elif kind == "SWAP":
            a, b = qs
            va, vb = state.get(a, 0), state.get(b, 0)
            d = (va ^ vb) & m
            state[a], state[b] = va ^ d, vb ^ d
        elif kind == "Z":
            phase ^= m & state.get(qs[0], 0)
        elif kind == "CZ":
            phase ^= m & state.get(qs[0], 0) & state.get(qs[1], 0)
        elif kind == "CCZ":
            phase ^= m & state.get(qs[0], 0) & state.get(qs[1], 0) & state.get(qs[2], 0)
            k = bin(m).count("1")
            toff += k
            by_label[label] += k
        elif kind == "HMR":
            t, bit = qs
            r = rng.getrandbits(shots) & m
            phase ^= state.get(t, 0) & r
            bits[bit] = (bits.get(bit, 0) & ~m) | r
            state[t] = state.get(t, 0) & ~m
        elif kind == "FREE":
            (q,) = qs
            if state.get(q, 0):
                dirty.append((q, label))
                dirty_mask |= state[q]
            state[q] = 0
        else:
            raise ValueError(kind)
    return Result(state, bits, phase, toff, by_label, dirty, shots, dirty_mask)
