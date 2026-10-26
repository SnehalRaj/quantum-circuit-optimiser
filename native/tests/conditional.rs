use serde_json::Value;
use std::process::Command;

#[test]
fn real_small_point_addition_activates_both_conditional_mechanisms() {
    let dir = std::env::temp_dir().join(format!("lco-cond-activation-{}", std::process::id()));
    std::fs::create_dir(&dir).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_small-emit"))
        .arg("--out")
        .arg(dir.join("point.qops.zst"))
        .args(["--policy", "mfsur", "--parent-policy", "mfs"])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let receipt: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(receipt["field_width"], 16);
    assert_eq!(receipt["guards_active"], true);
    assert_eq!(receipt["activation_admitted"], true);
    assert_eq!(
        receipt["inherited_inactive_mechanisms"],
        serde_json::json!(["s"])
    );
    assert_eq!(
        receipt["conditional_source_sha256"],
        "a56f374ad86c7dd3ca13cfd08291f5f9a4f40a54e122959c481656a54b9d987e"
    );
    assert!(
        receipt["activation"]["u"].as_u64().unwrap() > 0,
        "inverse-AND rule is inactive on real point addition"
    );
    assert!(
        receipt["activation"]["r"].as_u64().unwrap() > 0,
        "unread-write rule is inactive on real point addition"
    );
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn inactive_standalone_mechanism_is_rejected_with_a_receipt() {
    let dir = std::env::temp_dir().join(format!("lco-inactive-{}", std::process::id()));
    std::fs::create_dir(&dir).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_small-emit"))
        .arg("--out")
        .arg(dir.join("point.qops.zst"))
        .args(["--policy", "s"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["activation_admitted"], false);
    assert_eq!(value["inactive_new_mechanisms"], serde_json::json!(["s"]));
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn diagnostic_environment_controls_are_rejected() {
    for name in ["SL_COND_FAULT", "SL_COND_ALIGN", "SL_COND_NOGUARD"] {
        let output = Command::new(env!("CARGO_BIN_EXE_small-emit"))
            .env(name, "1")
            .args(["--out", "unused.qops.zst", "--policy", "mfs"])
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2));
        assert!(String::from_utf8_lossy(&output.stderr).contains("forbidden"));
    }
}
