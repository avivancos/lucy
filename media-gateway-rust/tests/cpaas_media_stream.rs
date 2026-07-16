use lucy_media_gateway::asterisk::media_plane::MediaPlaneEvent;
use lucy_media_gateway::cpaas::{
    CpaasMediaStreamAdapter, CpaasOutput, CpaasProvider, MAX_CPAAAS_MESSAGE_BYTES,
};
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

const TWILIO_STREAM_ID: &str = "MZREDACTED000000000000000000000001";
const TELNYX_STREAM_ID: &str = "32de0dea-53cb-4b21-89a4-redacted000";

fn text(value: Value) -> Message {
    Message::Text(value.to_string().into())
}

fn started(adapter: &mut CpaasMediaStreamAdapter, provider: CpaasProvider) {
    let frames = match provider {
        CpaasProvider::Twilio => vec![
            text(json!({"event":"connected","protocol":"Call","version":"1.0.0"})),
            text(json!({
                "event":"start",
                "sequenceNumber":"1",
                "streamSid":TWILIO_STREAM_ID,
                "start":{
                    "accountSid":"ACREDACTED",
                    "streamSid":TWILIO_STREAM_ID,
                    "callSid":"CAREDACTED",
                    "tracks":["inbound"],
                    "mediaFormat":{"encoding":"audio/x-mulaw","sampleRate":8000,"channels":1},
                    "customParameters":{}
                }
            })),
        ],
        CpaasProvider::Telnyx => vec![
            text(json!({"event":"connected","version":"1.0.0"})),
            text(json!({
                "event":"start",
                "sequence_number":"1",
                "stream_id":TELNYX_STREAM_ID,
                "start":{
                    "user_id":"redacted",
                    "call_control_id":"v2:redacted",
                    "call_session_id":"redacted",
                    "from":"[redacted]",
                    "to":"[redacted]",
                    "media_format":{"encoding":"L16","sample_rate":16000,"channels":1}
                }
            })),
        ],
    };
    let mut outputs = Vec::new();
    for frame in frames {
        outputs.extend(adapter.handle_message(frame, 1_000).unwrap());
    }
    let [CpaasOutput::Control(message)] = outputs.as_slice() else {
        panic!("provider handshake must emit one session.started")
    };
    assert_eq!(message.message_type(), "session.started");
    assert_eq!(
        message.as_value()["transport"],
        format!("cpaas/{}", provider.registry_key())
    );
    assert_eq!(message.as_value()["caller"], "[redacted]");
}

#[test]
fn both_provider_handshakes_emit_redacted_control_events() {
    for provider in [CpaasProvider::Telnyx, CpaasProvider::Twilio] {
        let mut adapter = CpaasMediaStreamAdapter::new(provider);
        started(&mut adapter, provider);
    }
}

#[test]
fn twilio_mulaw_and_telnyx_l16_decode_inside_the_media_plane() {
    let mut twilio = CpaasMediaStreamAdapter::new(CpaasProvider::Twilio);
    started(&mut twilio, CpaasProvider::Twilio);
    let outputs = twilio
        .handle_message(
            text(json!({
                "event":"media","sequenceNumber":"2","streamSid":TWILIO_STREAM_ID,
                "media":{"track":"inbound","chunk":"1","timestamp":"20","payload":"/38A"}
            })),
            1_020,
        )
        .unwrap();
    let [CpaasOutput::Media(frame)] = outputs.as_slice() else {
        panic!("Twilio media must stay in Rust")
    };
    assert_eq!(frame.sample_rate_hz, 8_000);
    assert_eq!(frame.pcm_s16le.len(), 6);

    let mut telnyx = CpaasMediaStreamAdapter::new(CpaasProvider::Telnyx);
    started(&mut telnyx, CpaasProvider::Telnyx);
    let outputs = telnyx
        .handle_message(
            text(json!({
                "event":"media","sequence_number":"2","stream_id":TELNYX_STREAM_ID,
                "media":{"track":"inbound","chunk":"1","timestamp":"20","payload":"EjT+3A=="}
            })),
            1_020,
        )
        .unwrap();
    let [CpaasOutput::Media(frame)] = outputs.as_slice() else {
        panic!("Telnyx media must stay in Rust")
    };
    assert_eq!(frame.sample_rate_hz, 16_000);
    assert_eq!(frame.pcm_s16le, [0x34, 0x12, 0xdc, 0xfe]);
}

#[test]
fn telnyx_reorders_media_chunks_and_rejects_duplicates() {
    let mut adapter = CpaasMediaStreamAdapter::new(CpaasProvider::Telnyx);
    started(&mut adapter, CpaasProvider::Telnyx);
    let chunk = |chunk: &str, payload: &str| {
        text(json!({
            "event":"media","sequence_number":chunk,"stream_id":TELNYX_STREAM_ID,
            "media":{"track":"inbound","chunk":chunk,"timestamp":chunk,"payload":payload}
        }))
    };

    assert!(adapter
        .handle_message(chunk("2", "AAI="), 1_040)
        .unwrap()
        .is_empty());
    let outputs = adapter.handle_message(chunk("1", "AAE="), 1_020).unwrap();
    assert_eq!(outputs.len(), 2);
    let CpaasOutput::Media(first) = &outputs[0] else {
        panic!("first reordered output must be media")
    };
    let CpaasOutput::Media(second) = &outputs[1] else {
        panic!("second reordered output must be media")
    };
    assert_eq!(first.pcm_s16le, [1, 0]);
    assert_eq!(second.pcm_s16le, [2, 0]);
    assert!(adapter
        .handle_message(chunk("1", "AAE="), 1_060)
        .unwrap_err()
        .to_string()
        .contains("duplicate"));
}

