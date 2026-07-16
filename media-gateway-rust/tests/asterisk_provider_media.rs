use futures_util::{SinkExt, StreamExt};
use lucy_media_gateway::asterisk::media_plane::{MediaPlaneError, MediaPlaneEvent, PlaybackStep};
use lucy_media_gateway::asterisk::provider_media::{
    DeepgramConfig, ElevenLabsConfig, ProviderMediaConfig, ProviderMediaPlane,
};
use lucy_media_gateway::asterisk::runtime::{
    serve_media_websocket_connection, MediaWebSocketRuntimeConfig,
};
use lucy_media_gateway::asterisk::server::{
    handle_audio_socket_connection_with_config, AudioSocketServerConfig, GatewayMetrics,
};
use serde_json::{json, Value};
use std::{fs, net::Ipv4Addr, path::PathBuf, time::Duration};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::oneshot,
    task::JoinHandle,
};
use tokio_tungstenite::{
    accept_async, accept_hdr_async,
    tungstenite::{
        handshake::server::{Request, Response},
        Message,
    },
};

const CONTROL_TOKEN: &str = "test-control-token";
const LOCAL_PROTOCOL_TIMEOUT: Duration = Duration::from_secs(5);
const MAX_PROVIDER_READINESS_POLLS: usize = 4_096;

fn received_fixture(path: &str) -> Vec<Value> {
    let root = std::env::var("LUCY_PROVIDER_FIXTURE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../packages"));
    fs::read_to_string(root.join(path))
        .unwrap()
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|frame| frame["direction"] == "received")
        .map(|frame| frame["payload"].clone())
        .collect()
}

const MAX_PROVIDER_TASK_POLLS: usize = 4_096;

fn corrupted_recorded_frame(path: &str) -> String {
    let mut serialized = received_fixture(path)
        .into_iter()
        .next()
        .expect("recorded provider fixture must contain a received frame")
        .to_string();
    serialized.pop();
    serialized
}

fn corrupted_recorded_audio_frame() -> Value {
    let mut frame = received_fixture("lucy-elevenlabs/tests/fixtures/tts_short_sentence.jsonl")
        .into_iter()
        .find(|frame| frame["audio"].is_object())
        .expect("recorded ElevenLabs fixture must contain an audio frame");
    frame["audio"]["b64"] = Value::String("not-base64".to_string());
    frame
}

fn local_config(
    deepgram_addr: std::net::SocketAddr,
    elevenlabs_addr: std::net::SocketAddr,
    idle_timeout: Duration,
) -> ProviderMediaConfig {
    local_config_with_timeouts(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
        idle_timeout,
    )
}

fn local_config_with_timeouts(
    deepgram_addr: std::net::SocketAddr,
    elevenlabs_addr: std::net::SocketAddr,
    connect_timeout: Duration,
    idle_timeout: Duration,
) -> ProviderMediaConfig {
    ProviderMediaConfig::local_protocol_test(
        DeepgramConfig::new(
            format!("ws://{deepgram_addr}/listen"),
            "nova-3",
            "stt-secret",
            16_000,
            640,
            300,
            connect_timeout,
            idle_timeout,
        ),
        ElevenLabsConfig::new(
            format!("ws://{elevenlabs_addr}"),
            "flash-v2.5",
            "voice-1",
            "tts-secret",
            "pcm_16000",
            16_000,
            connect_timeout,
            idle_timeout,
        ),
    )
    .unwrap()
}

async fn unused_listener() -> TcpListener {
    TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap()
}

#[tokio::test]
async fn provider_base_urls_reject_query_parameters_before_debug_output() {
    let config = ProviderMediaConfig::new(
        DeepgramConfig::new(
            "wss://deepgram.example/listen?token=secret",
            "nova-3",
            "stt-secret",
            16_000,
            640,
            300,
            LOCAL_PROTOCOL_TIMEOUT,
            LOCAL_PROTOCOL_TIMEOUT,
        ),
        ElevenLabsConfig::new(
            "wss://elevenlabs.example",
            "flash-v2.5",
            "voice-1",
            "tts-secret",
            "pcm_16000",
            16_000,
            LOCAL_PROTOCOL_TIMEOUT,
            LOCAL_PROTOCOL_TIMEOUT,
        ),
    );

    let error = config.unwrap_err();
    assert_eq!(
        error.to_string(),
        "Deepgram URL cannot contain credentials, query, or fragment"
    );
    assert!(!error.to_string().contains("secret"));
}

