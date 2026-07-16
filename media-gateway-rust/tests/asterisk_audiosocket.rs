use lucy_media_gateway::asterisk::audiosocket::{
    encode_dtmf_frames, encode_hangup_frame, encode_slin8_frame, AdapterOutput, AudioSocketAdapter,
    SLIN8_SAMPLE_RATE_HZ,
};
use lucy_media_gateway::asterisk::media_plane::MediaPlaneEvent;
use lucy_media_gateway::asterisk::metrics::TransportMetricSample;
use tokio::io::{duplex, AsyncWriteExt};

const SESSION_UUID: [u8; 16] = [
    0x12, 0x34, 0x56, 0x78, 0x12, 0x34, 0x56, 0x78, 0x90, 0xab, 0xcd, 0xef, 0x12, 0x34, 0x56, 0x78,
];
const SESSION_ID: &str = "12345678-1234-5678-90ab-cdef12345678";

fn frame(kind: u8, payload: &[u8]) -> Vec<u8> {
    let length = u16::try_from(payload.len()).unwrap().to_be_bytes();
    let mut wire = vec![kind, length[0], length[1]];
    wire.extend_from_slice(payload);
    wire
}

async fn stream(wire: Vec<u8>) -> tokio::io::DuplexStream {
    let (reader, mut writer) = duplex(wire.len().max(1));
    writer.write_all(&wire).await.unwrap();
    writer.shutdown().await.unwrap();
    reader
}

async fn consume_uuid(adapter: &mut AudioSocketAdapter, reader: &mut tokio::io::DuplexStream) {
    let output = adapter.read_next(reader, 1_000).await.unwrap().unwrap();
    let AdapterOutput::Control(message) = output else {
        panic!("UUID must produce session.started")
    };
    assert_eq!(message.message_type(), "session.started");
    assert_eq!(message.as_value()["session_id"], SESSION_ID);
    assert_eq!(message.as_value()["transport"], "asterisk/audiosocket");
    assert_eq!(
        message.as_value()["codecs"],
        serde_json::json!(["slin/8000"])
    );
}

#[tokio::test]
async fn uuid_must_be_first_and_starts_control_session() {
    let mut adapter = AudioSocketAdapter::default();
    let mut reader = stream(frame(0x01, &SESSION_UUID)).await;

    consume_uuid(&mut adapter, &mut reader).await;
    assert!(adapter
        .read_next(&mut reader, 1_001)
        .await
        .unwrap()
        .is_none());
}

#[tokio::test]
async fn slin8_audio_stays_an_internal_media_frame() {
    let mut wire = frame(0x01, &SESSION_UUID);
    wire.extend(frame(0x10, &[0x01, 0x00, 0xff, 0x7f]));
    let mut reader = stream(wire).await;
    let mut adapter = AudioSocketAdapter::default();
    consume_uuid(&mut adapter, &mut reader).await;

    let output = adapter
        .read_next(&mut reader, 1_020)
        .await
        .unwrap()
        .unwrap();
    let AdapterOutput::Media(media) = output else {
        panic!("raw audio must not become a control message")
    };
    assert_eq!(media.session_id, SESSION_ID);
    assert_eq!(media.sample_rate_hz, SLIN8_SAMPLE_RATE_HZ);
    assert_eq!(media.pcm_s16le, [0x01, 0x00, 0xff, 0x7f]);
}

#[tokio::test]
async fn dtmf_maps_to_the_locked_control_schema() {
    let mut wire = frame(0x01, &SESSION_UUID);
    wire.extend(frame(0x03, b"#"));
    let mut reader = stream(wire).await;
    let mut adapter = AudioSocketAdapter::default();
    consume_uuid(&mut adapter, &mut reader).await;

    let output = adapter
        .read_next(&mut reader, 1_030)
        .await
        .unwrap()
        .unwrap();
    let AdapterOutput::Control(message) = output else {
        panic!("DTMF must produce a control message")
    };
    assert_eq!(message.message_type(), "dtmf");
    assert_eq!(message.as_value()["digit"], "#");
    assert_eq!(message.as_value()["session_id"], SESSION_ID);
}

#[tokio::test]
async fn transport_metrics_are_schema_valid_and_tied_to_the_latest_real_turn() {
    let mut adapter = AudioSocketAdapter::default();
    let mut reader = stream(frame(0x01, &SESSION_UUID)).await;
    consume_uuid(&mut adapter, &mut reader).await;
    assert!(adapter
        .transport_metrics(TransportMetricSample::tcp_websocket(7.0, 3.0), 1_100)
        .unwrap()
        .is_none());

    adapter
        .media_plane_event(MediaPlaneEvent::SpeechStarted { at_ms: 1_010 })
        .unwrap();
    adapter
        .media_plane_event(MediaPlaneEvent::TranscriptFinal {
            text: "hello".to_string(),
            stt_ms: 4,
            at_ms: 1_020,
            provider: "fixture",
        })
        .unwrap();
    let Some(AdapterOutput::Control(message)) = adapter
        .transport_metrics(TransportMetricSample::tcp_websocket(7.0, 3.0), 1_100)
        .unwrap()
    else {
        panic!("a completed turn must receive the metric sample")
    };
    assert_eq!(message.message_type(), "transport.metrics");
    assert_eq!(message.as_value()["turn_id"], "turn-1");
    assert_eq!(message.as_value()["rtt_ms"], 7.0);
    assert_eq!(message.as_value()["jitter_ms"], 3.0);
    assert_eq!(message.as_value()["packet_loss"], 0.0);
}

