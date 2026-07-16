use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use futures_util::{SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use lucy_media_gateway::asterisk::media_plane::FixtureMediaPlaneConfig;
use lucy_media_gateway::cpaas::call_control::{CpaasCallControlClient, CpaasCallControlConfig};
use lucy_media_gateway::cpaas::runtime::{
    serve_cpaas_connection, CpaasHandshakeAuth, CpaasRuntimeConfig,
};
use lucy_media_gateway::cpaas::CpaasProvider;
use serde_json::{json, Value};
use sha1::Sha1;
use std::path::PathBuf;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio_tungstenite::{
    accept_hdr_async, connect_async,
    tungstenite::{client::IntoClientRequest, handshake::server::Request, Message},
};

const STREAM_SECRET: &str = "local-stream-secret";
const CONTROL_SECRET: &str = "local-control-secret";

fn fixture_media() -> FixtureMediaPlaneConfig {
    FixtureMediaPlaneConfig {
        playback_wav: std::env::var("LUCY_AUDIO_FIXTURE_WAV")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../tests/fixtures/audio/booking_caller_8k.wav")
            }),
        transcript_timeline: std::env::var("LUCY_AUDIO_FIXTURE_TIMELINE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../tests/fixtures/audio/booking_caller.timeline.json")
            }),
        transcript_trigger_bytes: 2,
        playback_chunk_bytes: 320,
        playback_pacing_ms: 1,
    }
}

fn fixture_lines(provider: &str) -> Vec<String> {
    let path = std::env::var("LUCY_CPAAAS_FIXTURE_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/cpaas")
        })
        .join(format!("{provider}_media_stream.jsonl"));
    std::fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(str::to_string)
        .collect()
}

async fn control_server() -> (String, tokio::task::JoinHandle<Vec<Value>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(stream, |request: &Request, response| {
            assert_eq!(
                request.headers()["authorization"],
                format!("Bearer {CONTROL_SECRET}")
            );
            Ok(response)
        })
        .await
        .unwrap();
        let started: Value = serde_json::from_str(
            socket
                .next()
                .await
                .unwrap()
                .unwrap()
                .into_text()
                .unwrap()
                .as_ref(),
        )
        .unwrap();
        assert_eq!(started["type"], "session.started");
        assert!(!started.to_string().contains("<redacted-from-number>"));
        let session_id = started["session_id"].as_str().unwrap().to_string();
        socket
            .send(Message::Text(
                json!({
                    "v":1,"type":"tts.speak","session_id":session_id,"seq":1,"ts_ms":10,
                    "utterance_id":"utt-1","text":"Hello from Lucy","flush":false
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();
        let mut events = vec![started];
        while let Some(message) = socket.next().await {
            let message = message.unwrap();
            if let Message::Text(text) = message {
                let value: Value = serde_json::from_str(text.as_ref()).unwrap();
                let ended = value["type"] == "session.ended";
                events.push(value);
                if ended {
                    break;
                }
            }
        }
        events
    });
    (format!("ws://{address}"), task)
}

async fn local_call_control_server() -> (String, oneshot::Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let (request_tx, request_rx) = oneshot::channel();
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = Vec::new();
        let mut buffer = [0; 1_024];
        loop {
            let count = stream.read(&mut buffer).await.unwrap();
            if count == 0 {
                break;
            }
            request.extend_from_slice(&buffer[..count]);
            if let Some(header_end) = request.windows(4).position(|part| part == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&request[..header_end + 4]);
                let length = headers
                    .lines()
                    .find_map(|line| {
                        line.strip_prefix("content-length: ")
                            .or_else(|| line.strip_prefix("Content-Length: "))
                    })
                    .and_then(|value| value.parse::<usize>().ok())
                    .unwrap_or(0);
                if request.len() >= header_end + 4 + length {
                    break;
                }
            }
        }
        request_tx
            .send(String::from_utf8(request).unwrap())
            .unwrap();
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            .await
            .unwrap();
    });
    (format!("http://{address}"), request_rx)
}

fn twilio_signature(public_url: &str) -> String {
    let mut mac = Hmac::<Sha1>::new_from_slice(STREAM_SECRET.as_bytes()).unwrap();
    mac.update(public_url.as_bytes());
    BASE64.encode(mac.finalize().into_bytes())
}

async fn authenticated_twilio_provider(
    public_url: &str,
) -> tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>> {
    let mut request = public_url.into_client_request().unwrap();
    request.headers_mut().insert(
        "x-twilio-signature",
        twilio_signature(public_url).parse().unwrap(),
    );
    let (provider, _) = connect_async(request).await.unwrap();
    provider
}

