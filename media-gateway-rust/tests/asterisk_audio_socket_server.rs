use futures_util::{SinkExt, StreamExt};
use lucy_media_gateway::asterisk::media_plane::FixtureMediaPlaneConfig;
use lucy_media_gateway::asterisk::server::{
    handle_audio_socket_connection, handle_audio_socket_connection_with_config,
    AudioSocketServerConfig, GatewayMetrics,
};
use serde_json::{json, Value};
use std::net::{Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;
use tokio_tungstenite::{
    accept_async, accept_hdr_async,
    tungstenite::{
        handshake::server::{Request, Response},
        Message,
    },
};

const SESSION_UUID: [u8; 16] = [
    0x12, 0x34, 0x56, 0x78, 0x12, 0x34, 0x56, 0x78, 0x90, 0xab, 0xcd, 0xef, 0x12, 0x34, 0x56, 0x78,
];
const SESSION_ID: &str = "12345678-1234-5678-90ab-cdef12345678";
const CONTROL_TOKEN: &str = "test-control-token";

fn media_fixture(pacing_ms: u64) -> FixtureMediaPlaneConfig {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/audio");
    FixtureMediaPlaneConfig {
        playback_wav: std::env::var("LUCY_AUDIO_FIXTURE_WAV")
            .map(PathBuf::from)
            .unwrap_or_else(|_| root.join("booking_caller_8k.wav")),
        transcript_timeline: std::env::var("LUCY_AUDIO_FIXTURE_TIMELINE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| root.join("booking_caller.timeline.json")),
        transcript_trigger_bytes: 4,
        playback_chunk_bytes: 640,
        playback_pacing_ms: pacing_ms,
    }
}

fn frame(kind: u8, payload: &[u8]) -> Vec<u8> {
    let length = u16::try_from(payload.len()).unwrap().to_be_bytes();
    let mut wire = vec![kind, length[0], length[1]];
    wire.extend_from_slice(payload);
    wire
}

#[tokio::test]
async fn real_audio_socket_forwards_only_control_and_counts_media() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(stream, |request: &Request, response: Response| {
            assert_eq!(
                request.headers().get("authorization").unwrap(),
                format!("Bearer {CONTROL_TOKEN}").as_str()
            );
            Ok(response)
        })
        .await
        .unwrap();
        let mut messages = Vec::new();
        while let Some(message) = socket.next().await {
            match message.unwrap() {
                Message::Text(text) => {
                    let value: Value = serde_json::from_str(text.as_ref()).unwrap();
                    if messages.is_empty() {
                        socket
                            .send(Message::Text(
                                json!({
                                    "v": 1,
                                    "type": "session.configure",
                                    "session_id": value["session_id"],
                                    "seq": 0,
                                    "ts_ms": 0,
                                    "stt": "gateway",
                                    "tts": "gateway",
                                    "vad": "gateway"
                                })
                                .to_string()
                                .into(),
                            ))
                            .await
                            .unwrap();
                    }
                    let ended = value["type"] == "session.ended";
                    messages.push(value);
                    if ended {
                        break;
                    }
                }
                Message::Close(_) => break,
                _ => {}
            }
        }
        messages
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        let mut timestamp = 1_000_u64;
        handle_audio_socket_connection(
            stream,
            format!("ws://{control_address}"),
            CONTROL_TOKEN.to_string(),
            handler_metrics,
            move || {
                timestamp += 10;
                timestamp
            },
        )
        .await
    });

    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    let mut wire = frame(0x01, &SESSION_UUID);
    wire.extend(frame(0x10, &[1, 0, 2, 0]));
    asterisk.write_all(&wire).await.unwrap();
    asterisk.shutdown().await.unwrap();

    handler.await.unwrap().unwrap();
    let messages = control_server.await.unwrap();
    assert_eq!(
        messages
            .iter()
            .map(|message| message["type"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["session.started", "session.ended"]
    );
    assert!(messages
        .iter()
        .all(|message| message.get("audio").is_none()));

    let snapshot = metrics.snapshot();
    assert_eq!(snapshot.asterisk_sessions_started, 1);
    assert_eq!(snapshot.asterisk_sessions_completed, 1);
    assert_eq!(snapshot.asterisk_sessions_failed, 0);
    assert_eq!(snapshot.asterisk_audio_bytes_received, 4);
    assert_eq!(snapshot.control_messages_forwarded, 2);
}

#[tokio::test]
async fn recorded_provider_transcribes_and_streams_pcm_back_to_asterisk() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (playback_finished_tx, playback_finished_rx) = oneshot::channel();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut types = Vec::new();
        let mut playback_finished_tx = Some(playback_finished_tx);
        while let Some(message) = socket.next().await {
            let Message::Text(text) = message.unwrap() else {
                continue;
            };
            let value: Value = serde_json::from_str(text.as_ref()).unwrap();
            let kind = value["type"].as_str().unwrap().to_string();
            types.push(kind.clone());
            if kind == "stt.final" {
                for (seq, utterance_id, text, flush) in [
                    (1, "utt-1", "Hello from Lucy.", false),
                    (2, "utt-2", "How can I help?", true),
                ] {
                    socket
                        .send(Message::Text(
                            json!({
                                "v": 1,
                                "type": "tts.speak",
                                "session_id": SESSION_ID,
                                "turn_id": value["turn_id"],
                                "seq": seq,
                                "ts_ms": 1_800 + seq,
                                "utterance_id": utterance_id,
                                "text": text,
                                "flush": flush
                            })
                            .to_string()
                            .into(),
                        ))
                        .await
                        .unwrap();
                }
            }
            if kind == "tts.playback"
                && value["state"] == "finished"
                && value["utterance_id"] == "utt-2"
            {
                playback_finished_tx.take().unwrap().send(()).unwrap();
            }
            if kind == "session.ended" {
                break;
            }
        }
        types
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let mut config = AudioSocketServerConfig::new(
        "127.0.0.1:0",
        &format!("ws://{control_address}"),
        CONTROL_TOKEN,
    )
    .unwrap();
    config.media_plane = Some(media_fixture(1));
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(stream, config, handler_metrics, || 1_000).await
    });

    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    let mut incoming = frame(0x01, &SESSION_UUID);
    incoming.extend(frame(0x10, &[1, 0, 2, 0]));
    asterisk.write_all(&incoming).await.unwrap();
    let clause_bytes = hound::WavReader::open(media_fixture(1).playback_wav)
        .unwrap()
        .duration() as usize
        * 2;
    let expected_bytes = clause_bytes * 2;
    let mut received_pcm = Vec::new();
    while received_pcm.len() < expected_bytes {
        let mut header = [0_u8; 3];
        asterisk.read_exact(&mut header).await.unwrap();
        assert_eq!(header[0], 0x10);
        let length = usize::from(u16::from_be_bytes([header[1], header[2]]));
        let mut payload = vec![0_u8; length];
        asterisk.read_exact(&mut payload).await.unwrap();
        received_pcm.extend(payload);
    }
    playback_finished_rx.await.unwrap();
    asterisk.write_all(&frame(0x00, &[])).await.unwrap();

    handler.await.unwrap().unwrap();
    let types = control_server.await.unwrap();
    assert_eq!(
        types,
        [
            "session.started",
            "vad.speech_start",
            "stt.partial",
            "vad.speech_end",
            "stt.final",
            "transport.metrics",
            "tts.playback",
            "tts.playback",
            "tts.playback",
            "tts.playback",
            "session.ended",
        ]
    );
    assert_eq!(received_pcm.len(), expected_bytes);
    assert!(types.contains(&"transport.metrics".to_string()));
    let snapshot = metrics.snapshot();
    assert_eq!(snapshot.asterisk_audio_bytes_received, 4);
    assert_eq!(snapshot.asterisk_audio_bytes_sent, expected_bytes as u64);
}

