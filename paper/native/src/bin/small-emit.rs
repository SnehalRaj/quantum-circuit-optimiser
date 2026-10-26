#![allow(dead_code)]
pub use quantum_ecc::{circuit, sim, weierstrass_elliptic_curve};
#[path = "../../../vendor/conditional/cond_pass.rs"]
mod cond_pass;
#[path = "../../../vendor/native_reference/configuration.rs"]
mod configuration;
#[path = "../../../vendor/native_reference/original/mod.rs"]
mod original;
use lco_native::{
    canonical_policy, compiler_metrics, file_sha256, reject_compiler_environment, sha256,
    write_ops, CONDITIONAL_SHA256,
};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

fn source_sha() -> String {
    let mut bytes = Vec::new();
    for source in [
        include_bytes!("small-emit.rs").as_slice(),
        include_bytes!("../lib.rs").as_slice(),
        include_bytes!("../../../vendor/native_reference/original/mod.rs").as_slice(),
        include_bytes!("../../../vendor/native_reference/original/fermat_inv.rs").as_slice(),
        include_bytes!("../../../vendor/native_reference/original/venting.rs").as_slice(),
        include_bytes!("../../../vendor/native_reference/configuration.rs").as_slice(),
        include_bytes!("../../../vendor/conditional/cond_pass.rs").as_slice(),
        include_bytes!("../../../vendor/grammar_passes/adjacent_cancel.rs").as_slice(),
        include_bytes!("../../Cargo.lock").as_slice(),
        include_bytes!("../../../vendor/frontier_rewrite/mod.rs").as_slice(),
        include_bytes!("../../../vendor/frontier_rewrite/catalog.rs").as_slice(),
    ] {
        bytes.extend_from_slice(&(source.len() as u64).to_le_bytes());
        bytes.extend_from_slice(source);
    }
    sha256(&bytes)
}
fn appended(path: &Path, suffix: &str) -> PathBuf {
    let mut s = path.as_os_str().to_os_string();
    s.push(suffix);
    PathBuf::from(s)
}
fn boolean_option(name: &str, value: &str) -> Result<bool, String> {
    match value {
        "true" => Ok(true),
        "false" => Ok(false),
        _ => Err(format!("{name} requires true or false")),
    }
}
fn run() -> Result<(), String> {
    if sha256(include_bytes!("../../../vendor/conditional/cond_pass.rs")) != CONDITIONAL_SHA256 {
        return Err("pinned conditional compiler source has changed".into());
    }
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args == ["--help"] {
        println!("small-emit --out PATH [--policy mfs] [--parent-policy POLICY] [--free-s true|false] [--combined-y true|false] [--cancel-adjacent] [--local-catalog] [--receipt PATH]\nPolicy: any unique subset of m,f,s,u,r; none selects no conditional rewrite.\nUse --parent-policy mfs for the fixed constructor's inherited compiler context.\nWithout parent context, every requested conditional flag must activate. Inherited inactive flags remain explicit.\n--free-s and --combined-y select inherited arithmetic settings; both default to false.\n--cancel-adjacent takes no value and enables exact adjacent gate cancellation.\nThe guarded compiler runs in a clean child process. Outputs include OUT.json and OUT.compiler.log.\nsmall-emit --build-info");
        return Ok(());
    }
    if args == ["--build-info"] {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        println!(
            "{}",
            json!({"small_emit":exe,"circuit_check":exe.with_file_name("circuit-check"),"circuit_transform":exe.with_file_name("circuit-transform"),"source_sha256":source_sha(),"checker_source_sha256":lco_native::checker_source_sha256(),"conditional_source_sha256":CONDITIONAL_SHA256,"adjacent_cancel_source_sha256":sha256(include_bytes!("../../../vendor/grammar_passes/adjacent_cancel.rs")),"field_width":16,"checker_threads":[1]})
        );
        return Ok(());
    }
    reject_compiler_environment()?;
    let mut out = None;
    let mut receipt = None;
    let mut policy = None;
    let mut parent_policy = None;
    let mut free_s = None;
    let mut combined_y = None;
    let mut cancel_adjacent = false;
    let mut local_catalog = false;
    let mut worker = false;
    let mut i = 0;
    while i < args.len() {
        let k = &args[i];
        if k == "--internal-worker" && !worker {
            worker = true;
            i += 1;
            continue;
        }
        if k == "--local-catalog" && !local_catalog {
            local_catalog = true;
            i += 1;
            continue;
        }
        if k == "--cancel-adjacent" && !cancel_adjacent {
            cancel_adjacent = true;
            i += 1;
            continue;
        }
        let v = args
            .get(i + 1)
            .ok_or_else(|| format!("missing value for {k}"))?;
        match k.as_str() {
            "--out" if out.is_none() => out = Some(PathBuf::from(v)),
            "--receipt" if receipt.is_none() => receipt = Some(PathBuf::from(v)),
            "--policy" if policy.is_none() => policy = Some(canonical_policy(v)?),
            "--parent-policy" if parent_policy.is_none() => {
                parent_policy = Some(canonical_policy(v)?)
            }
            "--free-s" if free_s.is_none() => free_s = Some(boolean_option(k, v)?),
            "--combined-y" if combined_y.is_none() => combined_y = Some(boolean_option(k, v)?),
            _ => return Err(format!("unknown or duplicate option {k}")),
        };
        i += 2;
    }
    let out = out.ok_or("--out is required")?;
    let policy = policy.unwrap_or_else(|| "mfs".into());
    let free_s = free_s.unwrap_or(false);
    let combined_y = combined_y.unwrap_or(false);
    let receipt = receipt.unwrap_or_else(|| appended(&out, ".json"));
    let log_path = appended(&out, ".compiler.log");
    if out == receipt || out == log_path || receipt == log_path {
        return Err("artifact, receipt and log paths must differ".into());
    }
    if out.exists() || (!worker && (receipt.exists() || log_path.exists())) {
        return Err("output path already exists".into());
    }
    if worker {
        original::configure_scaled_profile(16)?;
        configuration::insert("POINT_ADD_AFFINE_COMBINED_Y", if combined_y { "1" } else { "0" });
        configuration::insert("KAL_DIRECT_CONST_HALVE", "0");
        configuration::insert("KAL_FREE_S", if free_s { "1" } else { "0" });
        let ops = original::build();
        let input_operation_count = ops.len();
        let ops = cond_pass::run_with(ops, &policy);
        let (ops, catalog_proof) = if local_catalog {
            lco_native::apply_local_catalog(ops)
        } else { (ops, Value::Null) };
        let (ops, cancel_proof) = if cancel_adjacent {
            let (ops, proof) = lco_native::exact_cancel(ops);
            (ops, proof)
        } else {
            (ops, Value::Null)
        };
        let (q, b, _, regs) = circuit::analyze_ops(ops.iter());
        if regs.len() != 4 || regs.iter().any(|r| r.len() != 16) {
            return Err("constructor emitted an invalid point-addition interface".into());
        }
        write_ops(&ops, &out)?;
        println!(
            "{}",
            json!({"input_operation_count":input_operation_count,"operation_count":ops.len(),"qubits":q,"Q":q,"bits":b,"gate_records_ccx_ccz":ops.iter().filter(|o|matches!(o.kind,circuit::OperationType::CCX|circuit::OperationType::CCZ)).count(),"adjacent_cancel_pairs":cancel_proof["removed_pairs"].as_u64().unwrap_or(0),"adjacent_cancel_certificate":cancel_proof,"local_catalog_certificate":catalog_proof})
        );
        return Ok(());
    }
    let mut child = std::process::Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
    child
        .env_clear()
        .env("LC_ALL", "C")
        .arg("--internal-worker")
        .arg("--out")
        .arg(&out)
        .arg("--policy")
        .arg(if policy.is_empty() { "none" } else { &policy })
        .arg("--free-s")
        .arg(if free_s { "true" } else { "false" })
        .arg("--combined-y")
        .arg(if combined_y { "true" } else { "false" });
    if cancel_adjacent {
        child.arg("--cancel-adjacent");
    }
    if local_catalog { child.arg("--local-catalog"); }
    let output = child.output().map_err(|e| e.to_string())?;
    std::fs::write(&log_path, &output.stderr).map_err(|e| e.to_string())?;
    eprint!("{}", String::from_utf8_lossy(&output.stderr));
    if !output.status.success() {
        return Err(format!(
            "constructor/compiler child failed with {}",
            output.status
        ));
    }
    let mut value: Value = serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("invalid child metadata: {e}"))?;
    let metrics = compiler_metrics(&String::from_utf8_lossy(&output.stderr))?;
    for (k, v) in metrics.as_object().unwrap() {
        value[k] = v.clone();
    }
    let inactive: Vec<_> = policy
        .chars()
        .filter(|c| value["activation_counts"][c.to_string()].as_u64() == Some(0))
        .map(|c| c.to_string())
        .collect();
    let parent = parent_policy.as_deref().unwrap_or("");
    let newly_enabled: Vec<_> = policy
        .chars()
        .filter(|c| !parent.contains(*c))
        .map(|c| c.to_string())
        .collect();
    let disabled: Vec<_> = parent
        .chars()
        .filter(|c| !policy.contains(*c))
        .map(|c| c.to_string())
        .collect();
    let inactive_requested: Vec<_> = inactive
        .iter()
        .filter(|c| !parent.contains(c.as_str()))
        .cloned()
        .collect();
    let inactive_inherited: Vec<_> = inactive
        .iter()
        .filter(|c| parent.contains(c.as_str()))
        .cloned()
        .collect();
    value["schema"] = json!("lco-small-emission-v1");
    value["field_width"] = json!(16);
    value["backend"] = json!("original-kaliski");
    value["policy"] = json!(if policy.is_empty() { "none" } else { &policy });
    value["activation"] = value["activation_counts"].clone();
    value["activation_admitted"] = json!(inactive_requested.is_empty());
    value["inactive_mechanisms"] = json!(inactive);
    value["inactive_new_mechanisms"] = json!(inactive_requested);
    value["inherited_inactive_mechanisms"] = json!(inactive_inherited);
    value["newly_enabled_mechanisms"] = json!(newly_enabled);
    value["disabled_mechanisms"] = json!(disabled);
    value["parent_policy"] = parent_policy
        .as_ref()
        .map(|p| json!(if p.is_empty() { "none" } else { p }))
        .unwrap_or(Value::Null);
    value["activation_admission_scope"]=json!("Newly enabled conditional compiler flags must activate. The caller checks disabled flags against its pinned parent receipt. Inactive flags give no transfer evidence. The catalog records activation in its own certificate.");
    value["cancel_adjacent"] = json!(cancel_adjacent);
    value["local_catalog"] = json!(local_catalog);
    value["free_s"] = json!(free_s);
    value["combined_y"] = json!(combined_y);
    value["ops"] = json!(out);
    value["ops_sha256"] = json!(file_sha256(&out)?);
    value["receipt"] = json!(receipt);
    value["compiler_log"] = json!(log_path);
    value["source_sha256"] = json!(source_sha());
    value["conditional_source_sha256"] = json!(CONDITIONAL_SHA256);
    value["clean_child_environment"] = json!(true);
    value["mean_toffoli"] = Value::Null;
    value["metric_scope"]=json!("Q is the referenced width. Compiler T precedes optional cancellation and catalog edits. circuit-check measures executed T.");
    study_check::atomic_json(&receipt, &value).map_err(|e| e.to_string())?;
    println!("{value}");
    if value["activation_admitted"] != true {
        return Err(format!("requested inactive mechanisms {}; supply a valid pinned-parent context only for inherited flags",value["inactive_new_mechanisms"]));
    }
    Ok(())
}
fn main() {
    if let Err(e) = run() {
        eprintln!("small-emit: {e}");
        std::process::exit(2)
    }
}