#[tokio::test]
async fn local_telnyx_server_replays_fixture_bytes_and_returns_playback() {
    let (control_url, control_task) = control_server().await;
    let provider_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = provider_listener.local_addr().unwrap();
    let public_url = format!("ws://{address}/cpaas/telnyx");
    let mut config = CpaasRuntimeConfig::new(
        CpaasProvider::Telnyx,
        &public_url,
        STREAM_SECRET,
        &control_url,
        CONTROL_SECRET,
        true,
    )
    .unwrap();
    config.media_plane = Some(fixture_media());
    config.handshake_timeout = Duration::from_secs(2);
    config.idle_timeout = Duration::from_secs(2);
    let mut server = tokio::spawn(async move {
        let (stream, _) = provider_listener.accept().await.unwrap();
        serve_cpaas_connection(stream, config).await
    });

    let mut request = public_url.into_client_request().unwrap();
    request.headers_mut().insert(
        "x-telnyx-streaming-auth-token",
        STREAM_SECRET.parse().unwrap(),
    );
    let (mut provider, _) = connect_async(request).await.unwrap();
    let fixture = fixture_lines("telnyx");
    for line in &fixture[..3] {
        provider
            .send(Message::Text(line.clone().into()))
            .await
            .unwrap();
    }
    let mut saw_media = false;
    for _ in 0..20 {
        let message = tokio::time::timeout(Duration::from_secs(1), provider.next())
            .await
            .unwrap()
            .unwrap();
        let message = match message {
            Ok(message) => message,
            Err(error) => {
                let server_result = (&mut server).await.unwrap();
                panic!("provider stream failed: {error}; server: {server_result:?}")
            }
        };
        if let Message::Text(text) = message {
            let value: Value = serde_json::from_str(text.as_ref()).unwrap();
            if value["event"] == "media" {
                saw_media = true;
                break;
            }
        }
    }
    assert!(saw_media);
    provider
        .send(Message::Text(fixture[3].clone().into()))
        .await
        .unwrap();
    let events = control_task.await.unwrap();
    assert!(events.iter().any(|event| event["type"] == "stt.final"));
    assert!(events.iter().any(|event| event["type"] == "tts.playback"));
    assert!(events.iter().any(|event| event["type"] == "session.ended"));
    (&mut server).await.unwrap().unwrap();
}

#[tokio::test]
async fn authenticated_twilio_fixture_cancel_and_end_clear_hang_up_and_end_once() {
    let (call_control_url, call_control_request) = local_call_control_server().await;
    let control_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control_task = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(stream, |request: &Request, response| {
            assert_eq!(
                request.headers()["authorization"],
                format!("Bearer {CONTROL_SECRET}")
            );
            Ok(response)
        })
        .await
        .unwrap();
        let started: Value = serde_json::from_str(
            socket
                .next()
                .await
                .unwrap()
                .unwrap()
                .into_text()
                .unwrap()
                .as_ref(),
        )
        .unwrap();
        let session_id = started["session_id"].as_str().unwrap().to_string();
        socket
            .send(Message::Text(
                json!({
                    "v":1,"type":"tts.speak","session_id":session_id,"seq":1,"ts_ms":1,
                    "utterance_id":"utt-cancel","text":"cancel this active playback","flush":false
                })
                .to_string()
                .into(),
            ))
            .await
            .unwrap();

        let mut events = vec![started];
        let mut sent_cancel = false;
        let mut sent_end = false;
        while let Some(message) = socket.next().await {
            let Message::Text(text) = message.unwrap() else {
                continue;
            };
            let event: Value = serde_json::from_str(text.as_ref()).unwrap();
            if event["type"] == "tts.playback" && event["state"] == "started" && !sent_cancel {
                socket
                    .send(Message::Text(
                        json!({
                            "v":1,"type":"tts.cancel","session_id":session_id,"seq":2,"ts_ms":2,
                            "utterance_id":"utt-cancel"
                        })
                        .to_string()
                        .into(),
                    ))
                    .await
                    .unwrap();
                sent_cancel = true;
            }
            if event["type"] == "tts.playback" && event["state"] == "flushed" && !sent_end {
                assert_eq!(event["utterance_id"], "utt-cancel");
                socket
                    .send(Message::Text(
                        json!({
                            "v":1,"type":"session.end","session_id":session_id,"seq":3,"ts_ms":3,
                            "reason":"fixture_complete"
                        })
                        .to_string()
                        .into(),
                    ))
                    .await
                    .unwrap();
                sent_end = true;
            }
            let ended = event["type"] == "session.ended";
            events.push(event);
            if ended {
                break;
            }
        }
        events
    });

    let provider_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = provider_listener.local_addr().unwrap();
    let public_url = format!("ws://{address}/cpaas/twilio");
    let mut config = CpaasRuntimeConfig::new(
        CpaasProvider::Twilio,
        &public_url,
        STREAM_SECRET,
        &format!("ws://{control_address}"),
        CONTROL_SECRET,
        true,
    )
    .unwrap();
    config.media_plane = Some(fixture_media());
    config.call_control = Some(
        CpaasCallControlClient::new(
            CpaasCallControlConfig::new(
                CpaasProvider::Twilio,
                &call_control_url,
                Some("ACREDACTED"),
                "local-twilio-api-key",
                true,
                Duration::from_secs(1),
            )
            .unwrap(),
        )
        .unwrap(),
    );
    let server = tokio::spawn(async move {
        let (stream, _) = provider_listener.accept().await.unwrap();
        serve_cpaas_connection(stream, config).await
    });

    let mut provider = authenticated_twilio_provider(&public_url).await;
    let fixture = fixture_lines("twilio");
    for line in &fixture[..3] {
        provider
            .send(Message::Text(line.clone().into()))
            .await
            .unwrap();
    }
    let mut clear_count = 0;
    let mut saw_close = false;
    while let Some(message) = provider.next().await {
        match message.unwrap() {
            Message::Text(text) => {
                let frame: Value = serde_json::from_str(text.as_ref()).unwrap();
                if frame["event"] == "clear" {
                    clear_count += 1;
                }
            }
            Message::Close(_) => {
                saw_close = true;
                break;
            }
            _ => {}
        }
    }

    assert_eq!(clear_count, 1);
    assert!(saw_close);
    assert!(server.await.unwrap().is_ok());
    let request = call_control_request.await.unwrap();
    assert!(request.starts_with(
        "POST /2010-04-01/Accounts/ACREDACTED/Calls/%3Credacted-call-sid%3E.json HTTP/1.1"
    ));
    assert!(request.ends_with("Status=completed"));
    let events = control_task.await.unwrap();
    assert_eq!(
        events
            .iter()
            .filter(|event| event["type"] == "session.ended")
            .count(),
        1
    );
}

