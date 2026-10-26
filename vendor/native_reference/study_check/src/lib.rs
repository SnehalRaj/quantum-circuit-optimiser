use alloy_primitives::U256;
use quantum_ecc::circuit::{
    analyze_ops, BitId, Op, OperationType, QubitId, QubitOrBit, RegisterId, NO_BIT, NO_QUBIT,
    NO_REG,
};
use quantum_ecc::sim::Simulator;
use quantum_ecc::weierstrass_elliptic_curve::WeierstrassEllipticCurve;
use serde_json::{json, Value};
use sha3::digest::{ExtendableOutput, Update, XofReader};
use sha3::Shake256;
use std::collections::BTreeSet;
use std::io::Read;

pub const PROTOCOL: &str = "controlled-study-exact-scalars-v1";
pub fn secp256k1() -> WeierstrassEllipticCurve {
    let h = |s| U256::from_str_radix(s, 16).unwrap();
    WeierstrassEllipticCurve {
        a: U256::ZERO,
        b: U256::from(7),
        modulus: h("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F"),
        order: h("FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141"),
        gx: h("79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798"),
        gy: h("483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8"),
    }
}
pub fn stream(purpose: &[u8], parts: &[&[u8]]) -> sha3::Shake256Reader {
    let mut h = Shake256::default();
    h.update(PROTOCOL.as_bytes());
    h.update(&(purpose.len() as u64).to_le_bytes());
    h.update(purpose);
    for p in parts {
        h.update(&(p.len() as u64).to_le_bytes());
        h.update(p);
    }
    h.finalize_xof()
}
pub fn sample_scalar(mut draw: impl FnMut() -> U256, n: U256) -> (U256, usize) {
    let mut rejected = 0;
    loop {
        let k = draw();
        if k > U256::ZERO && k < n {
            return (k, rejected);
        }
        rejected += 1;
    }
}
pub fn domain(
    c: &WeierstrassEllipticCurve,
    t: (U256, U256),
    p: (U256, U256),
) -> Result<(U256, U256), &'static str> {
    if t == (U256::ZERO, U256::ZERO) || p == (U256::ZERO, U256::ZERO) {
        return Err("identity");
    }
    if !c.is_on_curve(t.0, t.1) || !c.is_on_curve(p.0, p.1) {
        return Err("off_curve");
    }
    if t.0 == p.0 {
        return Err("equal_x");
    }
    let e = c.add(t.0, t.1, p.0, p.1);
    if e == (U256::ZERO, U256::ZERO) {
        return Err("sum_identity");
    }
    if e.0 == p.0 {
        return Err("sum_x_equals_addend_x");
    }
    Ok(e)
}
#[derive(Clone)]
pub struct Pair {
    pub t: (U256, U256),
    pub o: (U256, U256),
    pub e: (U256, U256),
    pub scalars: (U256, U256),
    pub scalar_rejections: usize,
    pub exclusions: std::collections::BTreeMap<String, usize>,
}
pub fn make_pair(c: &WeierstrassEllipticCurve, label: &str, i: usize) -> Pair {
    let mut xof = stream(b"inputs", &[label.as_bytes(), &(i as u64).to_le_bytes()]);
    let mut exclusions = std::collections::BTreeMap::new();
    let mut scalar_rejections = 0;
    loop {
        let mut draw = || {
            let mut b = [0; 32];
            XofReader::read(&mut xof, &mut b);
            U256::from_le_bytes(b)
        };
        let (k1, r1) = sample_scalar(&mut draw, c.order);
        let (k2, r2) = sample_scalar(&mut draw, c.order);
        scalar_rejections += r1 + r2;
        let t = c.mul(c.gx, c.gy, k1);
        let o = c.mul(c.gx, c.gy, k2);
        match domain(c, t, o) {
            Ok(e) => {
                return Pair {
                    t,
                    o,
                    e,
                    scalars: (k1, k2),
                    scalar_rejections,
                    exclusions,
                }
            }
            Err(why) => *exclusions.entry(why.to_string()).or_default() += 1,
        }
    }
}
pub fn packet_row(p: &Pair, i: usize) -> Value {
    json!({"idx":i,"k_target":format!("{:#066x}",p.scalars.0),"k_addend":format!("{:#066x}",p.scalars.1),"target":[format!("{:#066x}",p.t.0),format!("{:#066x}",p.t.1)],"addend":[format!("{:#066x}",p.o.0),format!("{:#066x}",p.o.1)],"expected":[format!("{:#066x}",p.e.0),format!("{:#066x}",p.e.1)]})
}

