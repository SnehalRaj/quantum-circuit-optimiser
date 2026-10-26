use std::collections::BTreeMap;
use std::path::Path;
fn run() -> Result<i32, String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args == ["--help"] {
        println!("circuit-check --width 16|256 --ops PATH --pairs N --label LABEL --out PATH [--streams 2] [--alpha 0.05] [--threads 1]\nChecks target coordinates, phase, quantum scratch and classical addend preservation.\nExit 0: no observed failures; 1: observed circuit failures; 2: invalid request or instrument failure.");
        return Ok(0);
    }
    let mut options = BTreeMap::new();
    let mut it = args.iter();
    while let Some(k) = it.next() {
        if ![
            "--width",
            "--ops",
            "--pairs",
            "--label",
            "--out",
            "--streams",
            "--alpha",
            "--threads",
        ]
        .contains(&k.as_str())
        {
            return Err(format!("unknown option {k}"));
        }
        let v = it.next().ok_or_else(|| format!("missing value for {k}"))?;
        if options.insert(k.as_str(), v.as_str()).is_some() {
            return Err(format!("duplicate {k}"));
        }
    }
    let get = |key| {
        options
            .get(key)
            .copied()
            .ok_or_else(|| format!("{key} is required"))
    };
    let width = get("--width")?
        .parse::<usize>()
        .map_err(|_| "invalid width")?;
    let pairs = get("--pairs")?
        .parse::<usize>()
        .map_err(|_| "invalid pairs")?;
    let streams = options
        .get("--streams")
        .copied()
        .unwrap_or("2")
        .parse::<usize>()
        .map_err(|_| "invalid streams")?;
    if streams != 2 {
        return Err("the fixed protocol requires exactly two streams".into());
    }
    let threads = options
        .get("--threads")
        .copied()
        .unwrap_or("1")
        .parse::<usize>()
        .map_err(|_| "invalid threads")?;
    if threads != 1 {
        return Err(
            "this checker build supports --threads 1; independent process parallelism is available"
                .into(),
        );
    }
    let alpha = options
        .get("--alpha")
        .copied()
        .unwrap_or("0.05")
        .parse::<f64>()
        .map_err(|_| "invalid alpha")?;
    let out = Path::new(get("--out")?);
    let mut result = lco_native::evaluate(
        Path::new(get("--ops")?),
        width,
        pairs,
        get("--label")?,
        out,
        alpha,
    )?;
    result["schema"] = serde_json::json!("lco-fixed-check-v1");
    result["backend"] = serde_json::Value::Null;
    result["Q"] = result["qubits"].clone();
    result["T"] = result["mean_toffoli"].clone();
    result["score"] = result["shared_rounded_score"].clone();
    result["upper_bound"] = result["binomial_upper"].clone();
    result["threads"] = serde_json::json!(threads);
    result["scope"]=serde_json::json!("Finite full-point-addition evidence under the declared fixed input and two-stream measurement protocol. Qualification requires its frozen family bound.");
    result["checker_source_sha256"] = serde_json::json!(lco_native::checker_source_sha256());
    result["checker_binary_sha256"] = serde_json::json!(lco_native::file_sha256(
        &std::env::current_exe().map_err(|e| e.to_string())?
    )?);
    study_check::atomic_json(out, &result).map_err(|e| e.to_string())?;
    println!("{result}");
    Ok(if result["failed_inputs"].as_u64().unwrap() == 0 {
        0
    } else {
        1
    })
}
fn main() {
    match run() {
        Ok(code) => std::process::exit(code),
        Err(e) => {
            eprintln!("circuit-check: {e}");
            std::process::exit(2)
        }
    }
}