#[tokio::test]
async fn control_cancel_flushes_recorded_playback_before_the_next_tick() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (first_audio_tx, first_audio_rx) = oneshot::channel();
    let (flushed_tx, flushed_rx) = oneshot::channel();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut first_audio_rx = Some(first_audio_rx);
        let mut flushed_tx = Some(flushed_tx);
        let mut types = Vec::new();
        while let Some(message) = socket.next().await {
            let Message::Text(text) = message.unwrap() else {
                continue;
            };
            let value: Value = serde_json::from_str(text.as_ref()).unwrap();
            let kind = value["type"].as_str().unwrap().to_string();
            types.push((kind.clone(), value["state"].as_str().map(str::to_string)));
            if kind == "session.started" {
                for (seq, utterance_id, text) in [
                    (1, "utt-cancel", "This playback must be interrupted."),
                    (2, "utt-queued", "This queued clause must never start."),
                ] {
                    socket
                        .send(Message::Text(
                            json!({
                                "v": 1,
                                "type": "tts.speak",
                                "session_id": SESSION_ID,
                                "seq": seq,
                                "ts_ms": 1_000 + seq,
                                "utterance_id": utterance_id,
                                "text": text,
                                "flush": true
                            })
                            .to_string()
                            .into(),
                        ))
                        .await
                        .unwrap();
                }
            }
            if kind == "tts.playback" && value["state"] == "started" {
                first_audio_rx.take().unwrap().await.unwrap();
                socket
                    .send(Message::Text(
                        json!({
                            "v": 1,
                            "type": "tts.cancel",
                            "session_id": SESSION_ID,
                            "seq": 3,
                            "ts_ms": 1_003,
                            "utterance_id": "all"
                        })
                        .to_string()
                        .into(),
                    ))
                    .await
                    .unwrap();
            }
            if kind == "tts.playback" && value["state"] == "flushed" {
                flushed_tx.take().unwrap().send(()).unwrap();
            }
            if kind == "session.ended" {
                break;
            }
        }
        types
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let mut config = AudioSocketServerConfig::new(
        "127.0.0.1:0",
        &format!("ws://{control_address}"),
        CONTROL_TOKEN,
    )
    .unwrap();
    config.media_plane = Some(media_fixture(100));
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(stream, config, handler_metrics, || 1_000).await
    });

    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    asterisk
        .write_all(&frame(0x01, &SESSION_UUID))
        .await
        .unwrap();
    let mut header = [0_u8; 3];
    asterisk.read_exact(&mut header).await.unwrap();
    assert_eq!(header[0], 0x10);
    let length = usize::from(u16::from_be_bytes([header[1], header[2]]));
    let mut first_pcm = vec![0_u8; length];
    asterisk.read_exact(&mut first_pcm).await.unwrap();
    first_audio_tx.send(()).unwrap();
    flushed_rx.await.unwrap();
    asterisk.write_all(&frame(0x00, &[])).await.unwrap();

    handler.await.unwrap().unwrap();
    let types = control_server.await.unwrap();
    assert_eq!(
        types
            .iter()
            .filter(|event| event.0 == "tts.playback" && event.1.as_deref() == Some("started"))
            .count(),
        1
    );
    assert!(types.contains(&("tts.playback".to_string(), Some("flushed".to_string()))));
    let sent = metrics.snapshot().asterisk_audio_bytes_sent;
    assert!(sent > 0);
    assert!(
        sent < hound::WavReader::open(media_fixture(1).playback_wav)
            .unwrap()
            .duration() as u64
            * 2
    );
}

