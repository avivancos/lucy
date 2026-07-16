use crate::control::schema::ControlMessage;
use std::fmt;

use super::VALID_DTMF;

pub(crate) const CONTROL_SCHEMA_VERSION: u64 = 1;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AsteriskDirective {
    Playback {
        utterance_id: String,
        text: String,
        flush: bool,
    },
    CancelPlayback {
        utterance_id: String,
    },
    SendDtmf {
        digits: String,
    },
    Transfer {
        target: String,
    },
    Hangup {
        reason: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AsteriskDirectiveError(String);

impl AsteriskDirectiveError {
    fn invalid(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for AsteriskDirectiveError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for AsteriskDirectiveError {}

pub fn validate_asterisk_directive_envelope(
    message: &ControlMessage,
    session_id: &str,
    last_seq: &mut Option<u64>,
) -> Result<(), AsteriskDirectiveError> {
    let value = message.as_value();
    if value.get("v").and_then(serde_json::Value::as_u64) != Some(CONTROL_SCHEMA_VERSION) {
        return Err(AsteriskDirectiveError::invalid(
            "control directive schema version does not match",
        ));
    }
    if value.get("session_id").and_then(serde_json::Value::as_str) != Some(session_id) {
        return Err(AsteriskDirectiveError::invalid(
            "control directive session_id does not match active call",
        ));
    }
    let seq = value
        .get("seq")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| AsteriskDirectiveError::invalid("control directive seq is invalid"))?;
    if last_seq.is_some_and(|previous| seq <= previous) {
        return Err(AsteriskDirectiveError::invalid(
            "control directive seq must increase monotonically",
        ));
    }
    *last_seq = Some(seq);
    Ok(())
}

pub fn parse_asterisk_directive(
    message: &ControlMessage,
) -> Result<Option<AsteriskDirective>, AsteriskDirectiveError> {
    let value = message.as_value();
    let directive = match message.message_type() {
        "tts.speak" => AsteriskDirective::Playback {
            utterance_id: required_text(value, "utterance_id")?.to_string(),
            text: required_text(value, "text")?.to_string(),
            flush: value
                .get("flush")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
        },
        "tts.cancel" => AsteriskDirective::CancelPlayback {
            utterance_id: required_text(value, "utterance_id")?.to_string(),
        },
        "dtmf.send" => {
            let digits = required_text(value, "digits")?;
            if !digits
                .as_bytes()
                .iter()
                .all(|digit| VALID_DTMF.contains(digit))
            {
                return Err(AsteriskDirectiveError::invalid(
                    "dtmf.send contains an invalid digit",
                ));
            }
            AsteriskDirective::SendDtmf {
                digits: digits.to_ascii_uppercase(),
            }
        }
        "transfer" => AsteriskDirective::Transfer {
            target: required_text(value, "target")?.to_string(),
        },
        "session.end" => AsteriskDirective::Hangup {
            reason: required_text(value, "reason")?.to_string(),
        },
        _ => return Ok(None),
    };
    Ok(Some(directive))
}

fn required_text<'a>(
    value: &'a serde_json::Value,
    field: &str,
) -> Result<&'a str, AsteriskDirectiveError> {
    value
        .get(field)
        .and_then(serde_json::Value::as_str)
        .filter(|text| !text.trim().is_empty() && !text.chars().any(char::is_control))
        .ok_or_else(|| {
            AsteriskDirectiveError::invalid(format!(
                "Asterisk directive field {field} must be non-empty text",
            ))
        })
}