pub fn parse_ops(bytes: &[u8]) -> Result<Vec<Op>, String> {
    parse_ops_with_width(bytes,256)
}
/// Parse the same operation format for an explicit benchmark profile.
pub fn parse_ops_with_width(bytes: &[u8], width:usize) -> Result<Vec<Op>, String> {
    if width != 16 && width != 256 { return Err("field width must be 16 or 256".into()); }
    if bytes.len() < 16 {
        return Err("truncated artifact header".into());
    }
    let n = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
    if n == 0 || n > 50_000_000 {
        return Err("operation count outside 1..50000000".into());
    }
    let len = (n as usize).checked_mul(56).ok_or("size overflow")?;
    let body = match &bytes[..8] {
        b"QECCOPS1" => bytes[16..].to_vec(),
        b"QECCOPSZ" => {
            let mut d =
                zstd::stream::read::Decoder::new(&bytes[16..]).map_err(|e| e.to_string())?;
            d.window_log_max(27).map_err(|e| e.to_string())?;
            let mut b = Vec::new();
            d.take(len as u64 + 1)
                .read_to_end(&mut b)
                .map_err(|e| e.to_string())?;
            b
        }
        _ => return Err("unknown artifact magic".into()),
    };
    if body.len() != len {
        return Err("artifact body length mismatch".into());
    }
    let kinds = [
        OperationType::Neg,
        OperationType::Register,
        OperationType::AppendToRegister,
        OperationType::BitInvert,
        OperationType::BitStore0,
        OperationType::BitStore1,
        OperationType::X,
        OperationType::Z,
        OperationType::CX,
        OperationType::CZ,
        OperationType::Swap,
        OperationType::R,
        OperationType::Hmr,
        OperationType::CCX,
        OperationType::CCZ,
        OperationType::PushCondition,
        OperationType::PopCondition,
    ];
    let mut ops = Vec::with_capacity(n as usize);
    let mut depth = 0usize;
    for (i, b) in body.chunks_exact(56).enumerate() {
        let raw = u32::from_le_bytes(b[..4].try_into().unwrap()) as usize;
        let kind = *kinds
            .get(raw)
            .ok_or_else(|| format!("operation {i}: disallowed kind {raw}"))?;
        if b[4..8] != [0; 4] {
            return Err(format!("operation {i}: nonzero padding"));
        }
        let rd = |off| u64::from_le_bytes(b[off..off + 8].try_into().unwrap());
        let op = Op {
            kind,
            q_control2: QubitId(rd(8)),
            q_control1: QubitId(rd(16)),
            q_target: QubitId(rd(24)),
            c_target: BitId(rd(32)),
            c_condition: BitId(rd(40)),
            r_target: RegisterId(rd(48)),
        };
        if [op.q_target, op.q_control1, op.q_control2]
            .iter()
            .any(|q| *q != NO_QUBIT && q.0 >= 100_000)
            || [op.c_target, op.c_condition]
                .iter()
                .any(|b| *b != NO_BIT && b.0 >= 10_000_000)
            || (op.r_target != NO_REG && op.r_target.0 >= 4)
        {
            return Err(format!("operation {i}: resource identifier out of range"));
        }
        if std::panic::catch_unwind(|| op.validate()).is_err() {
            return Err(format!("operation {i}: invalid fields"));
        }
        match kind {
            OperationType::Register | OperationType::AppendToRegister => (),
            OperationType::PushCondition => depth += 1,
            OperationType::PopCondition => {
                if depth == 0 {
                    return Err("condition stack underflow".into());
                }
                depth -= 1;
            }
            _ => (),
        }
        ops.push(op);
    }
    if depth != 0 {
        return Err("unbalanced condition stack".into());
    }
    let (_, _, _, regs) = analyze_ops(ops.iter());
    if regs.len() != 4 || regs.iter().any(|r| r.len() != width) {
        return Err(format!("expected four {width}-bit registers"));
    }
    let mut seen_q = BTreeSet::new();
    let mut seen_b = BTreeSet::new();
    for (r, reg) in regs.iter().enumerate() {
        for item in reg {
            match item {
                QubitOrBit::Qubit(q) if r < 2 => {
                    if !seen_q.insert(q.0) {
                        return Err("aliased register qubit".into());
                    }
                }
                QubitOrBit::Bit(b) if r >= 2 => {
                    if !seen_b.insert(b.0) {
                        return Err("aliased register bit".into());
                    }
                }
                _ => {
                    return Err(
                        "target registers require qubits; addend registers require classical bits"
                            .into(),
                    )
                }
            }
        }
    }
    Ok(ops)
}

