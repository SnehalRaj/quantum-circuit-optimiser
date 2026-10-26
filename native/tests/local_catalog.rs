#[path = "../../vendor/frontier_rewrite/tests/catalog.rs"]
mod certificates;

#[test]
fn preserves_the_inherited_identity_tail() {
    use quantum_ecc::circuit::{Op, OperationType as K, QubitId};
    let mut x = Op::empty();
    x.kind = K::X;
    x.q_target = QubitId(3);
    let input = vec![x, x];
    let (output, receipt) = lco_native::apply_local_catalog(input.clone());
    assert_eq!(input, output);
    assert_eq!(receipt["protected_identity_tail_operations"], 2);
}

#[test]
fn wrapper_rewrites_and_preserves_value_phase_and_identity_tail() {
    use quantum_ecc::circuit::{Op, OperationType as K, QubitId};
    let mut u = Op::empty();
    u.kind = K::CCX;
    u.q_target = QubitId(2);
    u.q_control1 = QubitId(0);
    u.q_control2 = QubitId(1);
    let mut v = Op::empty();
    v.kind = K::CX;
    v.q_target = QubitId(1);
    v.q_control1 = QubitId(0);
    let mut x = Op::empty();
    x.kind = K::X;
    x.q_target = QubitId(3);
    let input = vec![u, v, u, x, x];
    let (output, receipt) = lco_native::apply_local_catalog(input.clone());
    assert_eq!(receipt["removed_nonlinear_gate_records"], 2);
    assert_eq!(receipt["activation_status"], "rewrote");
    assert_eq!(receipt["protected_identity_tail_operations"], 2);
    assert_eq!(&output[output.len() - 2..], &[x, x]);
    for bits in 0..16 {
        let simulate = |ops: &[Op]| {
            let mut rng = study_check::stream(b"catalog-wrapper", &[]);
            let mut sim = quantum_ecc::sim::Simulator::new(4, 0, &mut rng);
            for wire in 0..4 {
                sim.qubits[wire] = if bits & (1 << wire) != 0 { u64::MAX } else { 0 };
            }
            sim.apply_iter(ops.iter());
            (sim.qubits, sim.bits, sim.phase)
        };
        assert_eq!(simulate(&input), simulate(&output));
    }
}
