use lucy_media_gateway::asterisk::directives::{
    parse_asterisk_directive, validate_asterisk_directive_envelope, AsteriskDirective,
};
use lucy_media_gateway::control::schema::ControlMessage;
use serde_json::{json, Value};

fn message(kind: &str, payload: Value) -> ControlMessage {
    let mut value = json!({
        "v": 1,
        "type": kind,
        "session_id": "session-1",
        "seq": 1,
        "ts_ms": 1_000
    });
    value.as_object_mut().unwrap().extend(
        payload
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.clone())),
    );
    serde_json::from_value(value).unwrap()
}

#[test]
fn locked_control_directives_map_to_asterisk_operations() {
    let cases = [
        (
            message(
                "tts.speak",
                json!({"utterance_id": "utt-1", "text": "Hello", "flush": true}),
            ),
            AsteriskDirective::Playback {
                utterance_id: "utt-1".to_string(),
                text: "Hello".to_string(),
                flush: true,
            },
        ),
        (
            message("tts.cancel", json!({"utterance_id": "utt-1"})),
            AsteriskDirective::CancelPlayback {
                utterance_id: "utt-1".to_string(),
            },
        ),
        (
            message("dtmf.send", json!({"digits": "12#"})),
            AsteriskDirective::SendDtmf {
                digits: "12#".to_string(),
            },
        ),
        (
            message("transfer", json!({"target": "PJSIP/support"})),
            AsteriskDirective::Transfer {
                target: "PJSIP/support".to_string(),
            },
        ),
        (
            message("session.end", json!({"reason": "completed"})),
            AsteriskDirective::Hangup {
                reason: "completed".to_string(),
            },
        ),
    ];

    for (message, expected) in cases {
        assert_eq!(parse_asterisk_directive(&message).unwrap(), Some(expected));
    }
}

#[test]
fn non_adapter_messages_are_ignored_and_unsafe_values_fail_closed() {
    let configure = message(
        "session.configure",
        json!({"stt": "local", "tts": "local", "vad": "local"}),
    );
    assert_eq!(parse_asterisk_directive(&configure).unwrap(), None);

    for invalid in [
        message("dtmf.send", json!({"digits": "12X"})),
        message("transfer", json!({"target": "  "})),
        message(
            "tts.speak",
            json!({"utterance_id": "utt-1", "text": "", "flush": false}),
        ),
    ] {
        assert!(parse_asterisk_directive(&invalid).is_err());
    }
}

#[test]
fn directive_envelope_uses_one_schema_session_and_sequence_contract() {
    let mut last_seq = None;
    let first = message("session.end", json!({"reason": "complete"}));
    validate_asterisk_directive_envelope(&first, "session-1", &mut last_seq).unwrap();
    assert_eq!(last_seq, Some(1));

    let duplicate = message("session.end", json!({"reason": "complete"}));
    assert!(
        validate_asterisk_directive_envelope(&duplicate, "session-1", &mut last_seq)
            .unwrap_err()
            .to_string()
            .contains("increase monotonically")
    );

    let wrong_session: ControlMessage = serde_json::from_value(json!({
        "v": 1,
        "type": "session.end",
        "session_id": "other-session",
        "seq": 2,
        "ts_ms": 1_001,
        "reason": "complete"
    }))
    .unwrap();
    assert!(
        validate_asterisk_directive_envelope(&wrong_session, "session-1", &mut last_seq).is_err()
    );
}
