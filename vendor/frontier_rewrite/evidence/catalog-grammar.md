# Review: bounded exact unitary catalog

Reviewed 24 October 2026. Scope: the proposed catalog contract. `catalog.rs` was not present at inspection. This is a mathematical approval of the contract, rather than an implementation review. No native source or frozen experiment changed.

**[EXACT] Full signed-permutation signatures certify exact arbitrary-complex-state equivalence for the stated gate set.** They include global phase. Sorted role lifting and a shared outer classical condition preserve that equality.

Whether this grammar reduces executed Toffolis on any of the fastest three sources remains an empirical hypothesis. A no-match result supplies no global optimality claim.

## Conventions and proof

Use the full k-qubit space for `k=2,3,4`. Use lexicographic computational basis order, with local role zero leftmost. Matrices act on complex column vectors. There is no continuous parameter. The sorted physical wire list defines the local roles.

The allowed gates are `X,Z,CX,CZ,Swap,CCX,CCZ,Neg`, with valid distinct operands and no explicit classical conditions. Every matrix has exactly one nonzero entry in each row and column. That entry is positive or negative one.

The proof checks every matrix column, then lifts the local operator to the physical wires. Finally it checks both values of the shared outer mask.

For a circuit `U`, write its basis action as

$$
U|x\rangle=(-1)^{s_U(x)}|\pi_U(x)\rangle,
\qquad 0\le x<2^k.
$$

Here `pi_U` is a permutation and `s_U` is a bit. Encode each active column as

$$
B_U[x]=2\pi_U(x)+s_U(x).
$$

The value is at most 31 for four qubits, so one byte suffices. Store `2^k` active entries in a 16-byte array. Zero every unused entry. Keep separate catalogs for each `k`, or include `k` in the key.

Equal signatures give equal images and signs for every basis vector. For any complex coefficients `alpha_x`,

$$
\begin{aligned}
U\sum_x\alpha_x|x\rangle
&=\sum_x\alpha_x(-1)^{s_U(x)}|\pi_U(x)\rangle\\
&=\sum_x\alpha_x(-1)^{s_V(x)}|\pi_V(x)\rangle\\
&=V\sum_x\alpha_x|x\rangle.
\end{aligned}
$$

Thus

$$
\boxed{B_U=B_V\Longrightarrow U=V.}
$$

This compares matrices exactly. It does not identify matrices up to a global sign. `Neg` flips the sign bit of every active column, so its signature differs from the identity.

Composition must evaluate the next gate at the current output basis. If `V` executes after `U`, then

$$
\begin{aligned}
\pi_{VU}(x)&=\pi_V(\pi_U(x)),\\
s_{VU}(x)&=s_U(x)\oplus s_V(\pi_U(x)).
\end{aligned}
$$

Decoding and XORing both signs is essential. Applying the next sign to the original input, or overwriting the accumulated sign, gives wrong results.

Let `W` reorder all physical qubits into the selected sorted wires followed by their sorted complement. The lifted operators are

$$
U_{\rm full}=W^\dagger(U\otimes I)W,
\qquad V_{\rm full}=W^\dagger(V\otimes I)W.
$$

Local equality gives full-space equality. No selected qubit needs to start at zero. The identity holds on dirty temporary states and states entangled with unselected qubits or an external reference.

Every operation in the window has the same outer mask `g`. The window changes neither classical bits nor the stack. When `g=0`, each complete sequence skips and acts as the identity. When `g=1`, the two complete operators agree. Consequently, the replacement preserves the entire quantum instrument of its containing circuit.

## Construction and lifting requirements

- Enumerate the empty sequence and every valid sequence of length one through three with at most one `CCX` or `CCZ`. Include the zero-Toffoli sequences of the listed gate alphabet.
- Compare all 16 signature bytes using exact key equality. Hash collisions must trigger full equality checks. No numerical tolerance enters catalog lookup.
- Minimize `(Toffoli count, gate count)` lexicographically. Use a fixed gate order and sequence order. Retain the existing entry on an exact cost tie. Hash-map iteration order must not decide the winner.
- Build a source window's signature by the same declared bit and time conventions. Require two through five adjacent eligible operations, at least two Toffolis, and at most four distinct physical wires.
- Treat every reset, measurement, register operation, classical operation, stack operation, debug entry, and explicit classical condition as a window barrier.
- Lift each role through the same sorted physical list. Preserve target and control roles. Initialize emitted operations with `Op::empty`, set valid operands, and retain sentinel values in unused fields.
- Check emitted operations with `Op::validate`. Use only physical wires from the original window. Preserve all operations outside the window and all register metadata.

The word Clifford here refers to the listed subset `X,Z,CX,CZ,Swap,Neg`. A single sign bit cannot encode `S`, and this representation cannot encode `H`. Neither gate belongs in this catalog.