async fn wait_for_signal(receiver: &mut oneshot::Receiver<()>) {
    for _ in 0..MAX_PROVIDER_READINESS_POLLS {
        match receiver.try_recv() {
            Ok(()) => return,
            Err(oneshot::error::TryRecvError::Empty) => tokio::task::yield_now().await,
            Err(oneshot::error::TryRecvError::Closed) => {
                panic!("local protocol server closed before signaling readiness")
            }
        }
    }
    panic!(
        "local protocol server did not signal readiness within {MAX_PROVIDER_READINESS_POLLS} polls"
    );
}

async fn wait_for_realtime_signal(receiver: &mut oneshot::Receiver<()>) {
    match tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, receiver).await {
        Ok(Ok(())) => {}
        Ok(Err(_)) => panic!("local protocol server closed before signaling readiness"),
        Err(_) => panic!("local protocol server did not signal readiness before its deadline"),
    }
}

async fn wait_for_provider_error<T>(
    mut poll: impl FnMut() -> Result<T, MediaPlaneError>,
) -> MediaPlaneError {
    for _ in 0..MAX_PROVIDER_TASK_POLLS {
        if let Err(error) = poll() {
            return error;
        }
        tokio::task::yield_now().await;
    }
    panic!("provider task did not report its error within {MAX_PROVIDER_TASK_POLLS} polls");
}

fn audio_socket_frame(kind: u8, payload: &[u8]) -> Vec<u8> {
    let length = u16::try_from(payload.len()).unwrap().to_be_bytes();
    let mut frame = vec![kind, length[0], length[1]];
    frame.extend_from_slice(payload);
    frame
}

fn assert_control_messages_are_safe(messages: &[Value]) {
    for message in messages {
        let serialized = message.to_string();
        for forbidden in [
            "\"audio\"",
            "\"pcm\"",
            "\"b64\"",
            "\"bytes\"",
            "stt-secret",
            "tts-secret",
        ] {
            assert!(
                !serialized.contains(forbidden),
                "control message leaked sensitive content {forbidden}: {serialized}"
            );
        }
    }
}

fn assert_provider_error_is_redacted(error: &impl std::fmt::Display) {
    let serialized = error.to_string();
    for secret in ["stt-secret", "tts-secret"] {
        assert!(
            !serialized.contains(secret),
            "provider error leaked configured secret {secret}: {serialized}"
        );
    }
}

async fn replay_provider_servers() -> (ProviderMediaConfig, Vec<JoinHandle<()>>) {
    let deepgram = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let elevenlabs = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let config = local_config(
        deepgram.local_addr().unwrap(),
        elevenlabs.local_addr().unwrap(),
        LOCAL_PROTOCOL_TIMEOUT,
    );
    let stt_frames = received_fixture("lucy-deepgram/tests/fixtures/stt_short_utterance.jsonl");
    let tts_frames = received_fixture("lucy-elevenlabs/tests/fixtures/tts_short_sentence.jsonl");
    let stt = tokio::spawn(async move {
        let (stream, _) = deepgram.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        assert!(
            matches!(socket.next().await.unwrap().unwrap(), Message::Binary(bytes) if !bytes.is_empty())
        );
        for frame in stt_frames {
            socket
                .send(Message::Text(frame.to_string().into()))
                .await
                .unwrap();
        }
        while socket.next().await.is_some() {}
    });
    let tts = tokio::spawn(async move {
        let (stream, _) = elevenlabs.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        for _ in 0..3 {
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Text(_)
            ));
        }
        for frame in tts_frames {
            socket
                .send(Message::Text(frame.to_string().into()))
                .await
                .unwrap();
        }
    });
    (config, vec![stt, tts])
}

async fn join_servers(servers: Vec<JoinHandle<()>>) {
    for server in servers {
        tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, server)
            .await
            .unwrap()
            .unwrap();
    }
}

