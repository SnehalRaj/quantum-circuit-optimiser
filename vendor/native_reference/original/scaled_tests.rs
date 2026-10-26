use super::*;

static PROFILE_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

struct ProfileGuard {
    _lock: std::sync::MutexGuard<'static, ()>,
    old_width: usize,
}

impl ProfileGuard {
    fn new() -> Self {
        let lock = PROFILE_TEST_LOCK.lock().unwrap_or_else(|error| error.into_inner());
        let old_width = SCALED_WIDTH.swap(0, std::sync::atomic::Ordering::Relaxed);
        Self { _lock: lock, old_width }
    }
}

impl Drop for ProfileGuard {
    fn drop(&mut self) {
        SCALED_WIDTH.store(self.old_width, std::sync::atomic::Ordering::Relaxed);
    }
}

#[test]
fn scaled_profile_selects_the_field_width() {
    let _guard = ProfileGuard::new();
    configure_scaled_profile(16).unwrap();
    assert_eq!(field_width(), 16);
    configure_scaled_profile(256).unwrap();
    assert_eq!(field_width(), 256);
    assert!(configure_scaled_profile(32).is_err());
    for width in [16, 256] {
        configure_scaled_profile(width).unwrap();
        assert_eq!(r_small_threshold(), width);
        for pair in [KalPair::Default, KalPair::Pair1, KalPair::Pair2] {
            let caps = bulk_prefix_caps(pair);
            assert_eq!((caps.forward, caps.backward), (width, width));
        }
    }
}

fn check_primitive(
    width: usize,
    inputs: &[(U256, U256)],
    emit: impl FnOnce(&mut B, &[QubitId], &[QubitId], U256),
    oracle: impl Fn(U256, U256, U256) -> U256,
) {
    configure_scaled_profile(width).unwrap();
    let p = if width == 16 { U256::from(65419u64) } else { SECP256K1_P };
    let mut b = B::new();
    let acc = b.alloc_qubits(width);
    let arg = b.alloc_qubits(width);
    emit(&mut b, &acc, &arg, p);
    for tag in [0u64, 0x9a81] {
        let mut reader = alt_seed_xof(&b.ops, tag);
        let mut sim = Simulator::new(b.next_qubit as usize, b.next_bit as usize, &mut reader);
        for (lane, &(a, x)) in inputs.iter().enumerate() {
            for j in 0..width {
                if bit(a, j) { sim.qubits[acc[j].0 as usize] |= 1u64 << lane; }
                if bit(x, j) { sim.qubits[arg[j].0 as usize] |= 1u64 << lane; }
            }
        }
        sim.apply_iter(b.ops.iter());
        let mask = if inputs.len() == 64 { u64::MAX } else { (1u64 << inputs.len()) - 1 };
        assert_eq!(sim.phase & mask, 0, "phase, width={width}, tag={tag}");
        for (lane, &(a, x)) in inputs.iter().enumerate() {
            let mut actual = U256::ZERO;
            let mut preserved = U256::ZERO;
            for j in 0..width {
                if sim.qubits[acc[j].0 as usize] >> lane & 1 != 0 { actual |= U256::from(1u64) << j; }
                if sim.qubits[arg[j].0 as usize] >> lane & 1 != 0 { preserved |= U256::from(1u64) << j; }
            }
            assert_eq!(actual, oracle(a, x, p), "coordinate, width={width}, lane={lane}, tag={tag}");
            assert_eq!(preserved, x, "argument, width={width}, lane={lane}");
        }
        for q in 2 * width..sim.qubits.len() {
            assert_eq!(sim.qubits[q] & mask, 0, "scratch, width={width}, q={q}");
        }
    }
}

