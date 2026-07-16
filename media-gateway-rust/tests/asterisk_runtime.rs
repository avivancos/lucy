use axum::{
    body::Body,
    extract::{
        ws::{Message as AxumMessage, WebSocketUpgrade},
        State,
    },
    http::{Request, Response, StatusCode},
    response::IntoResponse,
    routing::{any, get},
    Router,
};
use futures_util::{SinkExt, StreamExt};
use lucy_media_gateway::asterisk::media_plane::FixtureMediaPlaneConfig;
use lucy_media_gateway::asterisk::media_websocket::MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES;
use lucy_media_gateway::asterisk::runtime::{
    run_ari_external_media_lifecycle, run_ari_external_media_session,
    serve_media_websocket_connection, AriRuntimeConfig, AsteriskRuntimeConfig,
    MediaWebSocketRuntimeConfig, TransportMode,
};
use serde_json::{json, Value};
use std::{
    env,
    net::{Ipv4Addr, SocketAddr},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    net::{TcpListener, UdpSocket},
    sync::oneshot,
};
use tokio_tungstenite::{
    accept_async, accept_hdr_async, connect_async,
    tungstenite::{
        handshake::server::{Request as WebSocketRequest, Response as WebSocketResponse},
        Message,
    },
};

const CONTROL_PATH: &str = "/v1/session/ws";
const CONTROL_TOKEN: &str = "test-control-token";
const RTP_PAYLOAD_TYPE: u8 = 118;
const RTP_CLOCK_RATE_HZ: u32 = 16_000;
static ENV_LOCK: Mutex<()> = Mutex::new(());

struct EnvGuard(Vec<(String, Option<String>)>);

impl EnvGuard {
    fn set(values: &[(&str, &str)]) -> Self {
        let previous = values
            .iter()
            .map(|(name, value)| {
                let previous = env::var(name).ok();
                env::set_var(name, value);
                ((*name).to_string(), previous)
            })
            .collect();
        Self(previous)
    }

    fn unset(names: &[&str]) -> Self {
        let previous = names
            .iter()
            .map(|name| {
                let previous = env::var(name).ok();
                env::remove_var(name);
                ((*name).to_string(), previous)
            })
            .collect();
        Self(previous)
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        for (name, value) in self.0.drain(..) {
            match value {
                Some(value) => env::set_var(name, value),
                None => env::remove_var(name),
            }
        }
    }
}

fn media_fixture() -> FixtureMediaPlaneConfig {
    let root = std::env::var("LUCY_AUDIO_FIXTURE_WAV")
        .ok()
        .and_then(|path| PathBuf::from(path).parent().map(PathBuf::from))
        .unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/audio")
        });
    FixtureMediaPlaneConfig {
        playback_wav: root.join("booking_caller_8k.wav"),
        transcript_timeline: root.join("booking_caller.timeline.json"),
        transcript_trigger_bytes: 4,
        playback_chunk_bytes: 640,
        playback_pacing_ms: 1,
    }
}

fn fixture_pcm() -> Vec<u8> {
    hound::WavReader::open(media_fixture().playback_wav)
        .unwrap()
        .into_samples::<i16>()
        .flat_map(|sample| sample.unwrap().to_le_bytes())
        .collect()
}

#[test]
fn typed_mode_config_selects_the_existing_audio_socket_server() {
    let config = AsteriskRuntimeConfig::from_values(
        "audiosocket",
        "127.0.0.1:9092",
        "ws://127.0.0.1:8000/v1/session/ws",
        CONTROL_TOKEN,
    )
    .unwrap();

    assert_eq!(config.mode, TransportMode::AudioSocket);
    assert_eq!(
        config.audio_socket.bind_address,
        "127.0.0.1:9092".parse().unwrap()
    );
    assert!(AsteriskRuntimeConfig::from_values(
        "unknown",
        "127.0.0.1:9092",
        "ws://127.0.0.1:8000/v1/session/ws",
        CONTROL_TOKEN,
    )
    .is_err());
}