#[tokio::test]
async fn audio_socket_configured_provider_backend_replays_fixtures_and_keeps_control_audio_free() {
    const SESSION_UUID: [u8; 16] = [
        0x12, 0x34, 0x56, 0x78, 0x12, 0x34, 0x56, 0x78, 0x90, 0xab, 0xcd, 0xef, 0x12, 0x34, 0x56,
        0x78,
    ];
    const SESSION_ID: &str = "12345678-1234-5678-90ab-cdef12345678";
    let (provider_media, providers) = replay_provider_servers().await;
    let control_listener = unused_listener().await;
    let control_address = control_listener.local_addr().unwrap();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut messages = Vec::new();
        while let Some(message) = socket.next().await {
            let message = message.unwrap();
            if let Message::Ping(payload) = message {
                socket.send(Message::Pong(payload)).await.unwrap();
                continue;
            }
            let Message::Text(text) = message else {
                continue;
            };
            let message: Value = serde_json::from_str(text.as_ref()).unwrap();
            if message["type"] == "stt.final" {
                socket.send(Message::Text(json!({"v":1,"type":"tts.speak","session_id":SESSION_ID,"turn_id":message["turn_id"],"seq":1,"ts_ms":1,"utterance_id":"provider-utt","text":"Hello from Lucy.","flush":true}).to_string().into())).await.unwrap();
            }
            if message["type"] == "tts.playback" && message["state"] == "finished" {
                socket.send(Message::Text(json!({"v":1,"type":"session.end","session_id":SESSION_ID,"seq":2,"ts_ms":2,"reason":"fixture_complete"}).to_string().into())).await.unwrap();
            }
            let ended = message["type"] == "session.ended";
            messages.push(message);
            if ended {
                break;
            }
        }
        messages
    });
    let audio_listener = unused_listener().await;
    let audio_address = audio_listener.local_addr().unwrap();
    let mut config = AudioSocketServerConfig::new(
        "127.0.0.1:0",
        &format!("ws://{control_address}"),
        CONTROL_TOKEN,
    )
    .unwrap();
    config.provider_media = Some(provider_media);
    let handler = tokio::spawn(async move {
        let (stream, _) = audio_listener.accept().await.unwrap();
        handle_audio_socket_connection_with_config(
            stream,
            config,
            GatewayMetrics::default(),
            || 1_000,
        )
        .await
    });
    let mut asterisk = TcpStream::connect(audio_address).await.unwrap();
    let mut incoming = audio_socket_frame(0x01, &SESSION_UUID);
    incoming.extend(audio_socket_frame(0x10, &[1, 0, 2, 0]));
    asterisk.write_all(&incoming).await.unwrap();
    let mut header = [0_u8; 3];
    tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, asterisk.read_exact(&mut header))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(header[0], 0x10);
    let mut pcm = vec![0; usize::from(u16::from_be_bytes([header[1], header[2]]))];
    asterisk.read_exact(&mut pcm).await.unwrap();
    assert!(!pcm.is_empty());
    asterisk
        .write_all(&audio_socket_frame(0x00, &[]))
        .await
        .unwrap();
    tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, handler)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let messages = tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, control)
        .await
        .unwrap()
        .unwrap();
    assert!(messages
        .iter()
        .any(|message| message["type"] == "stt.final"));
    assert!(messages
        .iter()
        .any(|message| { message["type"] == "stt.final" && message["provider"] == "deepgram" }));
    assert_control_messages_are_safe(&messages);
    join_servers(providers).await;
}

