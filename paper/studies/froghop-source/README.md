# Sources for the Froghop description and circuit diagram

These files support the paper's Froghop subsection. They are source references, not a new circuit experiment.

The paper uses two representative parents from its fourteen frozen frontier circuits:

| Parent | Author | Commit | Variant |
|---|---|---|---|
| Q839 | gnuchev | `410bec6de09f973c4fd0a8be75f70106fe5930de` | One-hop |
| Q799 | odinfree | `92186c3796cba8c06005f1c26e467c070c4606bd` | Two-hop |

Each folder contains selected source files copied without byte changes, the original notice, and the saved submission note.
The paper's `data/source-manifest.json` records the source-file hashes.
The complete constructors and fourteen-target catalog remain in the library's `vendor/froghop/` and `data/targets/` folders.

## Claim map

| Paper statement | Source |
|---|---|
| Pack the remainder and cofactor from opposite register ends | Opening comments in both `froghop_one.rs` and `froghop_double.rs` |
| One-hop overlaps cofactor work with the next alignment | `q839/src/point_add/froghop_one.rs`, opening comments and slot order |
| Two-hop separates alignment, division, cofactor update, and return | `q799/src/point_add/froghop_double.rs`, opening comments and slot order |
| Compute the slope, update coordinates, then recover the slope for phase repair | `pointadd_one.rs` and `pointadd_double.rs`, opening arithmetic schedule and `point_add` |
| Preserve the classical addend | Both `point_add` functions declare it as classical bits and use controlled loads |
| Replace an AND erasure with measurement and a conditional CZ | `q839/src/point_add/cond_pass.rs`, option `u` and its guards |
| Absorb unread writes into phase work | The same file, option `r` and its guards |
| Keep the parent's schedule and approximation settings | The fourteen-target catalog records six `mfs` to `mfsur` edits and eight `mfsr` to `mfsur` edits |

The one-hop and two-hop constructors differ internally. The diagram shows their shared affine point-addition schedule.
Each division block includes forward and reverse traversal and its temporary workspace.
The coordinate-product block measures the old slope. The following block repairs its phase from a recomputed slope.
The schematic applies where both division denominators are nonzero and the inherited traversal completes correctly.
The fresh audits in the paper quantify failures under the sampled input distribution.

Panel (b) shows a simple AND erasure with both operand values still available.
An X-basis result m produces a phase of (-1)^(mab).
The conditional CZ contributes the same phase and cancels it.
The reset restores the measured temporary qubit to zero.
The complete compiler pass enforces additional source conditions before it applies this identity.

`data/froghop-diagram-checks.json` records a finite algebra check of the displayed affine formulas and the two measurement branches.
This check does not validate the full Froghop constructor or replace its circuit audits.