#[test]
fn ari_events_reject_plaintext_remote_and_cross_origin_credentials() {
    let config = AriRuntimeConfig::new(
        "https://pbx.example.com/ari/".to_string(),
        "lucy-test",
        "secret",
        "lucy-voice",
        SocketAddr::from((Ipv4Addr::LOCALHOST, 20_010)),
        "lucy-media-gateway:9094",
        RTP_PAYLOAD_TYPE,
        RTP_CLOCK_RATE_HZ,
        Duration::from_secs(1),
        false,
    )
    .unwrap();

    for (events_url, expected) in [
        ("ws://pbx.example.com/ari/events", "scheme must match"),
        (
            "wss://attacker.example.com/ari/events",
            "configured ARI host",
        ),
        (
            "wss://pbx.example.com:8443/ari/events",
            "configured ARI port",
        ),
    ] {
        let result = config.clone().with_lifecycle(
            "caller-1".to_string(),
            "ws://127.0.0.1:8000/v1/session/ws".to_string(),
            CONTROL_TOKEN.to_string(),
            events_url.to_string(),
            Duration::from_secs(1),
            Duration::from_secs(1),
            Duration::from_secs(1),
            media_fixture(),
        );
        let error = match result {
            Err(error) => error,
            Ok(_) => panic!("unsafe ARI events URL was accepted"),
        };
        assert!(error.to_string().contains(expected), "{error}");
        assert!(!error.to_string().contains("secret"));
    }
}

#[test]
fn ari_runtime_env_accepts_the_compose_gateway_dns_name() {
    let _lock = ENV_LOCK.lock().unwrap();
    let _env = EnvGuard::set(&[
        ("LUCY_ASTERISK_ARI_BASE_URL", "http://127.0.0.1:8088/ari/"),
        ("LUCY_ASTERISK_ARI_USERNAME", "lucy-test"),
        ("LUCY_ASTERISK_ARI_PASSWORD", "secret"),
        ("LUCY_ASTERISK_ARI_APP", "lucy-voice"),
        ("LUCY_GATEWAY_ARI_RTP_BIND", "0.0.0.0:9094"),
        ("LUCY_GATEWAY_ARI_RTP_ADVERTISED", "lucy-media-gateway:9094"),
        ("LUCY_ASTERISK_ARI_ALLOW_INSECURE_HTTP", "true"),
        ("LUCY_ASTERISK_ARI_CALLER_CHANNEL_ID", "caller-1"),
        ("LUCY_SESSION_WS_URL", "ws://127.0.0.1:8000/v1/session/ws"),
        ("LUCY_GATEWAY_CONTROL_TOKEN", CONTROL_TOKEN),
        (
            "LUCY_ASTERISK_ARI_EVENTS_WS_URL",
            "ws://127.0.0.1:8088/ari/events?app=lucy-voice",
        ),
        ("LUCY_GATEWAY_MEDIA_BACKEND", "fixture"),
        (
            "LUCY_GATEWAY_MEDIA_FIXTURE_WAV",
            media_fixture().playback_wav.to_str().unwrap(),
        ),
        (
            "LUCY_GATEWAY_MEDIA_FIXTURE_TIMELINE",
            media_fixture().transcript_timeline.to_str().unwrap(),
        ),
    ]);

    AriRuntimeConfig::from_env().unwrap();
}

#[test]
fn media_websocket_env_resolves_the_shared_fixture_backend() {
    let _lock = ENV_LOCK.lock().unwrap();
    let fixture = media_fixture();
    let _env = EnvGuard::set(&[
        ("LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND", "127.0.0.1:9093"),
        ("LUCY_SESSION_WS_URL", "ws://127.0.0.1:8000/v1/session/ws"),
        ("LUCY_GATEWAY_MEDIA_BACKEND", "fixture"),
        ("LUCY_GATEWAY_CONTROL_TOKEN", "test-control-token"),
        (
            "LUCY_GATEWAY_MEDIA_FIXTURE_WAV",
            fixture.playback_wav.to_str().unwrap(),
        ),
        (
            "LUCY_GATEWAY_MEDIA_FIXTURE_TIMELINE",
            fixture.transcript_timeline.to_str().unwrap(),
        ),
    ]);

    let config = MediaWebSocketRuntimeConfig::from_env().unwrap();
    assert!(config.media_plane.is_some());
    assert!(config.provider_media.is_none());
}

