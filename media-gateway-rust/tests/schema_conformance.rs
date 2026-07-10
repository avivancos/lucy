use lucy_media_gateway::control::schema::{canonical_json, ControlMessage};
use serde_json::Value;
use std::{collections::BTreeSet, fs, path::PathBuf};

fn golden_dir() -> PathBuf {
    std::env::var("LUCY_CONTROL_SCHEMA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/control_schema")
        })
}

fn files() -> Vec<PathBuf> {
    let mut paths: Vec<_> = fs::read_dir(golden_dir())
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.extension().and_then(|item| item.to_str()) == Some("json"))
        .collect();
    paths.sort();
    paths
}

#[test]
fn golden_files_reserialize_byte_identical() {
    for path in files() {
        let raw = fs::read_to_string(path).unwrap();
        let message: ControlMessage = serde_json::from_str(raw.trim_end()).unwrap();
        assert_eq!(canonical_json(&message).unwrap(), raw.trim_end());
    }
}

#[test]
fn every_golden_type_maps_to_a_variant_and_counts_match() {
    let messages: Vec<ControlMessage> = files()
        .iter()
        .map(|path| serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap())
        .collect();
    let types: BTreeSet<_> = messages.iter().map(ControlMessage::message_type).collect();
    assert_eq!(messages.len(), 27);
    assert_eq!(types.len(), messages.len());
}

#[test]
fn unknown_type_is_rejected() {
    let raw = r#"{"v":1,"type":"unknown","session_id":"s","seq":1,"ts_ms":1}"#;
    assert!(serde_json::from_str::<ControlMessage>(raw).is_err());
}

#[test]
fn extra_field_is_rejected() {
    let mut value: Value = serde_json::from_str(&fs::read_to_string(&files()[0]).unwrap()).unwrap();
    value["unexpected"] = Value::Bool(true);
    assert!(serde_json::from_value::<ControlMessage>(value).is_err());
}

#[test]
fn missing_required_payload_field_is_rejected() {
    let path = golden_dir().join("stt.final.json");
    let mut value: Value = serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap();
    value.as_object_mut().unwrap().remove("text");
    assert!(serde_json::from_value::<ControlMessage>(value).is_err());
}

#[test]
fn wrong_payload_field_type_is_rejected() {
    let path = golden_dir().join("stt.final.json");
    let mut value: Value = serde_json::from_str(&fs::read_to_string(path).unwrap()).unwrap();
    value["stt_ms"] = Value::String("twenty".to_string());
    assert!(serde_json::from_value::<ControlMessage>(value).is_err());
}
