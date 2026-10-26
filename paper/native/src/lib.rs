use alloy_primitives::U256;
pub use quantum_ecc::circuit;
use quantum_ecc::circuit::{analyze_ops, Op};
use quantum_ecc::weierstrass_elliptic_curve::WeierstrassEllipticCurve;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use sha3::digest::XofReader;
#[path = "../../vendor/grammar_passes/adjacent_cancel.rs"]
pub mod adjacent_cancel;
#[path = "../../vendor/frontier_rewrite/mod.rs"]
pub mod local_catalog;
use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufWriter, Write};
use std::path::Path;
use study_check::Pair;

pub const SETTINGS: [&str; 3] = [
    "POINT_ADD_AFFINE_COMBINED_Y",
    "KAL_DIRECT_CONST_HALVE",
    "KAL_FREE_S",
];
pub const PROTOCOL: &str = "scaled-full-point-add-v1";
pub const CONDITIONAL_SHA256: &str =
    "a56f374ad86c7dd3ca13cfd08291f5f9a4f40a54e122959c481656a54b9d987e";
pub fn canonical_policy(value: &str) -> Result<String, String> {
    if value == "none" {
        return Ok(String::new());
    }
    if value.is_empty() {
        return Err("use policy none for the empty subset".into());
    }
    let mut seen = BTreeSet::new();
    for c in value.chars() {
        if !"mfsur".contains(c) || !seen.insert(c) {
            return Err("policy requires unique flags from mfsur, or none".into());
        }
    }
    Ok("mfsur".chars().filter(|c| seen.contains(c)).collect())
}
pub fn reject_compiler_environment() -> Result<(), String> {
    for (name, _) in std::env::vars_os() {
        let name = name.to_string_lossy();
        if name == "SL_COND" || name.starts_with("SL_COND_") {
            return Err(format!(
                "forbidden compiler environment control {name}; use --policy"
            ));
        }
    }
    Ok(())
}
pub fn compiler_metrics(log: &str) -> Result<Value, String> {
    let mut counts = BTreeMap::from([
        ("m", 0u64),
        ("f", 0),
        ("s", 0),
        ("u", 0),
        ("r", 0),
        ("r_ccx", 0),
    ]);
    let mut guard_counts = BTreeMap::new();
    let mut expected = None;
    for line in log.lines() {
        let words: Vec<_> = line.split_whitespace().collect();
        let count_after = |token: &str| -> Result<u64, String> {
            let i = words
                .iter()
                .position(|w| *w == token)
                .ok_or_else(|| format!("missing compiler metric {token}"))?;
            words
                .get(i + 1)
                .ok_or("truncated compiler metric")?
                .parse()
                .map_err(|_| "invalid compiler count".into())
        };
        if line.starts_with("SL_COND m:") {
            for name in ["m", "u", "f"] {
                counts.insert(name, count_after(&format!("{name}:"))?);
            }
        }
        if line.starts_with("SL_COND s:") {
            counts.insert("s", count_after("s:")?);
        }
        if line.starts_with("SL_COND r:") {
            counts.insert("r", count_after("r:")?);
            let i = words
                .iter()
                .position(|w| *w == "CCX")
                .ok_or("missing r CCX count")?;
            counts.insert(
                "r_ccx",
                words[i - 1]
                    .parse::<u64>()
                    .map_err(|_| "invalid r CCX count")?,
            );
        }
        if line.starts_with("SL_COND guards") {
            if line.contains("(OFF)") {
                return Err("conditional guards were disabled".into());
            }
            for name in ["G1", "G2", "G3", "G4"] {
                guard_counts.insert(name, count_after(name)?);
            }
        }
        if line.starts_with("SL_COND static expected Toffoli ") {
            if line.contains("FAULT") {
                return Err("conditional diagnostic fault was active".into());
            }
            expected = Some((
                words
                    .get(4)
                    .ok_or("missing baseline estimate")?
                    .parse::<f64>()
                    .map_err(|_| "invalid baseline estimate")?,
                words
                    .get(6)
                    .ok_or("missing candidate estimate")?
                    .parse::<f64>()
                    .map_err(|_| "invalid candidate estimate")?,
            ));
        }
    }
    if guard_counts.len() != 4 {
        return Err("missing guarded compiler receipt".into());
    }
    let (before, after) = expected.ok_or("missing compiler resource estimate")?;
    Ok(
        json!({"activation_counts":counts,"guard_rejections":guard_counts,"guards_active":true,"static_expected_toffoli_before":before,"static_expected_toffoli":after}),
    )
}
pub fn ir_sha256(ops: &[Op]) -> String {
    let mut digest = Sha256::new();
    digest.update(b"QECCOPS1");
    digest.update((ops.len() as u64).to_le_bytes());
    for op in ops {
        digest.update((op.kind as u32).to_le_bytes());
        digest.update([0u8; 4]);
        for v in [
            op.q_control2.0,
            op.q_control1.0,
            op.q_target.0,
            op.c_target.0,
            op.c_condition.0,
            op.r_target.0,
        ] {
            digest.update(v.to_le_bytes());
        }
    }
    study_check::hex(&digest.finalize())
}
pub fn exact_cancel(ops: Vec<Op>) -> (Vec<Op>, Value) {
    let input_ir_sha256 = ir_sha256(&ops);
    let input_operation_count = ops.len();
    let (output, count) = adjacent_cancel::run(ops);
    let proof = json!({"schema":"exact-adjacent-inverse-v1","input_ir_sha256":input_ir_sha256,"output_ir_sha256":ir_sha256(&output),"input_operation_count":input_operation_count,"output_operation_count":output.len(),"removed_pairs":count,"removed_gate_records":2*count,"guard":"Identical adjacent unitary involutions including explicit classical conditions; no classical write between the pair.","source_sha256":sha256(include_bytes!("../../vendor/grammar_passes/adjacent_cancel.rs"))});
    (output, proof)
}
pub fn apply_local_catalog(mut ops: Vec<Op>) -> (Vec<Op>, Value) {
    use quantum_ecc::circuit::{OperationType as K, NO_BIT};
    let input_ir_sha256 = ir_sha256(&ops);
    let input_operation_count = ops.len();
    let count_t = |items: &[Op]| items.iter().filter(|op| matches!(op.kind, K::CCX | K::CCZ)).count();
    let before_t = count_t(&ops);
    let mut end = ops.len();
    while end >= 2 {
        let (a, b) = (&ops[end - 2], &ops[end - 1]);
        if a.kind != K::X || b.kind != K::X || a.q_target != b.q_target
            || a.c_condition != NO_BIT || b.c_condition != NO_BIT { break; }
        end -= 2;
    }
    let tail = ops.split_off(end);
    let tail_ir_sha256 = ir_sha256(&tail);
    let tail_count = tail.len();
    let mut output = local_catalog::apply(ops);
    output.extend(tail);
    let after_t = count_t(&output);
    let certificate = json!({"schema":"local-signed-permutation-catalog-v1",
        "input_ir_sha256":input_ir_sha256,"output_ir_sha256":ir_sha256(&output),
        "input_operation_count":input_operation_count,"output_operation_count":output.len(),
        "input_nonlinear_gate_records":before_t,"output_nonlinear_gate_records":after_t,
        "removed_nonlinear_gate_records":before_t-after_t,
        "activated":input_ir_sha256 != ir_sha256(&output),
        "activation_status":if input_ir_sha256 != ir_sha256(&output) { "rewrote" } else { "no_matching_windows" },
        "protected_identity_tail_operations":tail_count,"protected_identity_tail_sha256":tail_ir_sha256,
        "catalog_sha256":sha256(include_bytes!("../../vendor/frontier_rewrite/catalog.rs")),
        "wrapper_sha256":sha256(include_bytes!("../../vendor/frontier_rewrite/mod.rs")),
        "guard":"Unconditioned local unitary blocks on at most four existing wires; measurement and condition changes are barriers.",
        "scope":"Local signed-permutation identities. Full-circuit reliability requires separate validation."});
    (output, certificate)
}
pub fn checker_source_sha256() -> String {
    let mut bytes = Vec::new();
    for source in [
        include_bytes!("lib.rs").as_slice(),
        include_bytes!("bin/circuit-check.rs").as_slice(),
        include_bytes!("../../vendor/native_reference/study_check/src/lib.rs").as_slice(),
        include_bytes!("../../vendor/native_reference/harness/src/circuit.rs").as_slice(),
        include_bytes!("../../vendor/native_reference/harness/src/sim.rs").as_slice(),
        include_bytes!("../../vendor/native_reference/harness/src/weierstrass_elliptic_curve.rs")
            .as_slice(),
        include_bytes!("../Cargo.lock").as_slice(),
    ] {
        bytes.extend_from_slice(&(source.len() as u64).to_le_bytes());
        bytes.extend_from_slice(source);
    }
    sha256(&bytes)
}