#[tokio::test]
async fn selective_cancel_flushes_only_the_queued_clause_and_preserves_order() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (last_playback_tx, last_playback_rx) = oneshot::channel();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut events = Vec::new();
        let mut last_playback_tx = Some(last_playback_tx);
        while let Some(message) = socket.next().await {
            let Message::Text(text) = message.unwrap() else {
                continue;
            };
            let value: Value = serde_json::from_str(text.as_ref()).unwrap();
            let kind = value["type"].as_str().unwrap();
            if kind == "session.started" {
                for (seq, utterance_id, text) in [
                    (1, "utt-active", "This clause remains active."),
                    (2, "utt-cancelled", "This queued clause is cancelled."),
                    (3, "utt-last", "This clause still plays last."),
                ] {
                    socket
                        .send(Message::Text(
                            json!({
                                "v": 1,
                                "type": "tts.speak",
                                "session_id": SESSION_ID,
                                "seq": seq,
                                "ts_ms": 1_000 + seq,
                                "utterance_id": utterance_id,
                                "text": text,
                                "flush": true
                            })
                            .to_string()
                            .into(),
                        ))
                        .await
                        .unwrap();
                }
                socket
                    .send(Message::Text(
                        json!({
                            "v": 1,
                            "type": "tts.cancel",
                            "session_id": SESSION_ID,
                            "seq": 4,
                            "ts_ms": 1_004,
                            "utterance_id": "utt-cancelled"
                        })
                        .to_string()
                        .into(),
                    ))
                    .await
                    .unwrap();
            }
            if kind == "tts.playback" {
                events.push((
                    value["utterance_id"].as_str().unwrap().to_string(),
                    value["state"].as_str().unwrap().to_string(),
                ));
                if value["utterance_id"] == "utt-last" && value["state"] == "finished" {
                    last_playback_tx.take().unwrap().send(()).unwrap();
                }
            }
            if kind == "session.ended" {
                break;
            }
        }
        events
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let mut config = AudioSocketServerConfig::new(
        "127.0.0.1:0",
        &format!("ws://{control_address}"),
        CONTROL_TOKEN,
    )
    .unwrap();
    config.media_plane = Some(media_fixture(1));
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(stream, config, handler_metrics, || 1_000).await
    });

    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    asterisk
        .write_all(&frame(0x01, &SESSION_UUID))
        .await
        .unwrap();
    let clause_bytes = hound::WavReader::open(media_fixture(1).playback_wav)
        .unwrap()
        .duration() as usize
        * 2;
    let mut received_pcm = Vec::new();
    while received_pcm.len() < clause_bytes * 2 {
        let mut header = [0_u8; 3];
        asterisk.read_exact(&mut header).await.unwrap();
        assert_eq!(header[0], 0x10);
        let length = usize::from(u16::from_be_bytes([header[1], header[2]]));
        let mut payload = vec![0_u8; length];
        asterisk.read_exact(&mut payload).await.unwrap();
        received_pcm.extend(payload);
    }
    last_playback_rx.await.unwrap();
    asterisk.write_all(&frame(0x00, &[])).await.unwrap();

    handler.await.unwrap().unwrap();
    let events = control_server.await.unwrap();
    assert_eq!(
        events,
        [
            ("utt-active".to_string(), "started".to_string()),
            ("utt-cancelled".to_string(), "flushed".to_string()),
            ("utt-active".to_string(), "finished".to_string()),
            ("utt-last".to_string(), "started".to_string()),
            ("utt-last".to_string(), "finished".to_string()),
        ]
    );
    assert_eq!(received_pcm.len(), clause_bytes * 2);
}