#[tokio::test]
async fn media_websocket_configured_provider_backend_replays_fixtures_and_keeps_control_audio_free()
{
    let (provider_media, providers) = replay_provider_servers().await;
    let control_listener = unused_listener().await;
    let control_address = control_listener.local_addr().unwrap();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut messages = Vec::new();
        while let Some(message) = socket.next().await {
            let message = message.unwrap();
            if let Message::Ping(payload) = message {
                socket.send(Message::Pong(payload)).await.unwrap();
                continue;
            }
            let Message::Text(text) = message else {
                continue;
            };
            let message: Value = serde_json::from_str(text.as_ref()).unwrap();
            if message["type"] == "stt.final" {
                socket.send(Message::Text(json!({"v":1,"type":"tts.speak","session_id":"caller-1","turn_id":message["turn_id"],"seq":1,"ts_ms":1,"utterance_id":"provider-utt","text":"Hello from Lucy.","flush":true}).to_string().into())).await.unwrap();
            }
            if message["type"] == "tts.playback" && message["state"] == "finished" {
                socket.send(Message::Text(json!({"v":1,"type":"session.end","session_id":"caller-1","seq":2,"ts_ms":2,"reason":"fixture_complete"}).to_string().into())).await.unwrap();
            }
            let ended = message["type"] == "session.ended";
            messages.push(message);
            if ended {
                break;
            }
        }
        messages
    });
    let media_listener = unused_listener().await;
    let media_address = media_listener.local_addr().unwrap();
    let mut config = MediaWebSocketRuntimeConfig::new(
        media_address,
        format!("ws://{control_address}"),
        CONTROL_TOKEN.to_string(),
    )
    .unwrap();
    config.provider_media = Some(provider_media);
    let handler = tokio::spawn(async move {
        let (stream, _) = media_listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config).await
    });
    let (mut asterisk, _) = tokio_tungstenite::connect_async(format!("ws://{media_address}"))
        .await
        .unwrap();
    asterisk.send(Message::Text(json!({"event":"MEDIA_START","connection_id":"connection-1","channel":"WebSocket/lucy","channel_id":"caller-1","format":"slin","optimal_frame_size":640,"ptime":20}).to_string().into())).await.unwrap();
    asterisk
        .send(Message::Binary(vec![1, 0, 2, 0].into()))
        .await
        .unwrap();
    let mut saw_pcm = false;
    while let Some(message) = tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, asterisk.next())
        .await
        .unwrap()
    {
        let message = message.unwrap();
        match message {
            Message::Binary(pcm) if !pcm.is_empty() => {
                saw_pcm = true;
                asterisk.send(Message::Close(None)).await.unwrap();
                break;
            }
            Message::Binary(_) => {}
            Message::Text(text) => {
                let command: Value = serde_json::from_str(text.as_ref()).unwrap();
                if command["command"] == "MARK_MEDIA" {
                    asterisk.send(Message::Text(json!({"event":"MEDIA_MARK_PROCESSED","channel_id":"caller-1","correlation_id":command["correlation_id"]}).to_string().into())).await.unwrap();
                }
                if command["command"] == "HANGUP" {
                    break;
                }
            }
            _ => {}
        }
    }
    assert!(
        saw_pcm,
        "provider PCM never reached the Media WebSocket transport"
    );
    handler.await.unwrap().unwrap();
    let messages = tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, control)
        .await
        .unwrap()
        .unwrap();
    assert!(messages
        .iter()
        .any(|message| message["type"] == "stt.final"));
    assert!(messages
        .iter()
        .any(|message| { message["type"] == "stt.final" && message["provider"] == "deepgram" }));
    assert_control_messages_are_safe(&messages);
    join_servers(providers).await;
}