#[test]
fn scaled_double_and_halve_cover_the_modulus_gap() {
    let _guard = ProfileGuard::new();
    for width in [16, 256] {
        let p = if width == 16 { U256::from(65419u64) } else { SECP256K1_P };
        let half = p >> 1;
        let values = [U256::ZERO, U256::from(1), U256::from(115), U256::from(117),
            half, half + U256::from(1), (U256::from(1) << (width - 1)) - U256::from(1), p - U256::from(1)];
        let inputs: Vec<_> = values.iter().map(|&x| (x, U256::ZERO)).collect();
        check_primitive(width, &inputs, |b, acc, _, p| mod_double_inplace_fast(b, acc, p),
            |a, _, p| a.mul_mod(U256::from(2), p));
        check_primitive(width, &inputs, |b, acc, _, p| mod_halve_inplace_fast(b, acc, p),
            |a, _, p| a.mul_mod((p >> 1) + U256::from(1), p));
    }
}

#[test]
fn scaled_add_and_subtract_cover_overflow() {
    let _guard = ProfileGuard::new();
    for width in [16, 256] {
        let p = if width == 16 { U256::from(65419u64) } else { SECP256K1_P };
        let inputs = [(U256::ZERO, U256::ZERO), (p - U256::from(1), U256::from(1)),
            (p - U256::from(1), p - U256::from(1)), (U256::ZERO, p - U256::from(1)),
            (U256::from(17), U256::from(29))];
        check_primitive(width, &inputs, |b, acc, arg, p| mod_add_qq_fast(b, acc, arg, p),
            |a, x, p| a.add_mod(x, p));
        check_primitive(width, &inputs, |b, acc, arg, p| mod_sub_qq_fast(b, acc, arg, p),
            |a, x, p| if a >= x { a - x } else { p - (x - a) });
    }
}

#[test]
fn scaled_product_reduces_raw_words() {
    let _guard = ProfileGuard::new();
    for width in [16, 256] {
        let p = if width == 16 { U256::from(65419u64) } else { SECP256K1_P };
        let inputs = [(p - U256::from(1), p - U256::from(1)),
            (U256::from(257), U256::from(255)), (U256::from(17), U256::from(29))];
        check_primitive(width, &inputs,
            |b, acc, arg, p| squaring_add_to_acc_schoolbook(b, acc, arg, p),
            |a, x, p| a.add_mod(x.mul_mod(x, p), p));
    }
}

#[test]
fn scaled_reducer_normalizes_the_raw_low_word() {
    let _guard = ProfileGuard::new();
    for width in [16, 256] {
        let p = if width == 16 { U256::from(65419u64) } else { SECP256K1_P };
        let mask = if width == 256 { U256::MAX } else { (U256::from(1) << width) - U256::from(1) };
        let inputs = [(U256::ZERO, mask), (p - U256::from(1), p), (U256::from(17), p + U256::from(1))];
        check_primitive(width, &inputs, |b, acc, arg, p| {
            let product = b.alloc_qubits(2 * width);
            for j in 0..width { b.cx(arg[j], product[j]); }
            scaled_reduce_product(b, acc, &product, p, false);
            for j in 0..width { b.cx(arg[j], product[j]); }
            b.free_vec(&product);
        }, |a, x, p| a.add_mod(x % p, p));
    }
}

#[test]
fn legacy_256_primitive_stream_matches_the_original_source() {
    let _guard = ProfileGuard::new();
    let mut b = B::new();
    let x = b.alloc_qubits(N);
    let y = b.alloc_qubits(N);
    let z = b.alloc_qubits(N);
    mod_add_qq(&mut b, &x, &y, SECP256K1_P);
    mod_add_qq_fast(&mut b, &x, &y, SECP256K1_P);
    mod_sub_qq_fast(&mut b, &x, &y, SECP256K1_P);
    mod_double_inplace_fast(&mut b, &x, SECP256K1_P);
    mod_halve_inplace_fast(&mut b, &x, SECP256K1_P);
    mod_mul_write_into_zero_acc_schoolbook(&mut b, &z, &x, &y, SECP256K1_P);
    let mut digest = [0u8; 32];
    alt_seed_xof(&b.ops, 0).read(&mut digest);
    let hash: String = digest.iter().map(|v| format!("{v:02x}")).collect();
    assert_eq!(b.ops.len(), 1_798_247);
    assert_eq!(hash, "44f9e2a47b6d13f5e52a0cce63e8dfaedea242f52cf4d120f7da5280f728bcc0");
}