#[test]
fn runtime_env_rejects_missing_or_whitespace_control_token() {
    let _lock = ENV_LOCK.lock().unwrap();
    let fixture = media_fixture();
    let _env = EnvGuard::set(&[
        ("LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND", "127.0.0.1:9093"),
        ("LUCY_SESSION_WS_URL", "ws://127.0.0.1:8000/v1/session/ws"),
        ("LUCY_GATEWAY_MEDIA_BACKEND", "fixture"),
        (
            "LUCY_GATEWAY_MEDIA_FIXTURE_WAV",
            fixture.playback_wav.to_str().unwrap(),
        ),
        (
            "LUCY_GATEWAY_MEDIA_FIXTURE_TIMELINE",
            fixture.transcript_timeline.to_str().unwrap(),
        ),
    ]);
    let _missing_token = EnvGuard::unset(&["LUCY_GATEWAY_CONTROL_TOKEN"]);

    let missing = match MediaWebSocketRuntimeConfig::from_env() {
        Err(error) => error,
        Ok(_) => panic!("missing control token was accepted"),
    };
    assert!(missing.to_string().contains("LUCY_GATEWAY_CONTROL_TOKEN"));

    env::set_var("LUCY_GATEWAY_CONTROL_TOKEN", "  ");
    let whitespace = match MediaWebSocketRuntimeConfig::from_env() {
        Err(error) => error,
        Ok(_) => panic!("whitespace control token was accepted"),
    };
    assert!(whitespace.to_string().contains("control token is invalid"));
}

#[tokio::test]
async fn media_websocket_uses_the_shared_control_bearer_request_policy() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut authorization = None;
        let mut socket = accept_hdr_async(
            stream,
            |request: &WebSocketRequest, response: WebSocketResponse| {
                authorization = request.headers().get("authorization").cloned();
                Ok(response)
            },
        )
        .await
        .unwrap();
        let _ = socket.next().await;
        authorization.unwrap().to_str().unwrap().to_string()
    });
    let fixture = media_fixture();
    let media_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let media_address = media_listener.local_addr().unwrap();
    let config = {
        let _lock = ENV_LOCK.lock().unwrap();
        let _env = EnvGuard::set(&[
            (
                "LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND",
                &media_address.to_string(),
            ),
            (
                "LUCY_SESSION_WS_URL",
                &format!("ws://{control_address}{CONTROL_PATH}"),
            ),
            ("LUCY_GATEWAY_CONTROL_TOKEN", "control-secret"),
            ("LUCY_GATEWAY_MEDIA_BACKEND", "fixture"),
            (
                "LUCY_GATEWAY_MEDIA_FIXTURE_WAV",
                fixture.playback_wav.to_str().unwrap(),
            ),
            (
                "LUCY_GATEWAY_MEDIA_FIXTURE_TIMELINE",
                fixture.transcript_timeline.to_str().unwrap(),
            ),
        ]);
        MediaWebSocketRuntimeConfig::from_env().unwrap()
    };
    let server = tokio::spawn(async move {
        let (stream, _) = media_listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config).await
    });
    let (mut media, _) = connect_async(format!("ws://{media_address}"))
        .await
        .unwrap();
    media.send(Message::Text(json!({
        "event": "MEDIA_START", "connection_id": "connection-1", "channel": "WebSocket/lucy",
        "channel_id": "caller-1", "format": "slin", "optimal_frame_size": 640, "ptime": 20
    }).to_string().into())).await.unwrap();
    assert_eq!(control.await.unwrap(), "Bearer control-secret");
    drop(media);
    let _ = server.await;
}

