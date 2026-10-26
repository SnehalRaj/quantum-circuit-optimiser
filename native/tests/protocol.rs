use alloy_primitives::U256;
use quantum_ecc::circuit::Circuit;

fn fixture(width: usize, fault: &str) -> Circuit {
    let mut text = String::new();
    for r in 0..4 {
        text += &format!("REGISTER r{r}\n");
        for j in 0..width {
            text += &format!(
                "APPEND_TO_REGISTER {}{} r{r}\n",
                if r < 2 { "q" } else { "b" },
                (r % 2) * width + j
            );
        }
    }
    text += &format!("X q{}\nX q{}\n", 2 * width, 2 * width);
    text += fault;
    Circuit::from_text(&text)
}

#[test]
fn both_widths_detect_every_fault_and_partial_simd_batches() {
    for width in [16, 256] {
        let mut pair = lco_native::make_pair(width, "native-fault-fixture", 0).unwrap();
        pair.e = pair.t;
        for (fault, key) in [
            (String::new(), ""),
            ("X q0\n".into(), "coordinate"),
            ("NEG\n".into(), "phase"),
            (format!("X q{}\n", 2 * width), "workspace"),
            ("BIT_INVERT b0\n".into(), "addend"),
        ] {
            let c = fixture(width, &fault);
            for stream in 0..2 {
                let (rows, _, _) = study_check::simulate_batch(
                    &c.operations,
                    &c.registers,
                    c.num_qubits,
                    c.num_bits,
                    &[pair.clone()],
                    "native-fault-fixture",
                    stream,
                    0,
                );
                if key.is_empty() {
                    assert_eq!(rows[0]["passed"], true);
                } else {
                    assert_eq!(rows[0]["failures"][key], true, "width={width}, fault={key}");
                }
            }
        }
        let c = fixture(
            width,
            &format!("CCX q0 q1 q{}\nCCX q0 q1 q{}\n", 2 * width, 2 * width),
        );
        for lanes in [1, 63, 64] {
            let (_, count, shots) = study_check::simulate_batch(
                &c.operations,
                &c.registers,
                c.num_qubits,
                c.num_bits,
                &vec![pair.clone(); lanes],
                "native-partial-fixture",
                0,
                0,
            );
            assert_eq!((count, shots), (2 * lanes as u64, lanes as u64));
        }
    }
}

#[test]
fn small_group_parameters_and_exact_inputs_are_checked_independently() {
    let c = lco_native::curve(16).unwrap();
    let p = c.modulus.to::<u64>();
    let prime = |n: u64| (2..).take_while(|d| d * d <= n).all(|d| n % d != 0);
    assert!(prime(p));
    let mut squares = vec![0u32; p as usize];
    for y in 0..p {
        squares[(y * y % p) as usize] += 1;
    }
    let order = 1
        + (0..p)
            .map(|x| squares[((x * x % p * x + 7) % p) as usize] as u64)
            .sum::<u64>();
    assert_eq!(order, 64921);
    assert!(prime(order));
    c.verify();
    for width in [16, 256] {
        let c = lco_native::curve(width).unwrap();
        let pair = lco_native::make_pair(width, "native-input-fixture", 0).unwrap();
        assert_eq!(study_check::domain(&c, pair.t, pair.o), Ok(pair.e));
        assert!(pair.scalars.0 > U256::ZERO && pair.scalars.0 < c.order);
        if width == 256 {
            assert_eq!(
                pair.t,
                study_check::make_pair(&c, "native-input-fixture", 0).t
            );
        }
        let g = (c.gx, c.gy);
        assert_eq!(study_check::domain(&c, g, g), Err("equal_x"));
    }
}

#[test]
fn exact_bounds_and_malformed_artifacts_keep_the_failure_criterion() {
    for (k, expected) in [
        (0, 2.995687401942795e-5),
        (1, 4.743775717386053e-5),
        (9, 0.0001570468988883919),
    ] {
        assert!((lco_native::binomial_upper(k, 100000, 0.05).unwrap() - expected).abs() < 1e-13);
    }
    assert!(lco_native::binomial_upper(2, 1, 0.05).is_err());
    assert!(lco_native::binomial_upper(0, 0, 0.05).is_err());
    assert!(study_check::parse_ops_with_width(b"bad", 16).is_err());
    for width in [16, 256] {
        let c = fixture(width, "");
        let path =
            std::env::temp_dir().join(format!("lco-parser-{}-{width}.zst", std::process::id()));
        lco_native::write_ops(&c.operations, &path).unwrap();
        let bytes = std::fs::read(&path).unwrap();
        assert_eq!(
            study_check::parse_ops_with_width(&bytes, width).unwrap(),
            c.operations
        );
        assert!(
            study_check::parse_ops_with_width(&bytes, if width == 16 { 256 } else { 16 }).is_err()
        );
        assert!(study_check::parse_ops_with_width(&bytes[..bytes.len() / 2], width).is_err());
        std::fs::remove_file(path).unwrap();
    }
}

#[test]
fn fixed_checker_uses_the_real_oracle_and_retains_every_input() {
    let dir = std::env::temp_dir().join(format!("lco-checker-cli-{}", std::process::id()));
    std::fs::create_dir(&dir).unwrap();
    let ops = dir.join("identity.zst");
    lco_native::write_ops(&fixture(16, "").operations, &ops).unwrap();
    let out = dir.join("check.json");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_circuit-check"))
        .arg("--ops")
        .arg(&ops)
        .args([
            "--width",
            "16",
            "--pairs",
            "65",
            "--label",
            "native-checker-cli",
            "--streams",
            "2",
            "--out",
        ])
        .arg(&out)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["failed_inputs"], 65);
    assert_eq!(value["failed_shots"], 130);
    assert_eq!(
        std::fs::read_to_string(out.with_extension("jsonl"))
            .unwrap()
            .lines()
            .count(),
        130
    );
    assert_eq!(value["upper_bound"], 1.0);
    assert_eq!(
        value["per_input_sha256"],
        lco_native::file_sha256(&out.with_extension("jsonl")).unwrap()
    );
    std::fs::remove_dir_all(dir).unwrap();
}