pub fn simulate_batch(
    ops: &[Op],
    regs: &[Vec<QubitOrBit>],
    nq: u64,
    nb: u64,
    pairs: &[Pair],
    label: &str,
    stream_id: usize,
    start: usize,
) -> (Vec<Value>, u64, u64) {
    let batch = start / 64;
    let mut xof = stream(
        b"measurements",
        &[
            label.as_bytes(),
            &(stream_id as u64).to_le_bytes(),
            &(batch as u64).to_le_bytes(),
        ],
    );
    let mut sim = Simulator::new(nq as usize, nb as usize, &mut xof);
    for (k, p) in pairs.iter().enumerate() {
        for (r, v) in [p.t.0, p.t.1, p.o.0, p.o.1].iter().enumerate() {
            sim.set_register(&regs[r], *v, k);
        }
    }
    // A private control bit masks unused SIMD lanes. The harness always counts 64 lanes.
    let active = if pairs.len() == 64 {
        u64::MAX
    } else {
        (1u64 << pairs.len()) - 1
    };
    let control = BitId(sim.bits.len() as u64);
    sim.bits.push(active);
    sim.num_bits += 1;
    let mut push = Op::empty();
    push.kind = OperationType::PushCondition;
    push.c_condition = control;
    let mut pop = Op::empty();
    pop.kind = OperationType::PopCondition;
    sim.apply_iter(
        std::iter::once(&push)
            .chain(ops.iter())
            .chain(std::iter::once(&pop)),
    );
    let register_q: BTreeSet<u64> = regs
        .iter()
        .flatten()
        .filter_map(|r| {
            if let QubitOrBit::Qubit(q) = r {
                Some(q.0)
            } else {
                None
            }
        })
        .collect();
    let rows=pairs.iter().enumerate().map(|(k,p)|{
        let got=[sim.get_register(&regs[0],k),sim.get_register(&regs[1],k)];let addend=[sim.get_register(&regs[2],k),sim.get_register(&regs[3],k)];
        let dirty:Vec<u64>=(0..nq).filter(|q|!register_q.contains(q) && ((sim.qubit(QubitId(*q))>>k)&1)!=0).collect();
        let coord=got!=[p.e.0,p.e.1];let phase=((sim.phase>>k)&1)!=0;let add=addend!=[p.o.0,p.o.1];let work=!dirty.is_empty();
        json!({"idx":start+k,"stream":stream_id,"passed":!(coord||phase||work||add),"failures":{"coordinate":coord,"phase":phase,"workspace":work,"addend":add},"input":packet_row(p,start+k),"observed_target":got.map(|v|format!("{v:#066x}")),"observed_addend":addend.map(|v|format!("{v:#066x}")),"dirty_qubits":dirty})
    }).collect();
    (rows, sim.stats.toffoli_gates, pairs.len() as u64)
}
pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub fn atomic_json(path: &std::path::Path, value: &Value) -> std::io::Result<()> {
    let tmp = path.with_extension(format!("tmp-{}", std::process::id()));
    std::fs::write(&tmp, serde_json::to_vec_pretty(value)?)?;
    std::fs::rename(tmp, path)
}
#[cfg(test)]
mod tests {
    use super::*;
    use quantum_ecc::circuit::Circuit;
    fn fixture(extra: &str) -> (Circuit, Pair) {
        let mut text = String::new();
        for r in 0..4 {
            text += &format!("REGISTER r{r}\n");
            for j in 0..256 {
                text += &format!(
                    "APPEND_TO_REGISTER {}{} r{r}\n",
                    if r < 2 { "q" } else { "b" },
                    (r % 2) * 256 + j
                );
            }
        }
        // Declare a clean workspace qubit, then apply a chosen fault.
        text += "X q512\nX q512\n";
        text += extra;
        let c = secp256k1();
        let mut p = make_pair(&c, "fault-control", 0);
        p.e = p.t; // The control checks identity against its declared oracle.
        (Circuit::from_text(&text), p)
    }
    #[test]
    fn identity_control_passes() {
        let (c, p) = fixture("");
        let (rows, _, _) = simulate_batch(
            &c.operations,
            &c.registers,
            c.num_qubits,
            c.num_bits,
            &[p],
            "test",
            0,
            0,
        );
        assert!(rows[0]["passed"].as_bool().unwrap());
    }
    #[test]
    fn faults_reach_each_required_check() {
        for (gate, key) in [
            ("X q0\n", "coordinate"),
            ("NEG\n", "phase"),
            ("X q512\n", "workspace"),
            ("BIT_INVERT b0\n", "addend"),
        ] {
            let (c, p) = fixture(gate);
            let (rows, _, _) = simulate_batch(
                &c.operations,
                &c.registers,
                c.num_qubits,
                c.num_bits,
                &[p],
                "test",
                0,
                0,
            );
            assert!(
                rows[0]["failures"][key].as_bool().unwrap(),
                "missing {key}: {}",
                rows[0]
            );
        }
    }
    #[test]
    fn partial_batch_cost_uses_active_lanes() {
        let (c, p) = fixture("CCX q0 q1 q512\nCCX q0 q1 q512\n");
        let (_, count, shots) = simulate_batch(
            &c.operations,
            &c.registers,
            c.num_qubits,
            c.num_bits,
            &[p],
            "test",
            0,
            0,
        );
        assert_eq!((count, shots), (2, 1));
    }
    #[test]
    fn exact_scalar_rejects_zero_and_order() {
        let n = secp256k1().order;
        let mut vals = vec![U256::ZERO, n, U256::from(7)].into_iter();
        assert_eq!(sample_scalar(|| vals.next().unwrap(), n).0, U256::from(7));
    }
    #[test]
    fn domain_rejects_both_denominators() {
        let c = secp256k1();
        let p = (c.gx, c.gy);
        let twice = c.add(p.0, p.1, p.0, p.1);
        assert_eq!(domain(&c, p, p), Err("equal_x"));
        assert_eq!(
            domain(&c, (twice.0, c.modulus - twice.1), p),
            Err("sum_x_equals_addend_x")
        );
    }
    #[test]
    fn input_stream_is_stable_and_label_separated() {
        let c = secp256k1();
        assert_eq!(make_pair(&c, "one", 0).t, make_pair(&c, "one", 0).t);
        assert_ne!(make_pair(&c, "one", 0).t, make_pair(&c, "two", 0).t);
    }
    #[test]
    fn append_only_registers_match_the_real_generator() {
        let (c, _) = fixture("");
        let ops: Vec<_> = c
            .operations
            .into_iter()
            .filter(|o| o.kind != OperationType::Register)
            .collect();
        let mut bytes = b"QECCOPS1".to_vec();
        bytes.extend_from_slice(&(ops.len() as u64).to_le_bytes());
        for o in &ops {
            bytes.extend_from_slice(&(o.kind as u32).to_le_bytes());
            bytes.extend_from_slice(&[0; 4]);
            for v in [
                o.q_control2.0,
                o.q_control1.0,
                o.q_target.0,
                o.c_target.0,
                o.c_condition.0,
                o.r_target.0,
            ] {
                bytes.extend_from_slice(&v.to_le_bytes());
            }
        }
        assert!(
            parse_ops(&bytes).is_ok(),
            "pinned analyze_ops creates registers from append records"
        );
    }
    #[test]
    fn loader_rejects_truncated_and_unbalanced_artifacts() {
        assert!(parse_ops(b"bad").is_err());
        let mut data = b"QECCOPS1".to_vec();
        data.extend_from_slice(&1u64.to_le_bytes());
        data.extend_from_slice(&[0u8; 56]);
        assert!(parse_ops(&data).is_err()); // No declared registers.
    }
    #[test]
    fn scaled_loader_admits_exact_sixteen_bit_registers() {
        let mut text = String::new();
        for r in 0..4 {
            text += &format!("REGISTER r{r}\n");
            for j in 0..16 {
                text += &format!("APPEND_TO_REGISTER {}{} r{r}\n", if r < 2 { "q" } else { "b" }, (r % 2) * 16 + j);
            }
        }
        let c = Circuit::from_text(&text);
        let mut bytes = b"QECCOPS1".to_vec();
        bytes.extend_from_slice(&(c.operations.len() as u64).to_le_bytes());
        for o in &c.operations {
            bytes.extend_from_slice(&(o.kind as u32).to_le_bytes());
            bytes.extend_from_slice(&[0;4]);
            for v in [o.q_control2.0,o.q_control1.0,o.q_target.0,o.c_target.0,o.c_condition.0,o.r_target.0] { bytes.extend_from_slice(&v.to_le_bytes()); }
        }
        assert!(parse_ops_with_width(&bytes,16).is_ok());
        assert!(parse_ops_with_width(&bytes,32).is_err());
        assert!(parse_ops(&bytes).is_err());
    }
}