#[test]
fn audio_socket_server_config_validates_typed_endpoints() {
    let config = AudioSocketServerConfig::new(
        "0.0.0.0:9092",
        "ws://127.0.0.1:8000/v1/session/ws",
        CONTROL_TOKEN,
    )
    .unwrap();
    assert_eq!(config.bind_address, SocketAddr::from(([0, 0, 0, 0], 9092)));

    for (bind, control, expected) in [
        ("invalid", "ws://localhost/session", "bind address"),
        ("127.0.0.1:9092", "http://localhost/session", "ws or wss"),
        (
            "127.0.0.1:9092",
            "ws://user:secret@localhost/session",
            "credentials",
        ),
        (
            "127.0.0.1:9092",
            "ws://control.example.com/session",
            "must use wss",
        ),
    ] {
        let error = AudioSocketServerConfig::new(bind, control, CONTROL_TOKEN).unwrap_err();
        assert!(error.to_string().contains(expected), "{error}");
    }
}

async fn rejected_directive_error(directives: Vec<Value>) -> (String, GatewayMetrics) {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Text(_)
        ));
        for directive in directives {
            socket
                .send(Message::Text(directive.to_string().into()))
                .await
                .unwrap();
        }
        while socket.next().await.is_some() {}
    });
    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection(
            stream,
            format!("ws://{control_address}"),
            CONTROL_TOKEN.to_string(),
            handler_metrics,
            || 1_000,
        )
        .await
    });
    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    asterisk
        .write_all(&frame(0x01, &SESSION_UUID))
        .await
        .unwrap();
    let error = handler.await.unwrap().unwrap_err().to_string();
    drop(asterisk);
    control_server.await.unwrap();
    (error, metrics)
}