#[tokio::test]
async fn hangup_maps_to_session_ended() {
    let mut wire = frame(0x01, &SESSION_UUID);
    wire.extend(frame(0x00, &[]));
    let mut reader = stream(wire).await;
    let mut adapter = AudioSocketAdapter::default();
    consume_uuid(&mut adapter, &mut reader).await;

    let output = adapter
        .read_next(&mut reader, 1_040)
        .await
        .unwrap()
        .unwrap();
    let AdapterOutput::Control(message) = output else {
        panic!("hangup must produce a control message")
    };
    assert_eq!(message.message_type(), "session.ended");
    assert_eq!(message.as_value()["reason"], "remote_hangup");
}

#[tokio::test]
async fn a_non_uuid_first_frame_is_rejected() {
    let mut adapter = AudioSocketAdapter::default();
    let mut reader = stream(frame(0x03, b"1")).await;

    let error = adapter.read_next(&mut reader, 1_000).await.unwrap_err();

    assert!(error.to_string().contains("first frame must be UUID"));
}

#[tokio::test]
async fn malformed_frames_fail_closed() {
    let cases = [
        (frame(0x01, &[0; 15]), "UUID payload must be 16 bytes"),
        (
            {
                let mut wire = frame(0x01, &SESSION_UUID);
                wire.extend(frame(0x10, &[0x00]));
                wire
            },
            "audio payload must contain complete 16-bit samples",
        ),
        (
            {
                let mut wire = frame(0x01, &SESSION_UUID);
                wire.extend([0x03, 0x00, 0x02, b'1']);
                wire
            },
            "truncated AudioSocket payload",
        ),
    ];

    for (wire, expected) in cases {
        let mut adapter = AudioSocketAdapter::default();
        let mut reader = stream(wire).await;
        let first = adapter.read_next(&mut reader, 1_000).await;
        let error = match first {
            Err(error) => error,
            Ok(Some(_)) => adapter.read_next(&mut reader, 1_001).await.unwrap_err(),
            Ok(None) => panic!("expected malformed frame"),
        };
        assert!(error.to_string().contains(expected), "{error}");
    }
}

#[tokio::test]
async fn invalid_protocol_transitions_fail_closed() {
    let cases = [
        (
            frame(0x01, &SESSION_UUID),
            "UUID frame may only appear once",
        ),
        (frame(0x03, b"x"), "DTMF payload must be one valid digit"),
        (frame(0x00, b"x"), "hangup payload must be empty"),
        (frame(0x7f, &[]), "unsupported AudioSocket frame type"),
        (
            frame(0xff, &[]),
            "Asterisk reported an AudioSocket protocol error",
        ),
    ];

    for (invalid_frame, expected) in cases {
        let mut wire = frame(0x01, &SESSION_UUID);
        wire.extend(invalid_frame);
        let mut reader = stream(wire).await;
        let mut adapter = AudioSocketAdapter::default();
        consume_uuid(&mut adapter, &mut reader).await;

        let error = adapter.read_next(&mut reader, 1_001).await.unwrap_err();

        assert!(error.to_string().contains(expected), "{error}");
    }
}

#[tokio::test]
async fn truncated_header_is_rejected() {
    let mut adapter = AudioSocketAdapter::default();
    let mut reader = stream(vec![0x01, 0x00]).await;

    let error = adapter.read_next(&mut reader, 1_000).await.unwrap_err();

    assert!(error.to_string().contains("truncated AudioSocket header"));
}

#[tokio::test]
async fn frames_after_hangup_are_rejected_even_when_the_stream_closes() {
    let mut wire = frame(0x01, &SESSION_UUID);
    wire.extend(frame(0x00, &[]));
    let mut reader = stream(wire).await;
    let mut adapter = AudioSocketAdapter::default();
    consume_uuid(&mut adapter, &mut reader).await;
    adapter
        .read_next(&mut reader, 1_001)
        .await
        .unwrap()
        .unwrap();

    let error = adapter.read_next(&mut reader, 1_002).await.unwrap_err();

    assert!(error.to_string().contains("frame received after hangup"));
}

#[test]
fn downstream_audio_dtmf_and_hangup_encode_as_audio_socket_frames() {
    assert_eq!(
        encode_slin8_frame(&[1, 0, 2, 0]).unwrap(),
        frame(0x10, &[1, 0, 2, 0])
    );
    assert_eq!(
        encode_dtmf_frames("1#").unwrap(),
        [frame(0x03, b"1"), frame(0x03, b"#")].concat()
    );
    assert_eq!(encode_hangup_frame(), frame(0x00, &[]));

    assert!(encode_slin8_frame(&[1]).is_err());
    assert!(encode_dtmf_frames("1X").is_err());
}