#[tokio::test]
async fn real_media_websocket_endpoint_forwards_control_without_audio() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(
            stream,
            |request: &WebSocketRequest, response: WebSocketResponse| {
                assert_eq!(
                    request.headers().get("authorization").unwrap(),
                    format!("Bearer {CONTROL_TOKEN}").as_str()
                );
                Ok(response)
            },
        )
        .await
        .unwrap();
        let Message::Text(message) = socket.next().await.unwrap().unwrap() else {
            panic!("control message must be text")
        };
        let started = serde_json::from_str::<Value>(message.as_ref()).unwrap();
        while let Some(message) = socket.next().await {
            if matches!(message.unwrap(), Message::Close(_)) {
                break;
            }
        }
        started
    });

    let media_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let media_address = media_listener.local_addr().unwrap();
    let config = MediaWebSocketRuntimeConfig::new(
        media_address,
        format!("ws://{control_address}{CONTROL_PATH}"),
        CONTROL_TOKEN.to_string(),
    )
    .unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = media_listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config)
            .await
            .unwrap();
    });

    let (mut asterisk, _) = connect_async(format!("ws://{media_address}"))
        .await
        .unwrap();
    asterisk
        .send(Message::Text(
            json!({
                "event": "MEDIA_START",
                "connection_id": "connection-1",
                "channel": "WebSocket/lucy",
                "channel_id": "caller-1",
                "format": "slin",
                "optimal_frame_size": 640,
                "ptime": 20
            })
            .to_string()
            .into(),
        ))
        .await
        .unwrap();
    asterisk
        .send(Message::Binary(vec![1, 0, 2, 0].into()))
        .await
        .unwrap();
    asterisk.send(Message::Close(None)).await.unwrap();

    let started = control.await.unwrap();
    assert_eq!(started["type"], "session.started");
    assert_eq!(started["transport"], "asterisk/media_websocket");
    assert!(started.get("audio").is_none());
    server.await.unwrap();
}

#[tokio::test]
async fn media_websocket_rejects_oversized_frames_before_adapter_parsing() {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    let config = MediaWebSocketRuntimeConfig::new(
        address,
        "ws://127.0.0.1:9/control".to_string(),
        CONTROL_TOKEN.to_string(),
    )
    .unwrap();
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config)
            .await
            .unwrap_err()
            .to_string()
    });
    let (mut media, _) = connect_async(format!("ws://{address}")).await.unwrap();

    media
        .send(Message::Binary(
            vec![0; MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES + 1].into(),
        ))
        .await
        .unwrap();

    let error = server.await.unwrap();
    assert!(error.contains("read media WebSocket"));
    assert!(!error.contains("media WebSocket media frame exceeds size limit"));
}