## Costs, termination, and sanity checks

If a replacement removes `d>0` static Toffolis, it removes `d*g` executed Toffolis on a shot. It adds no new physical ID. Therefore `Q` cannot increase, and executed `T` cannot increase under identical inputs and reset streams. A strict executed reduction requires a replaced window to execute with positive probability.

Every accepted replacement strictly decreases a nonnegative integer: the circuit's static Toffoli count. This proves termination even if a replacement increases total gate count. A scan must handle empty replacements and overlapping windows correctly.

Decoded output indices must form a permutation. Values must fit the active dimension. Padding must remain zero. These checks reject corrupted signatures, although correctness still requires matching the challenge's gate semantics.

For `k=2`, valid Toffoli gates do not exist. Its catalog contains only the listed Clifford subset. A valid source window with at least two Toffolis necessarily uses three or four wires.

Dropping an unused local role from the emitted sequence is safe when the full k-wire signatures agree. That role then has the same identity action in both sequences.

## Numerical verification

An independent PyTorch reference enumerates every allowed sequence using canonical symmetric operands. It compares each composed signature against its complete complex matrix. The expected sequence counts are 820 for two qubits, 11,728 for three, and 90,404 for four. It also checks generator unitarity, identity's minimum cost, and global-sign distinction.

All 102,952 sequence checks passed with zero matrix error. The catalogs contained 187, 3,526, and 28,250 distinct signatures, respectively. Four lower-Toffoli fixtures also matched exactly after lifting to noncontiguous wires of a six-qubit system. These compare complete 64×64 matrices, including every spectator state.

The following compact check reproduces the two-qubit composition and phase tests. Every matrix comparison is exact.

```python
import torch

D = torch.complex128
eye = torch.eye(4, dtype=D)
x = torch.tensor([[0, 1], [1, 0]], dtype=D)
z = torch.diag(torch.tensor([1, -1], dtype=D))
i = torch.eye(2, dtype=D)
gates = [torch.kron(x, i), torch.kron(i, x),
         torch.kron(z, i), torch.kron(i, z),
         eye[:, [0, 1, 3, 2]], eye[:, [0, 3, 2, 1]],
         torch.diag(torch.tensor([1, 1, 1, -1], dtype=D)),
         eye[:, [0, 2, 1, 3]], -eye]

def encode(u):
    p = u.abs().argmax(dim=0)
    a = u[p, torch.arange(4)]
    assert bool(((a == 1) | (a == -1)).all())
    assert sorted(p.tolist()) == list(range(4))
    out = torch.zeros_like(u)
    out[p, torch.arange(4)] = a
    assert torch.equal(out, u)
    return bytes((2*p + (a.real < 0)).tolist() + [0]*12)

def compose(first, second):
    out = bytearray(16)
    for x in range(4):
        a = first[x]
        b = second[a >> 1]
        out[x] = (b & 254) | ((a ^ b) & 1)
    return bytes(out)

gs = [encode(g) for g in gates]
for g in gates:
    assert torch.equal(g.conj().T @ g, eye)
assert encode(-eye) != encode(eye)
assert encode(gates[0] @ gates[2]) != encode(gates[2] @ gates[0])
checks = [0]

def visit(u, sig, depth):
    checks[0] += 1
    assert u.shape == (4, 4) and encode(u) == sig
    if depth == 3:
        return
    for g, s in zip(gates, gs):
        visit(g @ u, compose(sig, s), depth + 1)

visit(eye, encode(eye), 0)
assert checks[0] == 820
print(checks[0])
```

## Required implementation tests and empirical boundary

The native tests should compare every generated sequence's signature with an independent exact basis interpreter. Check all columns and signs. Include `XZ` versus `ZX`, `Neg`, the empty identity, and the three verified factor kernels.

Test sorted role lifting on noncontiguous IDs and every operand orientation. Compare the full operator with arbitrary spectator states. Test both outer-mask values and mixed 64-shot masks. Reject every barrier and explicit condition in either part of a potential window.

Require deterministic catalogs and output streams across repeated runs. Record each replacement's source window, sorted wires, signature, old/new costs, and emitted sequence. Confirm the final static count equals the sum of recorded savings.

Use the same saved development inputs and reset streams when comparing candidates with raw baselines. Preserve output, phase, cleanup flags, and every failure component. Change only the executed Toffoli count according to each active replacement. A changed Fiat–Shamir packet cannot replace this paired check.

A no-match result concerns this catalog, these window bounds, and the examined streams. It proves no global optimum and no impossibility for longer exact sequences. The fastest-three hypothesis remains untested by this review. The broader frozen 49-source experiment remains unchanged.
