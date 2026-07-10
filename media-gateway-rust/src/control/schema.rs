use serde::{de, Deserialize, Deserializer, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;

const ENVELOPE_FIELDS: &[&str] = &["v", "type", "session_id", "turn_id", "seq", "ts_ms"];

#[derive(Debug, Clone, Serialize)]
#[serde(transparent)]
pub struct ControlMessage(Value);

impl ControlMessage {
    pub fn message_type(&self) -> &str {
        self.0["type"].as_str().expect("validated control type")
    }

    pub fn as_value(&self) -> &Value {
        &self.0
    }
}

impl<'de> Deserialize<'de> for ControlMessage {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = Value::deserialize(deserializer)?;
        validate(&value).map_err(de::Error::custom)?;
        Ok(Self(value))
    }
}

pub fn canonical_json(message: &ControlMessage) -> Result<String, serde_json::Error> {
    serde_json::to_string(&serde_json::to_value(message)?)
}

fn validate(value: &Value) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or_else(|| "control message must be an object".to_string())?;
    let kind = object
        .get("type")
        .and_then(Value::as_str)
        .ok_or_else(|| "control message type is required".to_string())?;
    let payload = payload_fields(kind).ok_or_else(|| format!("unknown type: {kind}"))?;
    let allowed: BTreeSet<&str> = ENVELOPE_FIELDS
        .iter()
        .copied()
        .chain(payload.iter().copied())
        .collect();
    for field in object.keys() {
        if !allowed.contains(field.as_str()) {
            return Err(format!("unknown field {field} for {kind}"));
        }
    }
    for field in ["type", "session_id", "seq", "ts_ms"] {
        if !object.contains_key(field) {
            return Err(format!("missing required envelope field {field}"));
        }
    }
    for field in required_payload_fields(kind) {
        if !object.contains_key(*field) {
            return Err(format!("missing required field {field} for {kind}"));
        }
    }
    for (field, field_value) in object {
        validate_field_type(kind, field, field_value)?;
    }
    Ok(())
}

fn required_payload_fields(kind: &str) -> &'static [&'static str] {
    match kind {
        "barge_in" => &["at_ms", "during"],
        "hold" => &["state"],
        "recording.failed" => &["recording_id", "error_code"],
        "session.started" => &["transport", "caller", "codecs"],
        "tts.playback" => &["utterance_id", "state"],
        "tts.speak" => &["utterance_id", "text"],
        _ => payload_fields(kind).unwrap_or(&[]),
    }
}

fn validate_field_type(kind: &str, field: &str, value: &Value) -> Result<(), String> {
    let valid = match field {
        "v" | "seq" | "ts_ms" | "at_ms" | "speech_ms" | "stt_ms" | "mark_chars" | "timeout_ms"
        | "duration_ms" | "byte_count" => value.as_u64().is_some(),
        "stability" | "confidence" | "jitter_ms" | "rtt_ms" | "packet_loss" => {
            value.as_f64().is_some()
        }
        "retryable" | "flush" | "music" => value.is_boolean(),
        "codecs" | "features" => value
            .as_array()
            .is_some_and(|items| items.iter().all(Value::is_string)),
        "turn_id" => value.is_null() || value.is_string(),
        "utterance_id" if kind == "barge_in" => value.is_null() || value.is_string(),
        _ => value.is_string(),
    };
    if !valid {
        return Err(format!("invalid field type for {field} in {kind}"));
    }
    match (field, value.as_str()) {
        ("outcome", Some(value)) if !["human", "machine", "unknown"].contains(&value) => {
            Err(format!("invalid outcome: {value}"))
        }
        ("during", Some(value)) if !["speaking", "thinking"].contains(&value) => {
            Err(format!("invalid barge-in phase: {value}"))
        }
        ("leg", Some(value)) if !["caller", "agent", "mixed"].contains(&value) => {
            Err(format!("invalid recording leg: {value}"))
        }
        ("state", Some(value))
            if kind == "tts.playback"
                && !["started", "mark", "finished", "flushed"].contains(&value) =>
        {
            Err(format!("invalid playback state: {value}"))
        }
        ("state", Some(value)) if kind == "hold" && !["hold", "resume"].contains(&value) => {
            Err(format!("invalid hold state: {value}"))
        }
        _ => Ok(()),
    }
}

fn payload_fields(kind: &str) -> Option<&'static [&'static str]> {
    Some(match kind {
        "amd.result" => &["outcome", "confidence"],
        "barge_in" => &["at_ms", "during", "utterance_id"],
        "dial" => &["target", "caller_id", "timeout_ms"],
        "dtmf" => &["digit"],
        "dtmf.send" => &["digits"],
        "hold" => &["state", "music"],
        "realtime.connect" => &["provider", "model"],
        "realtime.tool_result" => &["call_id", "output_json"],
        "recording.failed" => &["recording_id", "error_code", "retryable"],
        "recording.start" => &[
            "recording_id",
            "leg",
            "blob_id",
            "upload_url_ref",
            "container",
            "consent_ref",
        ],
        "recording.started" => &["recording_id", "leg", "blob_id", "consent_ref"],
        "recording.stop" => &["recording_id"],
        "recording.uploaded" => &[
            "recording_id",
            "leg",
            "blob_id",
            "upload_url_ref",
            "duration_ms",
            "byte_count",
            "sha256",
            "container",
            "consent_ref",
        ],
        "session.configure" => &["stt", "tts", "vad"],
        "session.end" | "session.ended" => &["reason"],
        "session.started" => &["transport", "caller", "codecs", "features"],
        "stt.final" => &["text", "provider", "stt_ms"],
        "stt.partial" => &["text", "stability", "provider"],
        "transfer" => &["target"],
        "transport.metrics" => &["jitter_ms", "rtt_ms", "packet_loss"],
        "tts.cancel" => &["utterance_id"],
        "tts.playback" => &["utterance_id", "state", "mark_chars"],
        "tts.speak" => &["utterance_id", "text", "flush"],
        "tts.stream_end" => &[],
        "vad.speech_end" => &["at_ms", "speech_ms"],
        "vad.speech_start" => &["at_ms"],
        _ => return None,
    })
}