#[tokio::test]
async fn local_protocol_servers_replay_recorded_provider_frames_without_control_audio() {
    let deepgram = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let elevenlabs = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let stt_frames = received_fixture("lucy-deepgram/tests/fixtures/stt_short_utterance.jsonl");
    let tts_frames = received_fixture("lucy-elevenlabs/tests/fixtures/tts_short_sentence.jsonl");

    let stt = tokio::spawn(async move {
        let (stream, _) = deepgram.accept().await.unwrap();
        let mut saw_auth = false;
        let mut socket = accept_hdr_async(stream, |request: &Request, response: Response| {
            saw_auth = request
                .headers()
                .get("authorization")
                .is_some_and(|value| value == "Token stt-secret");
            Ok(response)
        })
        .await
        .unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Binary(_)
        ));
        for frame in stt_frames {
            socket
                .send(Message::Text(frame.to_string().into()))
                .await
                .unwrap();
        }
        while socket.next().await.is_some() {}
        saw_auth
    });
    let tts = tokio::spawn(async move {
        let (stream, _) = elevenlabs.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        for _ in 0..3 {
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Text(_)
            ));
        }
        for frame in tts_frames {
            socket
                .send(Message::Text(frame.to_string().into()))
                .await
                .unwrap();
        }
    });
    let config = local_config(deepgram_addr, elevenlabs_addr, LOCAL_PROTOCOL_TIMEOUT);
    let mut plane = ProviderMediaPlane::new(config);
    plane.push_audio(&vec![0; 320], 8_000, 100).unwrap();
    let mut events = Vec::new();
    tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, async {
        while !events
            .iter()
            .any(|event| matches!(event, MediaPlaneEvent::TranscriptFinal { .. }))
        {
            events.extend(plane.drain_events().unwrap());
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    for event in &events {
        assert_provider_error_is_redacted(&format!("{event:?}"));
    }
    let speech_end = events
        .iter()
        .position(|event| matches!(event, MediaPlaneEvent::SpeechEnded { speech_ms: 20, .. }))
        .unwrap();
    let transcript_final = events.iter().position(|event| matches!(event, MediaPlaneEvent::TranscriptFinal { text, .. } if text == "lucy")).unwrap();
    assert!(speech_end < transcript_final);
    assert_eq!(
        plane
            .start_playback("utt-1", "Hello from Lucy.", true)
            .unwrap(),
        MediaPlaneEvent::PlaybackStarted {
            utterance_id: "utt-1".to_string()
        }
    );
    let mut first_audio_bytes = None;
    tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, async {
        while first_audio_bytes.is_none() {
            if let PlaybackStep::Audio(bytes) = plane.next_playback(8_000).unwrap() {
                first_audio_bytes = Some(bytes.len());
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert_eq!(first_audio_bytes, Some(320));
    drop(plane);
    assert!(stt.await.unwrap());
    tts.await.unwrap();
}

#[tokio::test]
async fn malformed_deepgram_json_is_reported_without_leaking_credentials() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let malformed_frame =
        corrupted_recorded_frame("lucy-deepgram/tests/fixtures/stt_short_utterance.jsonl");
    let server = tokio::spawn(async move {
        let (stream, _) = deepgram.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Binary(_)
        ));
        socket
            .send(Message::Text(malformed_frame.into()))
            .await
            .unwrap();
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.push_audio(&vec![0; 320], 8_000, 100).unwrap();
    let error = tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, async {
        loop {
            if let Err(error) = plane.drain_events() {
                break error;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert_eq!(error.to_string(), "Deepgram returned malformed JSON");
    assert_provider_error_is_redacted(&error);
    server.await.unwrap();
}

#[tokio::test]
async fn deepgram_idle_timeout_is_bounded_and_reported() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let (ready_tx, mut ready_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (stream, _) = deepgram.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        assert!(matches!(
            socket.next().await.unwrap().unwrap(),
            Message::Binary(_)
        ));
        ready_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.push_audio(&vec![0; 320], 8_000, 100).unwrap();
    wait_for_realtime_signal(&mut ready_rx).await;
    tokio::time::pause();
    tokio::time::advance(LOCAL_PROTOCOL_TIMEOUT).await;
    tokio::task::yield_now().await;
    tokio::time::advance(LOCAL_PROTOCOL_TIMEOUT).await;
    tokio::task::yield_now().await;
    let error = wait_for_provider_error(|| plane.drain_events()).await;
    assert_eq!(error.to_string(), "Deepgram read timed out");
    assert_provider_error_is_redacted(&error);
    server.abort();
}

#[tokio::test(start_paused = true)]
async fn deepgram_stalled_handshake_times_out_deterministically() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let (accepted_tx, mut accepted_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (_stream, _) = deepgram.accept().await.unwrap();
        accepted_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let mut plane = ProviderMediaPlane::new(local_config_with_timeouts(
        deepgram_addr,
        elevenlabs_addr,
        Duration::from_millis(20),
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.push_audio(&vec![0; 320], 8_000, 100).unwrap();
    wait_for_signal(&mut accepted_rx).await;
    tokio::time::advance(Duration::from_millis(20)).await;
    tokio::task::yield_now().await;
    let error = wait_for_provider_error(|| plane.drain_events()).await;
    assert_eq!(error.to_string(), "Deepgram connection timed out");
    assert_provider_error_is_redacted(&error);
    server.abort();
}

#[tokio::test]
async fn deepgram_startup_buffers_audio_for_the_typed_connect_timeout() {
    const FRAME_COUNT: usize = 40;
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let (accepted_tx, accepted_rx) = oneshot::channel();
    let (handshake_tx, handshake_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (stream, _) = deepgram.accept().await.unwrap();
        accepted_tx.send(()).unwrap();
        handshake_rx.await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut frames = 0;
        while frames < FRAME_COUNT {
            if matches!(socket.next().await.unwrap().unwrap(), Message::Binary(_)) {
                frames += 1;
            }
        }
        frames
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.push_audio(&vec![0; 640], 16_000, 0).unwrap();
    accepted_rx.await.unwrap();
    for frame in 1..FRAME_COUNT {
        plane
            .push_audio(&vec![0; 640], 16_000, frame as u64 * 20)
            .unwrap();
    }
    handshake_tx.send(()).unwrap();
    assert_eq!(server.await.unwrap(), FRAME_COUNT);
}

#[tokio::test]
async fn invalid_elevenlabs_audio_is_rejected() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let malformed_frame = corrupted_recorded_audio_frame();
    let server = tokio::spawn(async move {
        let (stream, _) = elevenlabs.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        for _ in 0..3 {
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Text(_)
            ));
        }
        socket
            .send(Message::Text(malformed_frame.to_string().into()))
            .await
            .unwrap();
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.start_playback("utt-1", "Hello", true).unwrap();
    let error = tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, async {
        loop {
            if let Err(error) = plane.next_playback(8_000) {
                break error;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    assert_eq!(error.to_string(), "ElevenLabs returned invalid audio");
    assert_provider_error_is_redacted(&error);
    server.await.unwrap();
}

#[tokio::test]
async fn elevenlabs_receives_the_control_channel_flush_value() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = elevenlabs.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut payloads = Vec::new();
        for _ in 0..3 {
            let Message::Text(text) = socket.next().await.unwrap().unwrap() else {
                panic!("ElevenLabs input must be JSON text");
            };
            payloads.push(serde_json::from_str::<Value>(text.as_ref()).unwrap());
        }
        payloads[1]["flush"].as_bool().unwrap()
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane
        .start_playback("utt-1", "First clause", false)
        .unwrap();
    assert!(!server.await.unwrap());
}

#[tokio::test]
async fn elevenlabs_idle_timeout_is_bounded_and_reported() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let (ready_tx, mut ready_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (stream, _) = elevenlabs.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        for _ in 0..3 {
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Text(_)
            ));
        }
        ready_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.start_playback("utt-1", "Hello", true).unwrap();
    wait_for_realtime_signal(&mut ready_rx).await;
    tokio::time::pause();
    tokio::time::advance(LOCAL_PROTOCOL_TIMEOUT).await;
    tokio::task::yield_now().await;
    tokio::time::advance(LOCAL_PROTOCOL_TIMEOUT).await;
    tokio::task::yield_now().await;
    let error = wait_for_provider_error(|| plane.next_playback(8_000)).await;
    assert_eq!(error.to_string(), "ElevenLabs read timed out");
    assert_provider_error_is_redacted(&error);
    server.abort();
}

#[tokio::test(start_paused = true)]
async fn elevenlabs_stalled_handshake_times_out_deterministically() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let (accepted_tx, mut accepted_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (_stream, _) = elevenlabs.accept().await.unwrap();
        accepted_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let mut plane = ProviderMediaPlane::new(local_config_with_timeouts(
        deepgram_addr,
        elevenlabs_addr,
        Duration::from_millis(20),
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.start_playback("utt-1", "Hello", true).unwrap();
    wait_for_signal(&mut accepted_rx).await;
    tokio::time::advance(Duration::from_millis(20)).await;
    tokio::task::yield_now().await;
    let error = wait_for_provider_error(|| plane.next_playback(8_000)).await;
    assert_eq!(error.to_string(), "ElevenLabs connection timed out");
    assert_provider_error_is_redacted(&error);
    server.abort();
}

#[tokio::test]
async fn cancelling_playback_closes_provider_socket_and_flushes_locally() {
    let deepgram = unused_listener().await;
    let elevenlabs = unused_listener().await;
    let deepgram_addr = deepgram.local_addr().unwrap();
    let elevenlabs_addr = elevenlabs.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = elevenlabs.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        for _ in 0..3 {
            assert!(matches!(
                socket.next().await.unwrap().unwrap(),
                Message::Text(_)
            ));
        }
        matches!(socket.next().await, Some(Ok(Message::Close(_))) | None)
    });
    let mut plane = ProviderMediaPlane::new(local_config(
        deepgram_addr,
        elevenlabs_addr,
        LOCAL_PROTOCOL_TIMEOUT,
    ));
    plane.start_playback("utt-1", "Hello", true).unwrap();
    tokio::task::yield_now().await;
    assert_eq!(
        plane.cancel_playback("utt-1").unwrap(),
        MediaPlaneEvent::PlaybackFlushed {
            utterance_id: "utt-1".to_string(),
            mark_chars: 0,
        }
    );
    assert!(tokio::time::timeout(LOCAL_PROTOCOL_TIMEOUT, server)
        .await
        .unwrap()
        .unwrap());
    assert_eq!(plane.next_playback(8_000).unwrap(), PlaybackStep::Idle);
}