#[cfg(test)]
mod native_tests {
    use super::*;
    use quantum_ecc::circuit::Circuit;
    #[test]
    fn all_conditional_subsets_have_one_canonical_policy() {
        for mask in 0u32..32 {
            let flags: String = "mfsur"
                .chars()
                .enumerate()
                .filter(|(i, _)| mask & (1 << i) != 0)
                .map(|(_, c)| c)
                .collect();
            let reversed: String = flags.chars().rev().collect();
            assert_eq!(
                canonical_policy(if flags.is_empty() { "none" } else { &reversed }).unwrap(),
                flags
            );
        }
        for bad in ["", "mm", "mfx", "noneu"] {
            assert!(canonical_policy(bad).is_err());
        }
    }
    #[test]
    fn exact_cancellation_uses_adjacent_unitary_involutions_only() {
        let input =
            Circuit::from_text("X q0\nX q1\nX q1\nX q0\nHMR q2 b0\nHMR q2 b0\nR q3\nR q3\n")
                .operations;
        let (output, proof) = exact_cancel(input.clone());
        assert_eq!(output, input[4..]);
        assert_eq!(proof["removed_pairs"], 2);
        assert_ne!(proof["input_ir_sha256"], proof["output_ir_sha256"]);
        let barriers = Circuit::from_text("X q0\nR q0\nX q0\n").operations;
        assert_eq!(exact_cancel(barriers.clone()).0, barriers);
    }
    #[test]
    fn compiler_receipt_rejects_disabled_guards() {
        assert!(compiler_metrics("SL_COND guards (OFF): G1 0 G2 0 G3 0 G4 0\nSL_COND static expected Toffoli 1.0 -> 0.0 (-1.0)\n").is_err());
        let log="SL_COND m: 7 CCX -> CX (7.00 T); u: 3 CCX -> HMR + CZ-if (3.00 T); f: 2 ANDs as phases (2.00 T)\nSL_COND s: 0 ANDs built under their read bit (0.00 T)\nSL_COND r: 5 absorbed windows (reset or measured end), 8 CCX -> CZ-if (8.00 T)\nSL_COND guards: G1 1 G2 2 G3 3 G4 4\nSL_COND static expected Toffoli 100.0 -> 80.0 (-20.0)\n";
        let value = compiler_metrics(log).unwrap();
        assert_eq!(value["activation_counts"]["u"], 3);
        assert_eq!(value["activation_counts"]["r_ccx"], 8);
        assert_eq!(value["guard_rejections"]["G4"], 4);
    }
}
pub fn curve(width: usize) -> Result<WeierstrassEllipticCurve, String> {
    match width {
        16 => Ok(WeierstrassEllipticCurve {
            a: U256::ZERO,
            b: U256::from(7),
            modulus: U256::from(65419),
            order: U256::from(64921),
            gx: U256::from(3),
            gy: U256::from(16991),
        }),
        256 => Ok(study_check::secp256k1()),
        _ => Err("field width must be 16 or 256".into()),
    }
}
pub fn make_pair(width: usize, label: &str, i: usize) -> Result<Pair, String> {
    let c = curve(width)?;
    if width == 256 {
        return Ok(study_check::make_pair(&c, label, i));
    }
    let mut xof = study_check::stream(b"inputs", &[label.as_bytes(), &(i as u64).to_le_bytes()]);
    let mut exclusions = BTreeMap::new();
    let mut scalar_rejections = 0;
    loop {
        let mut draw = || {
            let mut b = [0; 2];
            XofReader::read(&mut xof, &mut b);
            U256::from(u16::from_le_bytes(b))
        };
        let (k1, r1) = study_check::sample_scalar(&mut draw, c.order);
        let (k2, r2) = study_check::sample_scalar(&mut draw, c.order);
        scalar_rejections += r1 + r2;
        let t = c.mul(c.gx, c.gy, k1);
        let o = c.mul(c.gx, c.gy, k2);
        match study_check::domain(&c, t, o) {
            Ok(e) => {
                return Ok(Pair {
                    t,
                    o,
                    e,
                    scalars: (k1, k2),
                    scalar_rejections,
                    exclusions,
                })
            }
            Err(why) => *exclusions.entry(why.into()).or_default() += 1,
        }
    }
}
/// One-sided Clopper-Pearson bound, found by inversion of the binomial CDF.
pub fn binomial_upper(k: usize, n: usize, alpha: f64) -> Result<f64, String> {
    if n == 0 || k > n || !(alpha > 0.0 && alpha < 1.0) {
        return Err("invalid binomial arguments".into());
    }
    if k == n {
        return Ok(1.0);
    }
    if k == 0 {
        return Ok(-(alpha.ln() / n as f64).exp_m1());
    }
    let log_choose = (1..=k)
        .map(|j| ((n - k + j) as f64 / j as f64).ln())
        .sum::<f64>();
    let logcdf = |p: f64| {
        let mut term = log_choose + k as f64 * p.ln() + (n - k) as f64 * (-p).ln_1p();
        let mut sum = term;
        for j in (1..=k).rev() {
            term += (j as f64 / (n - j + 1) as f64).ln() + (-p).ln_1p() - p.ln();
            let m = sum.max(term);
            sum = m + ((sum - m).exp() + (term - m).exp()).ln();
        }
        sum
    };
    let (mut lo, mut hi) = (0.0, 1.0);
    for _ in 0..80 {
        let mid = (lo + hi) * 0.5;
        if logcdf(mid) > alpha.ln() {
            lo = mid
        } else {
            hi = mid
        }
    }
    Ok(hi)
}
pub fn validate_settings(settings: &Value) -> Result<Vec<(String, String)>, String> {
    let object = settings.as_object().ok_or("settings must be an object")?;
    for (k, v) in object {
        if !SETTINGS.contains(&k.as_str()) || !v.is_boolean() {
            return Err(format!("invalid boolean setting {k}"));
        }
    }
    Ok(SETTINGS
        .iter()
        .map(|k| {
            (
                (*k).into(),
                if object.get(*k).and_then(Value::as_bool).unwrap_or(false) {
                    "1".into()
                } else {
                    "0".into()
                },
            )
        })
        .collect())
}
pub fn sha256(bytes: &[u8]) -> String {
    study_check::hex(&Sha256::digest(bytes))
}
pub fn file_sha256(path: &Path) -> Result<String, String> {
    Ok(sha256(&std::fs::read(path).map_err(|e| e.to_string())?))
}
pub fn write_ops(ops: &[Op], path: &Path) -> Result<(), String> {
    let tmp = path.with_extension(format!("tmp-{}", std::process::id()));
    let mut w = BufWriter::new(std::fs::File::create(&tmp).map_err(|e| e.to_string())?);
    w.write_all(b"QECCOPSZ").map_err(|e| e.to_string())?;
    w.write_all(&(ops.len() as u64).to_le_bytes())
        .map_err(|e| e.to_string())?;
    let mut enc = zstd::stream::write::Encoder::new(w, 3).map_err(|e| e.to_string())?;
    for op in ops {
        enc.write_all(&(op.kind as u32).to_le_bytes())
            .map_err(|e| e.to_string())?;
        enc.write_all(&[0; 4]).map_err(|e| e.to_string())?;
        for v in [
            op.q_control2.0,
            op.q_control1.0,
            op.q_target.0,
            op.c_target.0,
            op.c_condition.0,
            op.r_target.0,
        ] {
            enc.write_all(&v.to_le_bytes()).map_err(|e| e.to_string())?;
        }
    }
    enc.finish()
        .and_then(|mut w| w.flush())
        .map_err(|e| e.to_string())?;
    std::fs::rename(tmp, path).map_err(|e| e.to_string())
}
pub fn evaluate(
    ops_path: &Path,
    width: usize,
    n: usize,
    label: &str,
    out: &Path,
    alpha: f64,
) -> Result<Value, String> {
    curve(width)?;
    if n == 0 || n > 1_000_000 || label.is_empty() || label.len() > 1024 {
        return Err("invalid sample count or label".into());
    }
    binomial_upper(0, n, alpha)?;
    if out.exists() || out.with_extension("jsonl").exists() {
        return Err("result paths already exist; preserve consumed labels".into());
    }
    let started = std::time::Instant::now();
    let bytes = std::fs::read(ops_path).map_err(|e| e.to_string())?;
    let hash = sha256(&bytes);
    let ops = study_check::parse_ops_with_width(&bytes, width)?;
    drop(bytes);
    let (nq, nb, _, regs) = analyze_ops(ops.iter());
    let per = out.with_extension("jsonl");
    let mut writer = BufWriter::new(
        std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&per)
            .map_err(|e| e.to_string())?,
    );
    let mut packet = Sha256::new();
    let mut records = Sha256::new();
    let mut failures = BTreeSet::new();
    let mut exclusions = BTreeMap::<String, usize>::new();
    let mut scalar_rejections = 0;
    let mut total_toffoli = 0u64;
    let mut bad_rows = Vec::new();
    let mut failed_shots = 0usize;
    for start in (0..n).step_by(64) {
        let pairs: Vec<Pair> = (start..(start + 64).min(n))
            .map(|i| make_pair(width, label, i))
            .collect::<Result<_, _>>()?;
        for (j, p) in pairs.iter().enumerate() {
            packet.update(serde_json::to_vec(&study_check::packet_row(p, start + j)).unwrap());
            packet.update(b"\n");
            scalar_rejections += p.scalar_rejections;
            for (key, value) in &p.exclusions {
                *exclusions.entry(key.clone()).or_default() += value;
            }
        }
        for sid in 0..2 {
            let (rows, t, _) =
                study_check::simulate_batch(&ops, &regs, nq, nb, &pairs, label, sid, start);
            total_toffoli += t;
            for row in rows {
                if row["passed"] == false {
                    failures.insert(row["idx"].as_u64().unwrap());
                    failed_shots += 1;
                    if bad_rows.len() < 64 {
                        bad_rows.push(row.clone());
                    }
                }
                let line = serde_json::to_vec(&row).unwrap();
                writer.write_all(&line).map_err(|e| e.to_string())?;
                writer.write_all(b"\n").map_err(|e| e.to_string())?;
                records.update(&line);
                records.update(b"\n");
            }
        }
        if start % 6400 == 0 {
            eprintln!(
                "width={width} label={label} inputs={}/{n} failures={}",
                (start + 64).min(n),
                failures.len()
            );
        }
    }
    writer.flush().map_err(|e| e.to_string())?;
    let mean = total_toffoli as f64 / (n * 2) as f64;
    let upper = binomial_upper(failures.len(), n, alpha)?;
    let result = json!({"protocol":PROTOCOL,"input_protocol":study_check::PROTOCOL,"backend":"original-kaliski","field_width":width,"curve":format!("{:?}",curve(width)?),"ops":ops_path,"ops_sha256":hash,"operation_count":ops.len(),"pairs":n,"streams":2,"label":label,"input_draw_bytes":width/8,"input_byte_order":"little-endian","input_recipe":"study_check::stream(inputs,[label,u64le(index)]); reject scalar zero or >=order; reject affine-domain exclusions","measurement_recipe":"study_check::stream(measurements,[label,u64le(stream),u64le(index/64)])","packet_sha256":study_check::hex(&packet.finalize()),"per_input":per,"per_input_sha256":study_check::hex(&records.finalize()),"domain_exclusions":exclusions,"scalar_rejections":scalar_rejections,"failed_inputs":failures.len(),"failed_indices":failures,"failed_shots":failed_shots,"failure_examples":bad_rows,"all_failures_location":per,"qubits":nq,"mean_toffoli":mean,"executed_ccx_ccz":total_toffoli,"shared_rounded_score":nq*(mean.round() as u64),"epsilon":0.001,"alpha":alpha,"binomial_upper":upper,"meets_bound":upper<=0.001,"scope":"Development evidence until a frozen independent audit admits this circuit. The shared original Kaliski backend alone is scaled.","elapsed_seconds":started.elapsed().as_secs_f64()});
    study_check::atomic_json(out, &result).map_err(|e| e.to_string())?;
    Ok(result)
}