#[tokio::test]
async fn media_websocket_pauses_playback_then_marks_the_completed_utterance() {
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (playback_started_tx, playback_started_rx) = oneshot::channel();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let mut events = Vec::new();
        let mut playback_started_tx = Some(playback_started_tx);
        let mut processed_marks = 0;
        while let Some(message) = socket.next().await {
            let Message::Text(text) = message.unwrap() else {
                continue;
            };
            let value: Value = serde_json::from_str(text.as_ref()).unwrap();
            let kind = value["type"].as_str().unwrap().to_string();
            events.push(value.clone());
            if kind == "session.started" {
                socket.send(Message::Text(json!({
                    "v": 1, "type": "session.configure", "session_id": "caller-1",
                    "seq": 1, "ts_ms": 1, "stt": "fixture", "tts": "fixture", "vad": "fixture"
                }).to_string().into())).await.unwrap();
            }
            if kind == "stt.final" {
                for (seq, utterance_id, text, flush) in [
                    (2, "utt-1", "Hello caller", false),
                    (3, "utt-2", "How can I help?", true),
                ] {
                    socket.send(Message::Text(json!({
                        "v": 1, "type": "tts.speak", "session_id": "caller-1", "turn_id": value["turn_id"],
                        "seq": seq, "ts_ms": seq, "utterance_id": utterance_id, "text": text, "flush": flush
                    }).to_string().into())).await.unwrap();
                }
            }
            if kind == "tts.playback" && value["state"] == "started" {
                if let Some(started) = playback_started_tx.take() {
                    started.send(()).unwrap();
                }
            }
            if kind == "tts.playback" && value["state"] == "mark" {
                processed_marks += 1;
                if processed_marks == 2 {
                    socket
                        .send(Message::Text(
                            json!({
                                "v": 1, "type": "session.end", "session_id": "caller-1",
                                "seq": 4, "ts_ms": 4, "reason": "complete"
                            })
                            .to_string()
                            .into(),
                        ))
                        .await
                        .unwrap();
                }
            }
            if kind == "session.ended" {
                break;
            }
        }
        events
    });

    let media_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let media_address = media_listener.local_addr().unwrap();
    let mut config = MediaWebSocketRuntimeConfig::new(
        media_address,
        format!("ws://{control_address}{CONTROL_PATH}"),
        CONTROL_TOKEN.to_string(),
    )
    .unwrap();
    config.media_plane = Some(media_fixture());
    let server = tokio::spawn(async move {
        let (stream, _) = media_listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config)
            .await
            .unwrap();
    });

    let (mut asterisk, _) = connect_async(format!("ws://{media_address}"))
        .await
        .unwrap();
    asterisk.send(Message::Text(json!({
        "event": "MEDIA_START", "connection_id": "connection-1", "channel": "WebSocket/lucy",
        "channel_id": "caller-1", "format": "slin", "optimal_frame_size": 640, "ptime": 20
    }).to_string().into())).await.unwrap();
    asterisk
        .send(Message::Text(
            json!({"event": "MEDIA_XOFF", "channel_id": "caller-1"})
                .to_string()
                .into(),
        ))
        .await
        .unwrap();
    asterisk
        .send(Message::Binary(vec![1, 0, 2, 0].into()))
        .await
        .unwrap();

    playback_started_rx.await.unwrap();
    asterisk
        .send(Message::Text(
            json!({"event": "MEDIA_XON", "channel_id": "caller-1"})
                .to_string()
                .into(),
        ))
        .await
        .unwrap();

    let mut commands = Vec::new();
    let mut playback = Vec::new();
    let mut mark_correlation_ids = Vec::new();
    while let Some(message) = asterisk.next().await {
        match message.unwrap() {
            Message::Binary(bytes) => playback.extend(bytes),
            Message::Text(text) => {
                let command = serde_json::from_str::<Value>(text.as_ref()).unwrap();
                if command["command"] == "MARK_MEDIA" {
                    let correlation_id = command["correlation_id"].as_str().unwrap().to_string();
                    mark_correlation_ids.push(correlation_id.clone());
                    asterisk
                        .send(Message::Text(
                            json!({
                                "event": "MEDIA_MARK_PROCESSED",
                                "channel_id": "caller-1",
                                "correlation_id": correlation_id
                            })
                            .to_string()
                            .into(),
                        ))
                        .await
                        .unwrap();
                }
                commands.push(command);
            }
            Message::Close(_) => break,
            _ => {}
        }
        if commands
            .iter()
            .any(|command| command["command"] == "HANGUP")
        {
            break;
        }
    }
    server.await.unwrap();
    let events = control.await.unwrap();
    let types = events
        .iter()
        .map(|event| event["type"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(types.contains(&"stt.final"));
    assert!(types.contains(&"tts.playback"));
    let final_turn = events
        .iter()
        .find(|event| event["type"] == "stt.final")
        .unwrap()["turn_id"]
        .clone();
    assert!(events.iter().any(|event| {
        event["type"] == "transport.metrics"
            && event["turn_id"] == final_turn
            && event["rtt_ms"].as_f64().is_some()
    }));
    assert_eq!(playback, [fixture_pcm(), fixture_pcm()].concat());
    assert_eq!(mark_correlation_ids, ["lucy-mark-1", "lucy-mark-2"]);
    let marks = events
        .iter()
        .filter(|event| event["type"] == "tts.playback" && event["state"] == "mark")
        .map(|event| {
            (
                event["utterance_id"].as_str().unwrap(),
                event["mark_chars"].as_u64().unwrap(),
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(
        marks,
        [
            ("utt-1", "Hello caller".chars().count() as u64),
            ("utt-2", "How can I help?".chars().count() as u64)
        ]
    );
    assert!(commands
        .iter()
        .any(|command| command["command"] == "MARK_MEDIA"));
    assert!(commands
        .iter()
        .any(|command| command["command"] == "HANGUP"));
    assert!(events.iter().all(|event| event.get("audio").is_none()));
}

#[tokio::test(start_paused = true)]
async fn media_websocket_closes_stalled_handshake_and_idle_peers() {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    let mut config = MediaWebSocketRuntimeConfig::new(
        address,
        "ws://127.0.0.1:9/control".to_string(),
        CONTROL_TOKEN.to_string(),
    )
    .unwrap();
    config.handshake_timeout = Duration::from_millis(10);
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config)
            .await
            .unwrap_err()
            .to_string()
    });
    let _stalled = connect_async(format!("ws://{address}")).await.unwrap();
    for _ in 0..3 {
        tokio::task::yield_now().await;
    }
    tokio::time::advance(Duration::from_millis(11)).await;
    assert!(
        server.is_finished(),
        "MEDIA_START handshake deadline did not resolve"
    );
    assert!(server.await.unwrap().contains("handshake timed out"));

    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (started_tx, started_rx) = oneshot::channel();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        let _ = socket.next().await;
        started_tx.send(()).unwrap();
        while socket.next().await.is_some() {}
    });
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    let mut config = MediaWebSocketRuntimeConfig::new(
        address,
        format!("ws://{control_address}{CONTROL_PATH}"),
        CONTROL_TOKEN.to_string(),
    )
    .unwrap();
    config.idle_timeout = Duration::from_millis(10);
    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        serve_media_websocket_connection(stream, config)
            .await
            .unwrap_err()
            .to_string()
    });
    let (mut asterisk, _) = connect_async(format!("ws://{address}")).await.unwrap();
    asterisk.send(Message::Text(json!({
        "event": "MEDIA_START", "connection_id": "connection-1", "channel": "WebSocket/lucy",
        "channel_id": "caller-1", "format": "slin", "optimal_frame_size": 640, "ptime": 20
    }).to_string().into())).await.unwrap();
    started_rx.await.unwrap();
    for _ in 0..3 {
        tokio::task::yield_now().await;
    }
    tokio::time::advance(Duration::from_millis(11)).await;
    for _ in 0..3 {
        tokio::task::yield_now().await;
    }
    tokio::time::advance(Duration::from_millis(11)).await;
    assert!(server.is_finished(), "idle deadline did not resolve");
    assert!(server.await.unwrap().contains("idle timeout"));
    drop(asterisk);
    control.await.unwrap();
}

