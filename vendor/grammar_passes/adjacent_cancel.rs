//! Exact cancellation of adjacent identical unitary involutions.
//! The operation records include the explicit classical condition.
//! These gates do not change classical bits, so that condition stays fixed.
//! Registers, measurements, resets, and condition-stack operations are barriers.
use crate::circuit::{Op, OperationType as K};

/// Return the transformed operation stream and the number of removed pairs.
pub fn run(ops: Vec<Op>) -> (Vec<Op>, usize) {
    let mut result: Vec<Op> = Vec::with_capacity(ops.len());
    let mut removed_pairs = 0;
    for op in ops {
        let involution = matches!(op.kind, K::Neg | K::X | K::Z | K::CX | K::CZ | K::Swap | K::CCX | K::CCZ);
        if involution && result.last() == Some(&op) {
            result.pop();
            removed_pairs += 1;
        } else {
            result.push(op);
        }
    }
    (result, removed_pairs)
}