#[test]
fn small_field_classical_kaliski_bounds_cover_every_denominator() {
    let p = 65419u64;
    let n = 16;
    let mut first_terminal = 32;
    let mut last_terminal = 0;
    let mut max_s_add = 0;
    for x in 1..p {
        let (mut u, mut v, mut r, mut s, mut f) = (p, x, 0u64, 1u64, true);
        for i in 0..2 * n {
            let mut m = f && v == 0;
            if m {
                first_terminal = first_terminal.min(i);
                last_terminal = last_terminal.max(i);
            }
            f ^= m;
            let mut a = f && u & 1 == 0;
            m ^= f && u & 1 != 0 && v & 1 == 0;
            let b = a ^ m;
            let t = f && u > v && !b;
            a ^= t;
            if a { std::mem::swap(&mut u, &mut v); std::mem::swap(&mut r, &mut s); }
            if f && !b {
                v -= u;
                max_s_add = max_s_add.max(s + r);
                s = (s + r) & 65535;
            }
            if i < n {
                assert_eq!(r >> (n - 1), 0, "x={x}, iteration={i}");
                assert!(2 * r < p, "modular correction required: x={x}, iteration={i}, r={r}");
            }
            v >>= 1;
            r = 2 * r % p;
            if a { std::mem::swap(&mut u, &mut v); std::mem::swap(&mut r, &mut s); }
        }
        assert_eq!((u, v, s, f), (1, 0, p, false), "x={x}");
        assert_eq!(r * x % p, p - ((1u64 << 32) % p), "x={x}");
    }
    assert_eq!((first_terminal, last_terminal, max_s_add), (16, 31, 65419));
}

#[test]
fn large_field_classical_kaliski_bounds_cover_a_fixed_corpus() {
    let p = SECP256K1_P;
    let one = U256::from(1u64);
    let mut cases = vec![one, U256::from(2), U256::from(3), p - one,
        p - U256::from(2), p >> 1, (p >> 1) + one];
    for i in 0..256 {
        let power = one << i;
        cases.push(power);
        cases.push(p - power);
    }
    let mut hasher = Shake256::default();
    hasher.update(b"scaled-kaliski-bounds-large-v1");
    let mut reader = hasher.finalize_xof();
    while cases.len() < 2567 {
        let mut bytes = [0u8; 32];
        reader.read(&mut bytes);
        let x = U256::from_limbs(std::array::from_fn(|i| {
            u64::from_le_bytes(bytes[8 * i..8 * (i + 1)].try_into().unwrap())
        }));
        if x != U256::ZERO && x < p { cases.push(x); }
    }
    let gap = U256::from(4294968273u64);
    let negative_scale = p - gap.mul_mod(gap, p);
    for x in cases {
        let (mut u, mut v, mut r, mut s, mut f) = (p, x, U256::ZERO, one, true);
        for i in 0..512 {
            let mut m = f && v == U256::ZERO;
            if m { assert!(i >= 256, "early terminal entry: x={x}, iteration={i}"); }
            f ^= m;
            let mut a = f && !bit(u, 0);
            m ^= f && bit(u, 0) && !bit(v, 0);
            let b = a ^ m;
            a ^= f && u > v && !b;
            if a { std::mem::swap(&mut u, &mut v); std::mem::swap(&mut r, &mut s); }
            if f && !b {
                v -= u;
                let (sum, overflow) = s.overflowing_add(r);
                assert!(!overflow && sum <= p, "sentinel overflow: x={x}, iteration={i}");
                s = sum;
            }
            if i < 256 {
                assert!(r <= p >> 1, "modular correction required: x={x}, iteration={i}, r={r}");
            }
            v >>= 1;
            r = r.add_mod(r, p);
            if a { std::mem::swap(&mut u, &mut v); std::mem::swap(&mut r, &mut s); }
        }
        assert_eq!((u, v, s, f), (one, U256::ZERO, p, false), "x={x}");
        assert_eq!(r.mul_mod(x, p), negative_scale, "x={x}");
    }
}