#[derive(Clone, Default)]
struct AriState {
    requests: Arc<Mutex<Vec<(String, String)>>>,
    rtp_ready: Arc<Mutex<Option<oneshot::Sender<()>>>>,
}

async fn ari_handler(State(state): State<AriState>, request: Request<Body>) -> Response<Body> {
    let method = request.method().to_string();
    let path = request.uri().path().to_string();
    state
        .requests
        .lock()
        .unwrap()
        .push((method.clone(), path.clone()));
    if path == "/ari/channels/externalMedia" {
        if let Some(sender) = state.rtp_ready.lock().unwrap().take() {
            sender.send(()).unwrap();
        }
    }
    let body = match (method.as_str(), path.as_str()) {
        ("POST", "/ari/bridges") => json!({"id": "bridge-1"}),
        ("POST", "/ari/channels/externalMedia") => json!({"id": "external-1"}),
        ("POST", "/ari/bridges/bridge-1/addChannel") => Value::Null,
        ("POST", "/ari/channels/caller-1/dtmf") | ("POST", "/ari/channels/caller-1/redirect") => {
            Value::Null
        }
        ("DELETE", "/ari/channels/caller-1") => Value::Null,
        ("DELETE", "/ari/channels/external-1") | ("DELETE", "/ari/bridges/bridge-1") => Value::Null,
        _ => {
            return Response::builder()
                .status(StatusCode::NOT_FOUND)
                .body(Body::empty())
                .unwrap()
        }
    };
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn ari_events_handler(upgrade: WebSocketUpgrade) -> impl IntoResponse {
    upgrade.on_upgrade(|mut events| async move {
        events
            .send(AxumMessage::Text(
                json!({"type":"ChannelDtmfReceived","digit":"9","channel":{"id":"other-call"}})
                    .to_string(),
            ))
            .await
            .unwrap();
        events
            .send(AxumMessage::Text(
                json!({"type":"ChannelDtmfReceived","digit":"#","channel":{"id":"caller-1"}})
                    .to_string(),
            ))
            .await
            .unwrap();
        while events.recv().await.is_some() {}
    })
}

fn rtp_packet() -> Vec<u8> {
    let mut packet = vec![0x80, RTP_PAYLOAD_TYPE];
    packet.extend(1_u16.to_be_bytes());
    packet.extend(0_u32.to_be_bytes());
    packet.extend(1_u32.to_be_bytes());
    packet.extend([1, 0, 2, 0]);
    packet
}

#[tokio::test]
async fn ari_session_command_runs_real_ari_rtp_and_cleanup_lifecycle() {
    let (rtp_ready_tx, rtp_ready_rx) = oneshot::channel();
    let state = AriState {
        rtp_ready: Arc::new(Mutex::new(Some(rtp_ready_tx))),
        ..AriState::default()
    };
    let ari_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let ari_address = ari_listener.local_addr().unwrap();
    let app = Router::new()
        .fallback(any(ari_handler))
        .with_state(state.clone());
    tokio::spawn(async move { axum::serve(ari_listener, app).await.unwrap() });

    let rtp_receiver = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let rtp_address = rtp_receiver.local_addr().unwrap();
    drop(rtp_receiver);
    let config = AriRuntimeConfig::new(
        format!("http://{ari_address}/ari/"),
        "lucy-test",
        "secret",
        "lucy-voice",
        rtp_address,
        rtp_address,
        RTP_PAYLOAD_TYPE,
        RTP_CLOCK_RATE_HZ,
        Duration::from_millis(100),
        true,
    )
    .unwrap()
    .with_rtp_allowed_cidrs("127.0.0.0/8")
    .unwrap();
    let sender = tokio::spawn(async move {
        rtp_ready_rx.await.unwrap();
        UdpSocket::bind((Ipv4Addr::new(127, 0, 0, 2), 0))
            .await
            .unwrap()
            .send_to(&rtp_packet(), rtp_address)
            .await
            .unwrap();
        UdpSocket::bind((Ipv4Addr::LOCALHOST, 0))
            .await
            .unwrap()
            .send_to(&rtp_packet(), rtp_address)
            .await
            .unwrap();
    });

    let report = run_ari_external_media_session(&config, "caller-1")
        .await
        .unwrap();
    sender.await.unwrap();
    assert_eq!(report.caller_channel_id, "caller-1");
    assert_eq!(report.rtp_payload_bytes, 4);
    let requests = state.requests.lock().unwrap().clone();
    assert_eq!(
        requests,
        vec![
            ("POST".to_string(), "/ari/bridges".to_string()),
            (
                "POST".to_string(),
                "/ari/channels/externalMedia".to_string()
            ),
            (
                "POST".to_string(),
                "/ari/bridges/bridge-1/addChannel".to_string()
            ),
            ("DELETE".to_string(), "/ari/channels/external-1".to_string()),
            ("DELETE".to_string(), "/ari/bridges/bridge-1".to_string()),
        ]
    );
}

#[tokio::test]
async fn ari_lifecycle_stays_open_for_events_control_and_multiple_rtp_packets() {
    let (rtp_ready_tx, rtp_ready_rx) = oneshot::channel();
    let state = AriState {
        rtp_ready: Arc::new(Mutex::new(Some(rtp_ready_tx))),
        ..AriState::default()
    };
    let ari_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let ari_address = ari_listener.local_addr().unwrap();
    let ari_app = Router::new()
        .route("/ari/events", get(ari_events_handler))
        .fallback(any(ari_handler))
        .with_state(state.clone());
    tokio::spawn(async move { axum::serve(ari_listener, ari_app).await.unwrap() });
    let control_listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(
            stream,
            |request: &WebSocketRequest, response: WebSocketResponse| {
                assert_eq!(
                    request.headers().get("authorization").unwrap(),
                    format!("Bearer {CONTROL_TOKEN}").as_str()
                );
                Ok(response)
            },
        )
        .await
        .unwrap();
        let mut values = Vec::new();
        let mut speak_sent = false;
        while let Some(Ok(Message::Text(text))) = socket.next().await {
            let value: Value = serde_json::from_str(text.as_ref()).unwrap();
            let kind = value["type"].as_str().unwrap();
            values.push(value.clone());
            if kind == "session.started" {
                socket.send(Message::Text(json!({"v":1,"type":"session.configure","session_id":"caller-1","seq":1,"ts_ms":1,"stt":"fixture","tts":"fixture","vad":"fixture"}).to_string().into())).await.unwrap();
            }
            if kind == "stt.final" && !speak_sent {
                speak_sent = true;
                socket.send(Message::Text(json!({"v":1,"type":"tts.speak","session_id":"caller-1","turn_id":value["turn_id"],"seq":2,"ts_ms":2,"utterance_id":"utt-1","text":"hello","flush":false}).to_string().into())).await.unwrap();
                socket.send(Message::Text(json!({"v":1,"type":"tts.speak","session_id":"caller-1","turn_id":value["turn_id"],"seq":3,"ts_ms":3,"utterance_id":"utt-2","text":"again","flush":true}).to_string().into())).await.unwrap();
            }
            if kind == "tts.playback"
                && value["state"] == "finished"
                && value["utterance_id"] == "utt-2"
            {
                socket.send(Message::Text(json!({"v":1,"type":"dtmf.send","session_id":"caller-1","seq":4,"ts_ms":4,"digits":"1#"}).to_string().into())).await.unwrap();
                socket.send(Message::Text(json!({"v":1,"type":"transfer","session_id":"caller-1","seq":5,"ts_ms":5,"target":"PJSIP/support"}).to_string().into())).await.unwrap();
                socket.send(Message::Text(json!({"v":1,"type":"session.end","session_id":"caller-1","seq":6,"ts_ms":6,"reason":"complete"}).to_string().into())).await.unwrap();
            }
            if kind == "session.ended" {
                break;
            }
        }
        values
    });
    let rtp_probe = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let rtp_address = rtp_probe.local_addr().unwrap();
    drop(rtp_probe);
    let config = AriRuntimeConfig::new(
        format!("http://{ari_address}/ari/"),
        "lucy-test",
        "secret",
        "lucy-voice",
        rtp_address,
        rtp_address,
        RTP_PAYLOAD_TYPE,
        RTP_CLOCK_RATE_HZ,
        Duration::from_millis(200),
        true,
    )
    .unwrap()
    .with_lifecycle(
        "caller-1".to_string(),
        format!("ws://{control_address}{CONTROL_PATH}"),
        CONTROL_TOKEN.to_string(),
        format!("ws://{ari_address}/ari/events"),
        Duration::from_millis(200),
        Duration::from_millis(200),
        Duration::from_millis(200),
        media_fixture(),
    )
    .unwrap()
    .with_rtp_allowed_cidrs("127.0.0.0/8")
    .unwrap();
    let sender = tokio::spawn(async move {
        rtp_ready_rx.await.unwrap();
        let attacker = UdpSocket::bind((Ipv4Addr::new(127, 0, 0, 2), 0))
            .await
            .unwrap();
        attacker.send_to(&rtp_packet(), rtp_address).await.unwrap();
        for _ in 0..3 {
            tokio::task::yield_now().await;
        }
        let socket = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
        socket.send_to(&rtp_packet(), rtp_address).await.unwrap();
        let mut second = rtp_packet();
        second[3] = 2;
        second[7] = 64;
        socket.send_to(&second, rtp_address).await.unwrap();
    });
    let report = run_ari_external_media_lifecycle(&config, "caller-1")
        .await
        .unwrap();
    sender.await.unwrap();
    assert!(
        report.rtp_payload_bytes >= 8,
        "a one-packet probe must not complete the lifecycle"
    );
    let values = control.await.unwrap();
    let types = values
        .iter()
        .map(|value| value["type"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(
        types.contains(&"dtmf")
            && types.contains(&"stt.final")
            && types.contains(&"transport.metrics")
            && types.contains(&"session.ended")
    );
    assert!(values.iter().all(|value| value.get("audio").is_none()));
    let final_turn = values
        .iter()
        .find(|value| value["type"] == "stt.final")
        .unwrap()["turn_id"]
        .clone();
    assert!(values
        .iter()
        .any(|value| { value["type"] == "transport.metrics" && value["turn_id"] == final_turn }));
    let playback = values
        .iter()
        .filter(|value| value["type"] == "tts.playback")
        .map(|value| {
            (
                value["utterance_id"].as_str().unwrap(),
                value["state"].as_str().unwrap(),
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(
        playback,
        [
            ("utt-1", "started"),
            ("utt-1", "finished"),
            ("utt-2", "started"),
            ("utt-2", "finished"),
        ]
    );
    assert!(values
        .windows(2)
        .all(|pair| pair[0]["seq"].as_u64() < pair[1]["seq"].as_u64()));
    let requests = state.requests.lock().unwrap().clone();
    assert!(requests.iter().any(|request| request.1.ends_with("/dtmf")));
    assert!(requests
        .iter()
        .any(|request| request.1.ends_with("/redirect")));
}