#[test]
fn sustained_sequential_media_does_not_accumulate_completed_chunk_state() {
    let mut adapter = CpaasMediaStreamAdapter::new(CpaasProvider::Telnyx);
    started(&mut adapter, CpaasProvider::Telnyx);

    for chunk in 1..=10_000_u64 {
        let outputs = adapter
            .handle_message(
                text(json!({
                    "event":"media",
                    "sequence_number":chunk.to_string(),
                    "stream_id":TELNYX_STREAM_ID,
                    "media":{
                        "track":"inbound",
                        "chunk":chunk.to_string(),
                        "timestamp":chunk.to_string(),
                        "payload":"AAE="
                    }
                })),
                1_000 + chunk,
            )
            .unwrap();
        assert_eq!(outputs.len(), 1);
    }

    let duplicate = adapter
        .handle_message(
            text(json!({
                "event":"media","sequence_number":"10001","stream_id":TELNYX_STREAM_ID,
                "media":{"track":"inbound","chunk":"1","timestamp":"1","payload":"AAE="}
            })),
            11_001,
        )
        .unwrap_err();
    assert!(duplicate.to_string().contains("duplicate"));
}

#[test]
fn playback_cancel_marks_dtmf_and_stop_map_without_phone_numbers() {
    for provider in [CpaasProvider::Telnyx, CpaasProvider::Twilio] {
        let mut adapter = CpaasMediaStreamAdapter::new(provider);
        started(&mut adapter, provider);

        let media = adapter.playback_message(&[0, 0, 0xff, 0x7f]).unwrap();
        let Message::Text(media) = media else {
            panic!("CPaaS playback must be a JSON text frame")
        };
        let media: Value = serde_json::from_str(media.as_ref()).unwrap();
        assert_eq!(media["event"], "media");
        assert!(media["media"]["payload"].as_str().unwrap().len() >= 4);

        let clear: Value = serde_json::from_str(
            adapter
                .clear_message()
                .unwrap()
                .into_text()
                .unwrap()
                .as_ref(),
        )
        .unwrap();
        assert_eq!(clear["event"], "clear");
        let mark = adapter.mark_message("utt-1", 12).unwrap();
        let mark: Value = serde_json::from_str(mark.into_text().unwrap().as_ref()).unwrap();
        assert_eq!(mark["event"], "mark");

        let (stream_key, stream_id, sequence_key) = match provider {
            CpaasProvider::Twilio => ("streamSid", TWILIO_STREAM_ID, "sequenceNumber"),
            CpaasProvider::Telnyx => ("stream_id", TELNYX_STREAM_ID, "sequence_number"),
        };
        let outputs = adapter
            .handle_message(
                text(json!({
                    "event":"dtmf",(stream_key):stream_id,(sequence_key):"2",
                    "dtmf":{"track":"inbound_track","digit":"5"}
                })),
                1_040,
            )
            .unwrap();
        let [CpaasOutput::Control(dtmf)] = outputs.as_slice() else {
            panic!("DTMF must map to control")
        };
        assert_eq!(dtmf.message_type(), "dtmf");
        assert_eq!(dtmf.as_value()["digit"], "5");

        let outputs = adapter
            .handle_message(
                text(json!({"event":"stop",(stream_key):stream_id,(sequence_key):"3","stop":{}})),
                1_060,
            )
            .unwrap();
        let [CpaasOutput::Control(ended)] = outputs.as_slice() else {
            panic!("stop must map to session.ended")
        };
        assert_eq!(ended.message_type(), "session.ended");
        assert!(!ended.as_value().to_string().contains('+'));
    }
}

#[test]
fn media_plane_events_and_provider_marks_use_the_locked_control_schema() {
    let mut adapter = CpaasMediaStreamAdapter::new(CpaasProvider::Twilio);
    started(&mut adapter, CpaasProvider::Twilio);
    let started = adapter
        .media_plane_event(MediaPlaneEvent::PlaybackStarted {
            utterance_id: "utt-1".into(),
        })
        .unwrap();
    assert_eq!(started.message_type(), "tts.playback");

    let mark = adapter.mark_message("utt-1", 12).unwrap();
    let mark: Value = serde_json::from_str(mark.into_text().unwrap().as_ref()).unwrap();
    let outputs = adapter
        .handle_message(
            text(json!({
                "event":"mark","sequenceNumber":"2","streamSid":TWILIO_STREAM_ID,
                "mark":{"name":mark["mark"]["name"]}
            })),
            1_100,
        )
        .unwrap();
    let [CpaasOutput::Control(progress)] = outputs.as_slice() else {
        panic!("provider mark must map to playback progress")
    };
    assert_eq!(progress.message_type(), "tts.playback");
    assert_eq!(progress.as_value()["state"], "mark");
    assert_eq!(progress.as_value()["mark_chars"], 12);
}

#[test]
fn malformed_cross_session_and_oversized_frames_fail_closed() {
    let mut adapter = CpaasMediaStreamAdapter::new(CpaasProvider::Twilio);
    assert!(adapter
        .handle_message(
            Message::Text("x".repeat(MAX_CPAAAS_MESSAGE_BYTES + 1).into()),
            1
        )
        .unwrap_err()
        .to_string()
        .contains("maximum"));
    assert!(adapter
        .handle_message(text(json!({"event":"media"})), 2)
        .unwrap_err()
        .to_string()
        .contains("start"));

    started(&mut adapter, CpaasProvider::Twilio);
    let error = adapter
        .handle_message(
            text(json!({
                "event":"media","sequenceNumber":"2","streamSid":"MZOTHER",
                "media":{"track":"inbound","chunk":"1","timestamp":"20","payload":"/w=="}
            })),
            3,
        )
        .unwrap_err();
    assert!(error.to_string().contains("stream id"));
}