#[tokio::test]
async fn stale_cross_session_and_wrong_version_directives_fail_closed() {
    let cases = [
        (
            vec![json!({
                "v": 2,
                "type": "dtmf.send",
                "session_id": SESSION_ID,
                "seq": 1,
                "ts_ms": 1_000,
                "digits": "1"
            })],
            "schema version",
        ),
        (
            vec![json!({
                "v": 1,
                "type": "dtmf.send",
                "session_id": "another-session",
                "seq": 1,
                "ts_ms": 1_000,
                "digits": "1"
            })],
            "session_id",
        ),
        (
            vec![
                json!({
                    "v": 1,
                    "type": "session.configure",
                    "session_id": SESSION_ID,
                    "seq": 1,
                    "ts_ms": 1_000,
                    "stt": "gateway",
                    "tts": "gateway",
                    "vad": "gateway"
                }),
                json!({
                    "v": 1,
                    "type": "dtmf.send",
                    "session_id": SESSION_ID,
                    "seq": 1,
                    "ts_ms": 1_001,
                    "digits": "1"
                }),
            ],
            "increase monotonically",
        ),
    ];

    for (directives, expected) in cases {
        let (error, metrics) = rejected_directive_error(directives).await;
        assert!(error.contains(expected), "{error}");
        assert_eq!(metrics.snapshot().asterisk_sessions_failed, 1);
    }
}

#[tokio::test(start_paused = true)]
async fn handshake_and_idle_deadlines_close_stalled_peers() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (started_tx, started_rx) = oneshot::channel();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Text(_)
        ));
        started_tx.send(()).unwrap();
        while socket.next().await.is_some() {}
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let mut config = AudioSocketServerConfig::new(
        "127.0.0.1:0",
        &format!("ws://{control_address}"),
        CONTROL_TOKEN,
    )
    .unwrap();
    config.handshake_timeout = std::time::Duration::from_millis(10);
    config.idle_timeout = std::time::Duration::from_millis(50);
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(stream, config, handler_metrics, || 1_000).await
    });
    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    asterisk
        .write_all(&frame(0x01, &SESSION_UUID))
        .await
        .unwrap();
    started_rx.await.unwrap();
    tokio::task::yield_now().await;
    tokio::time::advance(std::time::Duration::from_millis(200)).await;
    for _ in 0..3 {
        tokio::task::yield_now().await;
    }
    assert!(
        handler.is_finished(),
        "playback ticks must not postpone the AudioSocket idle deadline"
    );
    let error = handler.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("idle timeout"), "{error}");
    assert_eq!(metrics.snapshot().asterisk_sessions_failed, 1);
    control_server.await.unwrap();

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let mut config =
        AudioSocketServerConfig::new("127.0.0.1:0", "ws://127.0.0.1:9", CONTROL_TOKEN).unwrap();
    config.handshake_timeout = std::time::Duration::from_millis(10);
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(stream, config, handler_metrics, || 1_000).await
    });
    let _stalled = TcpStream::connect(audio_address).await.unwrap();
    tokio::time::advance(std::time::Duration::from_millis(11)).await;
    let error = handler.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("handshake timed out"), "{error}");
    assert_eq!(metrics.snapshot().asterisk_sessions_failed, 1);
}

#[tokio::test]
async fn audio_socket_peer_allowlist_is_enforced_before_control_connect() {
    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let mut config =
        AudioSocketServerConfig::new("127.0.0.1:0", "ws://127.0.0.1:9", CONTROL_TOKEN).unwrap();
    config.allowed_peer_cidrs = Arc::from(["192.0.2.0/24".parse().unwrap()]);
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(stream, config, handler_metrics, || 1_000).await
    });
    let _peer = TcpStream::connect(audio_address).await.unwrap();

    let error = handler.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("allowlist"), "{error}");
    assert_eq!(metrics.snapshot().asterisk_sessions_failed, 1);
}

