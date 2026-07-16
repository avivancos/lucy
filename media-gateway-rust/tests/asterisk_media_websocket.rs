use lucy_media_gateway::asterisk::media_plane::MediaPlaneEvent;
use lucy_media_gateway::asterisk::media_websocket::{
    MediaWebSocketAdapter, MediaWebSocketOutput, MediaWebSocketSignal,
    MAX_MEDIA_WEBSOCKET_CONTROL_BYTES, MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES,
};
use lucy_media_gateway::asterisk::metrics::TransportMetricSample;
use serde_json::{json, Value};
use tokio_tungstenite::tungstenite::Message;

const CHANNEL_ID: &str = "pbx1-123456789.999";

fn text(value: Value) -> Message {
    Message::Text(serde_json::to_string(&value).unwrap().into())
}

fn media_start(format: &str) -> Message {
    text(json!({
        "event": "MEDIA_START",
        "connection_id": "e226e283-c90a-4ea9-9e37-389000b9ef47",
        "channel": "WebSocket/media_connection1",
        "channel_id": CHANNEL_ID,
        "format": format,
        "optimal_frame_size": 640,
        "ptime": 20
    }))
}

fn started(adapter: &mut MediaWebSocketAdapter) {
    let output = adapter.handle_message(media_start("slin"), 2_000).unwrap();
    let Some(MediaWebSocketOutput::Control(message)) = output else {
        panic!("MEDIA_START must produce session.started")
    };
    assert_eq!(message.message_type(), "session.started");
    assert_eq!(message.as_value()["session_id"], CHANNEL_ID);
    assert_eq!(message.as_value()["transport"], "asterisk/media_websocket");
    assert_eq!(message.as_value()["codecs"], json!(["slin"]));
}

#[test]
fn media_start_then_binary_frames_stay_in_the_media_plane() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);

    let output = adapter
        .handle_message(Message::Binary(vec![1, 0, 2, 0].into()), 2_020)
        .unwrap();
    let Some(MediaWebSocketOutput::Media(media)) = output else {
        panic!("binary WebSocket frames must remain media")
    };
    assert_eq!(media.session_id, CHANNEL_ID);
    assert_eq!(media.codec, "slin");
    assert_eq!(media.sample_rate_hz, 8_000);
    assert_eq!(media.bytes.as_ref(), [1, 0, 2, 0]);
}

#[test]
fn dtmf_end_maps_to_the_locked_control_schema() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);

    let output = adapter
        .handle_message(
            text(json!({
                "event": "DTMF_END",
                "channel_id": CHANNEL_ID,
                "digit": "5"
            })),
            2_030,
        )
        .unwrap();
    let Some(MediaWebSocketOutput::Control(message)) = output else {
        panic!("DTMF_END must produce dtmf")
    };
    assert_eq!(message.message_type(), "dtmf");
    assert_eq!(message.as_value()["digit"], "5");
}

#[test]
fn transport_metrics_follow_the_latest_media_websocket_turn() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);
    assert!(adapter
        .transport_metrics(TransportMetricSample::tcp_websocket(8.0, 2.0), 2_010)
        .unwrap()
        .is_none());

    adapter
        .media_plane_event(MediaPlaneEvent::SpeechStarted { at_ms: 2_020 })
        .unwrap();
    adapter
        .media_plane_event(MediaPlaneEvent::TranscriptFinal {
            text: "hello".to_string(),
            stt_ms: 5,
            at_ms: 2_030,
            provider: "fixture",
        })
        .unwrap();
    let message = adapter
        .transport_metrics(TransportMetricSample::tcp_websocket(8.0, 2.0), 2_040)
        .unwrap()
        .expect("completed turns receive metrics");
    assert_eq!(message.message_type(), "transport.metrics");
    assert_eq!(message.as_value()["turn_id"], "turn-1");
    assert_eq!(message.as_value()["rtt_ms"], 8.0);
    assert_eq!(message.as_value()["jitter_ms"], 2.0);
    assert_eq!(message.as_value()["packet_loss"], 0.0);
}

#[test]
fn media_websocket_ping_requires_a_pong_reply() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);
    assert!(matches!(
        adapter
            .handle_message(Message::Ping(vec![9].into()), 2_050)
            .unwrap(),
        Some(MediaWebSocketOutput::ReplyPong(payload)) if payload == vec![9]
    ));
}

#[test]
fn xoff_and_xon_map_to_internal_flow_control() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);

    for (event, expected) in [
        ("MEDIA_XOFF", MediaWebSocketSignal::Pause),
        ("MEDIA_XON", MediaWebSocketSignal::Resume),
    ] {
        let output = adapter
            .handle_message(
                text(json!({"event": event, "channel_id": CHANNEL_ID})),
                2_040,
            )
            .unwrap();
        assert!(matches!(
            output,
            Some(MediaWebSocketOutput::FlowControl(signal)) if signal == expected
        ));
    }
}