async fn assert_control_directives_are_rejected(directives: Vec<Value>, expected_error: &str) {
    let control_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let control_task = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(stream, |request: &Request, response| {
            assert!(!request.uri().path().is_empty());
            Ok(response)
        })
        .await
        .unwrap();
        let started: Value = serde_json::from_str(
            socket
                .next()
                .await
                .unwrap()
                .unwrap()
                .into_text()
                .unwrap()
                .as_ref(),
        )
        .unwrap();
        for directive in directives {
            let directive = match directive {
                Value::Object(mut object) => {
                    if object.get("session_id") == Some(&Value::String("$session".into())) {
                        object.insert(
                            "session_id".into(),
                            started["session_id"].as_str().unwrap().into(),
                        );
                    }
                    Value::Object(object)
                }
                value => value,
            };
            socket
                .send(Message::Text(directive.to_string().into()))
                .await
                .unwrap();
        }
    });
    let provider_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = provider_listener.local_addr().unwrap();
    let public_url = format!("ws://{address}/cpaas/twilio");
    let mut config = CpaasRuntimeConfig::new(
        CpaasProvider::Twilio,
        &public_url,
        STREAM_SECRET,
        &format!("ws://{control_address}"),
        CONTROL_SECRET,
        true,
    )
    .unwrap();
    config.media_plane = Some(fixture_media());
    let server = tokio::spawn(async move {
        let (stream, _) = provider_listener.accept().await.unwrap();
        serve_cpaas_connection(stream, config).await
    });
    let mut provider = authenticated_twilio_provider(&public_url).await;
    for line in &fixture_lines("twilio")[..2] {
        provider
            .send(Message::Text(line.clone().into()))
            .await
            .unwrap();
    }
    control_task.await.unwrap();
    let error = server.await.unwrap().unwrap_err().to_string();
    assert!(error.contains(expected_error), "{error}");
}

#[tokio::test]
async fn control_websocket_rejects_invalid_schema_wrong_session_and_non_increasing_sequences() {
    assert_control_directives_are_rejected(
        vec![json!({"type":"tts.speak"})],
        "control directive is invalid",
    )
    .await;
    assert_control_directives_are_rejected(
        vec![json!({
            "v":1,"type":"tts.cancel","session_id":"wrong-session","seq":1,"ts_ms":1,
            "utterance_id":"utt-1"
        })],
        "control session id does not match",
    )
    .await;
    assert_control_directives_are_rejected(
        vec![
            json!({
                "v":1,"type":"tts.speak","session_id":"$session","seq":1,"ts_ms":1,
                "utterance_id":"utt-1","text":"first","flush":false
            }),
            json!({
                "v":1,"type":"tts.cancel","session_id":"$session","seq":1,"ts_ms":2,
                "utterance_id":"utt-1"
            }),
        ],
        "control sequence must increase",
    )
    .await;
}

