use serde_json::json;
use std::path::{Path, PathBuf};
fn run() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--help"] {
        println!("circuit-transform --width 16|256 --ops PATH --out PATH --passes exact-cancel|local-catalog\nWrites a standard operation artifact and OUT.json with an exact local cancellation certificate.");
        return Ok(());
    }
    let mut options = std::collections::BTreeMap::new();
    let mut it = args.iter();
    while let Some(k) = it.next() {
        if !["--width", "--ops", "--out", "--passes"].contains(&k.as_str()) {
            return Err(format!("unknown option {k}"));
        }
        let v = it.next().ok_or_else(|| format!("missing {k}"))?;
        if options.insert(k.as_str(), v.as_str()).is_some() {
            return Err(format!("duplicate {k}"));
        }
    }
    let get = |k| {
        options
            .get(k)
            .copied()
            .ok_or_else(|| format!("{k} is required"))
    };
    let pass = get("--passes")?;
    if !["exact-cancel", "local-catalog"].contains(&pass) {
        return Err("passes must be exact-cancel or local-catalog".into());
    }
    let width = get("--width")?
        .parse::<usize>()
        .map_err(|_| "invalid width")?;
    let input = Path::new(get("--ops")?);
    let out = Path::new(get("--out")?);
    let receipt = PathBuf::from(format!("{}.json", out.display()));
    if out.exists() || receipt.exists() {
        return Err("output path already exists".into());
    }
    let bytes = std::fs::read(input).map_err(|e| e.to_string())?;
    let input_sha = lco_native::sha256(&bytes);
    let ops = study_check::parse_ops_with_width(&bytes, width)?;
    drop(bytes);
    let (ops, proof) = if pass == "exact-cancel" {
        lco_native::exact_cancel(ops)
    } else { lco_native::apply_local_catalog(ops) };
    lco_native::write_ops(&ops, out)?;
    let value = json!({"schema":"lco-exact-transform-v1","input":input,"input_ops_sha256":input_sha,"ops":out,"ops_sha256":lco_native::file_sha256(out)?,"field_width":width,"adjacent_cancel_pairs":proof["removed_pairs"],"pass":pass,"certificate":proof,"adjacent_cancel_certificate":if pass == "exact-cancel" { proof.clone() } else { serde_json::Value::Null },"local_catalog_certificate":if pass == "local-catalog" { proof.clone() } else { serde_json::Value::Null }});
    study_check::atomic_json(&receipt, &value).map_err(|e| e.to_string())?;
    println!("{value}");
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("circuit-transform: {e}");
        std::process::exit(2)
    }
}