#[test]
fn media_marks_round_trip_to_tts_playback_progress() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);

    let command = adapter.mark_command("utt-1", 12).unwrap();
    let Message::Text(command) = command else {
        panic!("MARK_MEDIA must be a text frame")
    };
    let command: Value = serde_json::from_str(command.as_ref()).unwrap();
    assert_eq!(command["command"], "MARK_MEDIA");
    let correlation_id = command["correlation_id"].as_str().unwrap();

    let output = adapter
        .handle_message(
            text(json!({
                "event": "MEDIA_MARK_PROCESSED",
                "channel_id": CHANNEL_ID,
                "correlation_id": correlation_id
            })),
            2_050,
        )
        .unwrap();
    let Some(MediaWebSocketOutput::Control(message)) = output else {
        panic!("processed mark must produce playback progress")
    };
    assert_eq!(message.message_type(), "tts.playback");
    assert_eq!(message.as_value()["utterance_id"], "utt-1");
    assert_eq!(message.as_value()["state"], "mark");
    assert_eq!(message.as_value()["mark_chars"], 12);
}

#[test]
fn cancel_is_the_documented_flush_media_command() {
    let command = MediaWebSocketAdapter::cancel_command();
    let Message::Text(command) = command else {
        panic!("FLUSH_MEDIA must be a text frame")
    };

    assert_eq!(
        serde_json::from_str::<Value>(command.as_ref()).unwrap(),
        json!({"command": "FLUSH_MEDIA"})
    );
}

#[test]
fn hangup_is_the_documented_media_websocket_command() {
    assert_eq!(
        MediaWebSocketAdapter::hangup_command(),
        Message::Text(json!({"command": "HANGUP"}).to_string().into())
    );
}

#[test]
fn malformed_or_cross_session_messages_fail_closed() {
    let cases = [
        (
            Message::Binary(vec![0, 0].into()),
            "MEDIA_START must arrive first",
        ),
        (
            text(json!({"event": "UNKNOWN"})),
            "unsupported media WebSocket event",
        ),
        (Message::Text("MEDIA_XOFF".into()), "valid JSON object"),
    ];
    for (message, expected) in cases {
        let mut adapter = MediaWebSocketAdapter::default();
        let error = adapter.handle_message(message, 2_000).unwrap_err();
        assert!(error.to_string().contains(expected), "{error}");
    }

    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);
    let error = adapter
        .handle_message(
            text(json!({"event": "MEDIA_XOFF", "channel_id": "other"})),
            2_060,
        )
        .unwrap_err();
    assert!(error.to_string().contains("channel_id does not match"));
}

#[test]
fn oversized_text_control_is_rejected_before_json_parsing() {
    let mut adapter = MediaWebSocketAdapter::default();
    let oversized = "x".repeat(MAX_MEDIA_WEBSOCKET_CONTROL_BYTES + 1);

    let error = adapter
        .handle_message(Message::Text(oversized.into()), 1_000)
        .unwrap_err();

    assert!(error
        .to_string()
        .contains("control exceeds Asterisk maximum"));
}

#[test]
fn oversized_binary_media_and_unknown_mark_are_rejected() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);
    let error = adapter
        .handle_message(
            Message::Binary(vec![0; MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES + 1].into()),
            2_070,
        )
        .unwrap_err();
    assert!(error.to_string().contains("exceeds Asterisk maximum"));

    let error = adapter
        .handle_message(
            text(json!({
                "event": "MEDIA_MARK_PROCESSED",
                "channel_id": CHANNEL_ID,
                "correlation_id": "unknown"
            })),
            2_080,
        )
        .unwrap_err();
    assert!(error.to_string().contains("unknown media mark"));
}

#[test]
fn xoff_blocks_outbound_media_until_xon() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);
    adapter
        .handle_message(
            text(json!({"event": "MEDIA_XOFF", "channel_id": CHANNEL_ID})),
            2_090,
        )
        .unwrap();

    let error = adapter.media_command(vec![1, 2]).unwrap_err();
    assert!(error.to_string().contains("paused by MEDIA_XOFF"));

    adapter
        .handle_message(
            text(json!({"event": "MEDIA_XON", "channel_id": CHANNEL_ID})),
            2_100,
        )
        .unwrap();
    assert!(matches!(
        adapter.media_command(vec![1, 2]).unwrap(),
        Message::Binary(_)
    ));
}

#[test]
fn close_maps_to_session_ended_and_closes_the_state_machine() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);

    let output = adapter.handle_message(Message::Close(None), 2_110).unwrap();
    let Some(MediaWebSocketOutput::Control(message)) = output else {
        panic!("close must produce session.ended")
    };
    assert_eq!(message.message_type(), "session.ended");
    assert_eq!(message.as_value()["reason"], "media_websocket_closed");

    let error = adapter
        .handle_message(Message::Ping(Vec::new().into()), 2_120)
        .unwrap_err();
    assert!(error.to_string().contains("message received after close"));
}

#[test]
fn duplicate_start_and_non_fixture_codecs_are_rejected() {
    let mut adapter = MediaWebSocketAdapter::default();
    started(&mut adapter);
    let error = adapter
        .handle_message(media_start("slin"), 2_130)
        .unwrap_err();
    assert!(error
        .to_string()
        .contains("MEDIA_START may only arrive once"));

    for codec in ["slin16", "ulaw", "alaw", "opus"] {
        let mut adapter = MediaWebSocketAdapter::default();
        let error = adapter
            .handle_message(media_start(codec), 2_140)
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("unsupported media WebSocket codec"),
            "{codec}: {error}"
        );
    }
}
