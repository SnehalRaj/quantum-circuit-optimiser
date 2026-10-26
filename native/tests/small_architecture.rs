use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn directory(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("lco-small-{name}-{}", std::process::id()));
    std::fs::create_dir(&path).unwrap();
    path
}

fn emit(path: &Path, arguments: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_small-emit"))
        .env_clear()
        .arg("--out")
        .arg(path)
        .args(["--policy", "mfs", "--parent-policy", "mfs"])
        .args(arguments)
        .output()
        .unwrap()
}

#[test]
fn architecture_flags_reject_invalid_missing_and_duplicate_values_before_emission() {
    let dir = directory("bad-architecture");
    let path = dir.join("ops.zst");
    for flag in ["--free-s", "--combined-y"] {
        for value in ["1", "0", "True", "False", "yes", "", "true,false"] {
            let output = emit(&path, &[flag, value]);
            assert_eq!(output.status.code(), Some(2), "{flag}={value}");
            assert!(String::from_utf8_lossy(&output.stderr).contains("requires true or false"));
            assert!(!path.exists());
        }
        let missing = emit(&path, &[flag]);
        assert_eq!(missing.status.code(), Some(2));
        assert!(String::from_utf8_lossy(&missing.stderr).contains("missing value"));
        let duplicate = emit(&path, &[flag, "false", flag, "true"]);
        assert_eq!(duplicate.status.code(), Some(2));
        assert!(String::from_utf8_lossy(&duplicate.stderr).contains("duplicate option"));
        assert!(!path.exists());
    }
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn false_defaults_preserve_parent_bytes_and_record_architecture() {
    let dir = directory("default-architecture");
    let default_path = dir.join("default.zst");
    let explicit_path = dir.join("false.zst");
    let default = emit(&default_path, &[]);
    assert!(default.status.success(), "{}", String::from_utf8_lossy(&default.stderr));
    let explicit = emit(&explicit_path, &["--free-s", "false", "--combined-y", "false"]);
    assert!(explicit.status.success(), "{}", String::from_utf8_lossy(&explicit.stderr));
    assert_eq!(std::fs::read(&default_path).unwrap(), std::fs::read(&explicit_path).unwrap());
    assert_eq!(
        lco_native::file_sha256(&default_path).unwrap(),
        "b84161037cdd8580e28172308418c1767b179abb27a7b02aa6ac90ebb74bfaf8"
    );
    for output in [default, explicit] {
        let value: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(value["free_s"], false);
        assert_eq!(value["combined_y"], false);
        assert_eq!(value["Q"], 214);
        assert_eq!(value["clean_child_environment"], true);
    }
    std::fs::remove_dir_all(dir).unwrap();
}