#[test]
fn provider_handshake_auth_rejects_missing_or_invalid_credentials() {
    let telnyx = CpaasHandshakeAuth::new(
        CpaasProvider::Telnyx,
        "wss://voice.example.test/cpaas/telnyx",
        STREAM_SECRET,
        false,
    )
    .unwrap();
    let missing = Request::builder().uri("/cpaas/telnyx").body(()).unwrap();
    assert!(!telnyx.authorize(&missing));

    let public_url = "wss://voice.example.test/cpaas/twilio";
    let twilio =
        CpaasHandshakeAuth::new(CpaasProvider::Twilio, public_url, STREAM_SECRET, false).unwrap();
    let mut mac = Hmac::<Sha1>::new_from_slice(STREAM_SECRET.as_bytes()).unwrap();
    mac.update(public_url.as_bytes());
    let signature = BASE64.encode(mac.finalize().into_bytes());
    let valid = Request::builder()
        .uri("/cpaas/twilio")
        .header("x-twilio-signature", signature)
        .body(())
        .unwrap();
    assert!(twilio.authorize(&valid));
    let invalid = Request::builder()
        .uri("/cpaas/twilio")
        .header("x-twilio-signature", "invalid")
        .body(())
        .unwrap();
    assert!(!twilio.authorize(&invalid));
}

#[tokio::test(start_paused = true)]
async fn handshake_deadline_advances_without_wall_clock_sleep() {
    let provider_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let provider_address = provider_listener.local_addr().unwrap();
    let (accepted_tx, accepted_rx) = oneshot::channel();
    let public_url = format!("ws://{provider_address}/cpaas/telnyx");
    let mut config = CpaasRuntimeConfig::new(
        CpaasProvider::Telnyx,
        &public_url,
        STREAM_SECRET,
        "ws://127.0.0.1:1",
        CONTROL_SECRET,
        true,
    )
    .unwrap();
    config.handshake_timeout = Duration::from_millis(10);
    let server = tokio::spawn(async move {
        let (stream, _) = provider_listener.accept().await.unwrap();
        accepted_tx.send(()).unwrap();
        serve_cpaas_connection(stream, config).await
    });
    let _stalled_peer = tokio::net::TcpStream::connect(provider_address)
        .await
        .unwrap();
    accepted_rx.await.unwrap();
    tokio::task::yield_now().await;
    tokio::time::advance(Duration::from_millis(11)).await;
    let error = server.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("handshake timed out"), "{error}");
}

#[tokio::test]
async fn idle_deadline_advances_without_wall_clock_sleep() {
    let control_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let control_address = control_listener.local_addr().unwrap();
    let (started_tx, started_rx) = oneshot::channel();
    let control_task = tokio::spawn(async move {
        let (stream, _) = control_listener.accept().await.unwrap();
        let mut socket = accept_hdr_async(stream, |_request: &Request, response| Ok(response))
            .await
            .unwrap();
        let _started = socket.next().await.unwrap().unwrap();
        started_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let provider_listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let provider_address = provider_listener.local_addr().unwrap();
    let public_url = format!("ws://{provider_address}/cpaas/twilio");
    let mut config = CpaasRuntimeConfig::new(
        CpaasProvider::Twilio,
        &public_url,
        STREAM_SECRET,
        &format!("ws://{control_address}"),
        CONTROL_SECRET,
        true,
    )
    .unwrap();
    config.handshake_timeout = Duration::from_secs(1);
    config.idle_timeout = Duration::from_secs(1);
    let mut server = tokio::spawn(async move {
        let (stream, _) = provider_listener.accept().await.unwrap();
        serve_cpaas_connection(stream, config).await
    });
    let mut provider = authenticated_twilio_provider(&public_url).await;
    for line in &fixture_lines("twilio")[..2] {
        provider
            .send(Message::Text(line.clone().into()))
            .await
            .unwrap();
    }
    tokio::select! {
        started = started_rx => started.unwrap(),
        result = &mut server => panic!("runtime ended before control start: {result:?}"),
    }
    tokio::time::pause();
    for _ in 0..3 {
        tokio::task::yield_now().await;
        tokio::time::advance(Duration::from_millis(1_001)).await;
        if server.is_finished() {
            break;
        }
    }
    if !server.is_finished() {
        server.abort();
        control_task.abort();
        panic!("CPaaS idle deadline did not fire under paused time");
    }
    let error = server.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("idle timeout"), "{error}");
    control_task.abort();
}