#[tokio::test]
async fn control_directives_send_dtmf_and_hangup_back_to_asterisk() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control_server = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let started = socket.next().await.unwrap().unwrap();
        let Message::Text(started) = started else {
            panic!("session.started must be text")
        };
        let started: Value = serde_json::from_str(started.as_ref()).unwrap();
        let session_id = &started["session_id"];
        for directive in [
            json!({
                "v": 1,
                "type": "dtmf.send",
                "session_id": session_id,
                "seq": 1,
                "ts_ms": 1_010,
                "digits": "5"
            }),
            json!({
                "v": 1,
                "type": "session.end",
                "session_id": session_id,
                "seq": 2,
                "ts_ms": 1_020,
                "reason": "completed"
            }),
        ] {
            socket
                .send(Message::Text(directive.to_string().into()))
                .await
                .unwrap();
        }
        let ended = socket.next().await.unwrap().unwrap();
        let Message::Text(ended) = ended else {
            panic!("session.ended must be text")
        };
        serde_json::from_str::<Value>(ended.as_ref()).unwrap()
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection(
            stream,
            format!("ws://{control_address}"),
            CONTROL_TOKEN.to_string(),
            handler_metrics,
            || 1_030,
        )
        .await
    });

    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    asterisk
        .write_all(&frame(0x01, &SESSION_UUID))
        .await
        .unwrap();
    let mut commands = [0_u8; 7];
    tokio::time::timeout(
        std::time::Duration::from_secs(1),
        asterisk.read_exact(&mut commands),
    )
    .await
    .unwrap()
    .unwrap();

    assert_eq!(commands, [0x03, 0, 1, b'5', 0x00, 0, 0]);
    handler.await.unwrap().unwrap();
    let ended = control_server.await.unwrap();
    assert_eq!(ended["type"], "session.ended");
    assert_eq!(ended["reason"], "completed");
    let snapshot = metrics.snapshot();
    assert_eq!(snapshot.asterisk_sessions_completed, 1);
    assert_eq!(snapshot.asterisk_sessions_failed, 0);
}

#[tokio::test]
async fn fragmented_audio_socket_frame_survives_a_control_directive_race() {
    let (send_directive, receive_directive) = oneshot::channel();
    let (directive_processed, processed) = oneshot::channel();
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control_server = tokio::spawn(async move {
        let mut directive_processed = Some(directive_processed);
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let started = socket.next().await.unwrap().unwrap();
        assert!(matches!(started, Message::Text(_)));
        receive_directive.await.unwrap();
        socket
            .send(Message::Text(
                json!({
                    "v": 1,
                    "type": "session.configure",
                    "session_id": "12345678-1234-5678-90ab-cdef12345678",
                    "seq": 0,
                    "ts_ms": 0,
                    "stt": "gateway",
                    "tts": "gateway",
                    "vad": "gateway"
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
        socket
            .send(Message::Ping(b"barrier".to_vec().into()))
            .await
            .unwrap();
        while let Some(message) = socket.next().await {
            match message.unwrap() {
                Message::Pong(payload) if payload.as_ref() == b"barrier" => {
                    directive_processed.take().unwrap().send(()).unwrap();
                }
                Message::Text(text) => {
                    let value: Value = serde_json::from_str(text.as_ref()).unwrap();
                    if value["type"] == "session.ended" {
                        return value;
                    }
                }
                _ => {}
            }
        }
        panic!("control channel closed before session.ended")
    });

    let audio_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let audio_address = audio_listener.local_addr().unwrap();
    let metrics = GatewayMetrics::default();
    let handler_metrics = metrics.clone();
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection(
            stream,
            format!("ws://{control_address}"),
            CONTROL_TOKEN.to_string(),
            handler_metrics,
            || 2_000,
        )
        .await
    });

    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    let mut uuid_and_partial_media = frame(0x01, &SESSION_UUID);
    uuid_and_partial_media.extend([0x10, 0x00]);
    asterisk.write_all(&uuid_and_partial_media).await.unwrap();
    send_directive.send(()).unwrap();
    tokio::time::timeout(std::time::Duration::from_secs(1), processed)
        .await
        .unwrap()
        .unwrap();
    let remainder = [0x04, 1, 0, 2, 0];
    asterisk.write_all(&remainder).await.unwrap();
    asterisk.shutdown().await.unwrap();

    handler.await.unwrap().unwrap();
    assert_eq!(control_server.await.unwrap()["type"], "session.ended");
    let snapshot = metrics.snapshot();
    assert_eq!(snapshot.asterisk_audio_bytes_received, 4);
    assert_eq!(snapshot.asterisk_sessions_completed, 1);
    assert_eq!(snapshot.asterisk_sessions_failed, 0);
}
