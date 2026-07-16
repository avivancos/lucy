use axum::{
    body::{Body, Bytes},
    extract::State,
    http::{Request, Response, StatusCode},
    routing::any,
    Router,
};
use lucy_media_gateway::asterisk::ari_external_media::{
    AriEventMapper, AriExternalMediaClient, AriExternalMediaConfig, AriMediaFormat, RtpReceiver,
};
use serde_json::{json, Value};
use std::{
    net::{Ipv4Addr, SocketAddr},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::net::{TcpListener, UdpSocket};
use tokio::sync::oneshot;

const BASIC_AUTH: &str = "Basic bHVjeS10ZXN0OnNlY3JldA==";

#[derive(Clone, Debug)]
struct RequestRecord {
    method: String,
    path: String,
    query: String,
    authorization: String,
}

#[derive(Clone, Default)]
struct AriServerState {
    requests: Arc<Mutex<Vec<RequestRecord>>>,
    fail_add_channel: bool,
    chunked_oversized_bridge: bool,
}

async fn ari_handler(
    State(state): State<AriServerState>,
    request: Request<Body>,
) -> Response<Body> {
    let method = request.method().to_string();
    let path = request.uri().path().to_string();
    let query = request.uri().query().unwrap_or_default().to_string();
    let authorization = request
        .headers()
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default()
        .to_string();
    state.requests.lock().unwrap().push(RequestRecord {
        method: method.clone(),
        path: path.clone(),
        query,
        authorization: authorization.clone(),
    });
    if authorization != BASIC_AUTH {
        return Response::builder()
            .status(StatusCode::UNAUTHORIZED)
            .body(Body::empty())
            .unwrap();
    }
    if state.chunked_oversized_bridge && path == "/ari/bridges" {
        use futures_util::StreamExt as _;
        let chunks = futures_util::stream::iter([Ok::<_, std::convert::Infallible>(Bytes::from(
            vec![b'x'; 65_537],
        ))])
        .chain(futures_util::stream::pending());
        return Response::builder()
            .status(StatusCode::OK)
            .header("content-type", "application/json")
            .body(Body::from_stream(chunks))
            .unwrap();
    }
    let (status, body) = match (method.as_str(), path.as_str()) {
        ("POST", "/ari/bridges") => (StatusCode::OK, json!({"id": "bridge-1"})),
        ("POST", "/ari/channels/externalMedia") => (StatusCode::OK, json!({"id": "external-1"})),
        ("POST", "/ari/bridges/bridge-1/addChannel") if state.fail_add_channel => (
            StatusCode::CONFLICT,
            json!({"message": "bridge rejected channel"}),
        ),
        ("POST", "/ari/bridges/bridge-1/addChannel") => (StatusCode::NO_CONTENT, Value::Null),
        ("POST", "/ari/channels/caller-1/dtmf") | ("POST", "/ari/channels/caller-1/redirect") => {
            (StatusCode::NO_CONTENT, Value::Null)
        }
        ("DELETE", "/ari/channels/caller-1") => (StatusCode::NO_CONTENT, Value::Null),
        ("DELETE", "/ari/channels/external-1") | ("DELETE", "/ari/bridges/bridge-1") => {
            (StatusCode::NO_CONTENT, Value::Null)
        }
        _ => (StatusCode::NOT_FOUND, Value::Null),
    };
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn start_ari_server(fail_add_channel: bool) -> (SocketAddr, AriServerState) {
    let state = AriServerState {
        fail_add_channel,
        ..AriServerState::default()
    };
    let app = Router::new()
        .fallback(any(ari_handler))
        .with_state(state.clone());
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    (address, state)
}

#[tokio::test]
async fn chunked_ari_response_is_aborted_at_the_cumulative_size_limit() {
    let state = AriServerState {
        chunked_oversized_bridge: true,
        ..AriServerState::default()
    };
    let app = Router::new()
        .fallback(any(ari_handler))
        .with_state(state.clone());
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_010));
    let settings = AriExternalMediaConfig::new(
        format!("http://{address}/ari/"),
        "lucy-test".to_string(),
        "secret".to_string(),
        "lucy-voice".to_string(),
        external_host,
        AriMediaFormat::Slin16,
        Duration::from_millis(50),
        true,
    )
    .unwrap();
    let client = AriExternalMediaClient::new(settings).unwrap();

    let error = client.connect("caller-1").await.unwrap_err();

    assert!(error.to_string().contains("response exceeds size limit"));
}

fn config(address: SocketAddr, external_host: SocketAddr) -> AriExternalMediaConfig {
    AriExternalMediaConfig::new(
        format!("http://{address}/ari/"),
        "lucy-test".to_string(),
        "secret".to_string(),
        "lucy-voice".to_string(),
        external_host,
        AriMediaFormat::Slin16,
        Duration::from_secs(2),
        true,
    )
    .unwrap()
}

#[tokio::test]
async fn ari_client_creates_bridges_external_media_and_cleans_up() {
    let (address, state) = start_ari_server(false).await;
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_000));
    let client = AriExternalMediaClient::new(config(address, external_host)).unwrap();

    let session = client.connect("caller-1").await.unwrap();

    assert_eq!(session.bridge_id, "bridge-1");
    assert_eq!(session.external_channel_id, "external-1");
    client.cleanup(&session).await.unwrap();

    let requests = state.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 5);
    assert!(requests
        .iter()
        .all(|request| request.authorization == BASIC_AUTH));
    assert_eq!(requests[0].method, "POST");
    assert_eq!(requests[0].path, "/ari/bridges");
    assert!(requests[0].query.contains("type=mixing"));
    assert_eq!(requests[1].path, "/ari/channels/externalMedia");
    assert!(requests[1].query.contains("app=lucy-voice"));
    assert!(requests[1].query.contains("format=slin16"));
    assert!(requests[1].query.contains("transport=udp"));
    assert!(requests[1].query.contains("encapsulation=rtp"));
    assert!(requests[1].query.contains("direction=both"));
    assert!(requests[1]
        .query
        .contains("external_host=127.0.0.1%3A20000"));
    assert_eq!(requests[2].path, "/ari/bridges/bridge-1/addChannel");
    assert!(requests[2].query.contains("caller-1"));
    assert!(requests[2].query.contains("external-1"));
    assert_eq!(requests[3].method, "DELETE");
    assert_eq!(requests[4].method, "DELETE");
}

#[tokio::test]
async fn ari_client_accepts_a_compose_dns_name_for_the_advertised_rtp_host() {
    let (address, state) = start_ari_server(false).await;
    let settings = AriExternalMediaConfig::new(
        format!("http://{address}/ari/"),
        "lucy-test".to_string(),
        "secret".to_string(),
        "lucy-voice".to_string(),
        "lucy-media-gateway:9094".to_string(),
        AriMediaFormat::Slin16,
        Duration::from_secs(2),
        true,
    )
    .unwrap();
    let client = AriExternalMediaClient::new(settings).unwrap();

    let session = client.connect("caller-1").await.unwrap();
    client.cleanup(&session).await.unwrap();

    let requests = state.requests.lock().unwrap().clone();
    assert!(requests[1]
        .query
        .contains("external_host=lucy-media-gateway%3A9094"));
}

#[tokio::test]
async fn failed_bridge_add_rolls_back_created_resources() {
    let (address, state) = start_ari_server(true).await;
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_001));
    let client = AriExternalMediaClient::new(config(address, external_host)).unwrap();

    let error = client.connect("caller-1").await.unwrap_err();

    assert!(error.to_string().contains("add external media to bridge"));
    let requests = state.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 5);
    assert_eq!(requests[3].path, "/ari/channels/external-1");
    assert_eq!(requests[3].method, "DELETE");
    assert_eq!(requests[4].path, "/ari/bridges/bridge-1");
    assert_eq!(requests[4].method, "DELETE");
}

#[tokio::test]
async fn ari_client_executes_dtmf_transfer_and_hangup_directives() {
    let (address, state) = start_ari_server(false).await;
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_004));
    let client = AriExternalMediaClient::new(config(address, external_host)).unwrap();

    client.send_dtmf("caller-1", "12#").await.unwrap();
    client.transfer("caller-1", "PJSIP/support").await.unwrap();
    client.hangup("caller-1").await.unwrap();

    let requests = state.requests.lock().unwrap().clone();
    assert_eq!(requests.len(), 3);
    assert_eq!(requests[0].path, "/ari/channels/caller-1/dtmf");
    assert_eq!(requests[0].query, "dtmf=12%23");
    assert_eq!(requests[1].path, "/ari/channels/caller-1/redirect");
    assert_eq!(requests[1].query, "endpoint=PJSIP%2Fsupport");
    assert_eq!(requests[2].method, "DELETE");
    assert_eq!(requests[2].path, "/ari/channels/caller-1");
}

fn rtp_packet(sequence: u16, timestamp: u32, payload: &[u8]) -> Vec<u8> {
    let mut packet = vec![0x80, 118];
    packet.extend(sequence.to_be_bytes());
    packet.extend(timestamp.to_be_bytes());
    packet.extend(0x1020_3040_u32.to_be_bytes());
    packet.extend(payload);
    packet
}

#[tokio::test]
async fn bidirectional_rtp_uses_a_stable_gateway_ssrc_distinct_from_asterisk() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "bidirectional-rtp".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let asterisk = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let gateway_address = receiver.local_addr().unwrap();

    asterisk
        .send_to(&rtp_packet(10, 0, &[1, 2]), gateway_address)
        .await
        .unwrap();
    receiver.receive(0).await.unwrap();

    receiver.send_payload(&[3, 4]).await.unwrap();
    let mut first_packet = [0_u8; 64];
    let (first_length, _) = asterisk.recv_from(&mut first_packet).await.unwrap();
    receiver.send_payload(&[5, 6]).await.unwrap();
    let mut second_packet = [0_u8; 64];
    let (second_length, _) = asterisk.recv_from(&mut second_packet).await.unwrap();

    assert_eq!(first_length, 14);
    assert_eq!(second_length, 14);
    let inbound_ssrc = u32::from_be_bytes([0x10, 0x20, 0x30, 0x40]);
    let first_outbound_ssrc = u32::from_be_bytes(first_packet[8..12].try_into().unwrap());
    let second_outbound_ssrc = u32::from_be_bytes(second_packet[8..12].try_into().unwrap());
    assert_ne!(first_outbound_ssrc, inbound_ssrc);
    assert_eq!(first_outbound_ssrc, second_outbound_ssrc);
}

#[tokio::test]
async fn real_udp_rtp_socket_parses_media_and_reports_loss_and_jitter() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "rtp-session".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();

    sender
        .send_to(&rtp_packet(10, 0, &[1, 2]), receiver.local_addr().unwrap())
        .await
        .unwrap();
    let first = receiver.receive(0).await.unwrap();
    assert_eq!(first.session_id, "rtp-session");
    assert_eq!(first.sequence, 10);
    assert_eq!(first.payload, [1, 2]);

    sender
        .send_to(
            &rtp_packet(12, 320, &[3, 4]),
            receiver.local_addr().unwrap(),
        )
        .await
        .unwrap();
    sender
        .send_to(
            &rtp_packet(13, 640, &[5, 6]),
            receiver.local_addr().unwrap(),
        )
        .await
        .unwrap();
    let concealed = receiver.receive(40).await.unwrap();
    assert_eq!(concealed.sequence, 11);
    assert_eq!(concealed.payload, [0, 0]);
    let second = receiver.receive(60).await.unwrap();
    assert_eq!(second.sequence, 12);

    let metrics = receiver.transport_metrics(3_000).unwrap();
    assert_eq!(metrics.message_type(), "transport.metrics");
    assert!(metrics.as_value()["packet_loss"].as_f64().unwrap() > 0.0);
    assert!(metrics.as_value()["jitter_ms"].as_f64().unwrap() > 0.0);
    assert_eq!(metrics.as_value()["rtt_ms"], 0.0);
}

#[tokio::test]
async fn rtp_reorders_ordinary_udp_packets_before_emitting_them() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "reordered-rtp".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let destination = receiver.local_addr().unwrap();

    sender
        .send_to(&rtp_packet(10, 0, &[1, 2]), destination)
        .await
        .unwrap();
    assert_eq!(receiver.receive(0).await.unwrap().sequence, 10);

    sender
        .send_to(&rtp_packet(12, 640, &[5, 6]), destination)
        .await
        .unwrap();
    sender
        .send_to(&rtp_packet(11, 320, &[3, 4]), destination)
        .await
        .unwrap();

    let reordered = receiver.receive(20).await.unwrap();
    assert_eq!(reordered.sequence, 11);
    assert_eq!(reordered.payload, [3, 4]);
    let buffered = receiver.receive(40).await.unwrap();
    assert_eq!(buffered.sequence, 12);
    assert_eq!(buffered.payload, [5, 6]);
    assert_eq!(receiver.metrics_snapshot().1, 0.0);
}

#[tokio::test]
async fn rtp_reorders_packets_across_the_sequence_wraparound() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "wrapped-rtp".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let destination = receiver.local_addr().unwrap();

    sender
        .send_to(&rtp_packet(u16::MAX, 0, &[1, 2]), destination)
        .await
        .unwrap();
    assert_eq!(receiver.receive(0).await.unwrap().sequence, u16::MAX);

    sender
        .send_to(&rtp_packet(1, 640, &[5, 6]), destination)
        .await
        .unwrap();
    sender
        .send_to(&rtp_packet(0, 320, &[3, 4]), destination)
        .await
        .unwrap();

    assert_eq!(receiver.receive(20).await.unwrap().sequence, 0);
    assert_eq!(receiver.receive(40).await.unwrap().sequence, 1);
}

#[tokio::test]
async fn rtp_rejects_duplicates_and_packets_outside_the_bounded_reorder_window() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "bounded-reorder".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let destination = receiver.local_addr().unwrap();

    sender
        .send_to(&rtp_packet(10, 0, &[1, 2]), destination)
        .await
        .unwrap();
    receiver.receive(0).await.unwrap();

    sender
        .send_to(&rtp_packet(10, 0, &[1, 2]), destination)
        .await
        .unwrap();
    let duplicate = receiver.receive(20).await.unwrap_err();
    assert!(duplicate.to_string().contains("duplicate or out of order"));

    sender
        .send_to(&rtp_packet(14, 1_280, &[3, 4]), destination)
        .await
        .unwrap();
    let overflow = receiver.receive(40).await.unwrap_err();
    assert!(overflow.to_string().contains("outside the reorder window"));
}

#[test]
fn ari_events_map_dtmf_and_hangup_without_accepting_other_channels() {
    let mut mapper = AriEventMapper::new("external-1".to_string());
    let dtmf = mapper
        .handle(
            json!({
                "type": "ChannelDtmfReceived",
                "digit": "#",
                "channel": {"id": "external-1"}
            }),
            4_000,
        )
        .unwrap()
        .unwrap();
    assert_eq!(dtmf.message_type(), "dtmf");
    assert_eq!(dtmf.as_value()["digit"], "#");

    assert!(mapper
        .handle(
            json!({
                "type": "ChannelDestroyed",
                "channel": {"id": "other"}
            }),
            4_010,
        )
        .unwrap()
        .is_none());

    let ended = mapper
        .handle(
            json!({
                "type": "ChannelDestroyed",
                "channel": {"id": "external-1"}
            }),
            4_020,
        )
        .unwrap()
        .unwrap();
    assert_eq!(ended.message_type(), "session.ended");
    assert_eq!(ended.as_value()["reason"], "ari_channel_destroyed");
}

#[test]
fn ari_config_redacts_credentials_and_rejects_unsafe_inputs() {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, 8088));
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_002));
    let settings = config(address, external_host);
    assert!(!format!("{settings:?}").contains("secret"));

    let error = AriExternalMediaConfig::new(
        "ftp://127.0.0.1/ari/".to_string(),
        "user".to_string(),
        "secret".to_string(),
        "app".to_string(),
        external_host,
        AriMediaFormat::Slin16,
        Duration::from_secs(2),
        true,
    )
    .unwrap_err();
    assert!(error.to_string().contains("http or https"));

    let error = AriExternalMediaConfig::new(
        format!("http://{address}/ari/"),
        "user".to_string(),
        "secret".to_string(),
        "app".to_string(),
        external_host,
        AriMediaFormat::Slin16,
        Duration::ZERO,
        true,
    )
    .unwrap_err();
    assert!(error.to_string().contains("timeout must be positive"));

    let error = AriExternalMediaConfig::new(
        format!("http://{address}/ari/"),
        "user".to_string(),
        "secret".to_string(),
        "app".to_string(),
        external_host,
        AriMediaFormat::Slin16,
        Duration::from_secs(2),
        false,
    )
    .unwrap_err();
    assert!(error.to_string().contains("local-lab override"));

    for invalid_host in [
        "lucy-media-gateway",
        "lucy-media-gateway:0",
        "user@lucy-media-gateway:9094",
        "lucy-media-gateway:9094/path",
    ] {
        let error = AriExternalMediaConfig::new(
            format!("http://{address}/ari/"),
            "user".to_string(),
            "secret".to_string(),
            "app".to_string(),
            invalid_host,
            AriMediaFormat::Slin16,
            Duration::from_secs(2),
            true,
        )
        .unwrap_err();
        assert!(error.to_string().contains("external RTP host"));
    }
}

#[tokio::test]
async fn ari_connection_failure_is_bounded_and_named() {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_003));
    let client = AriExternalMediaClient::new(config(address, external_host)).unwrap();

    let error = client.connect("caller-1").await.unwrap_err();

    assert!(error.to_string().contains("create mixing bridge"));
    assert!(!error.to_string().contains("secret"));
}

#[tokio::test(start_paused = true)]
async fn ari_request_timeout_closes_a_connected_server_that_never_responds() {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let address = listener.local_addr().unwrap();
    let (request_received_tx, request_received_rx) = oneshot::channel();
    let (peer_closed_tx, peer_closed_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = Vec::new();
        while !request.ends_with(b"\r\n\r\n") {
            let mut byte = [0_u8; 1];
            let bytes = tokio::io::AsyncReadExt::read(&mut stream, &mut byte)
                .await
                .unwrap();
            assert_eq!(bytes, 1, "ARI client closed before sending HTTP headers");
            request.push(byte[0]);
        }
        request_received_tx.send(()).unwrap();
        let mut byte = [0_u8; 1];
        let bytes = tokio::io::AsyncReadExt::read(&mut stream, &mut byte)
            .await
            .unwrap();
        peer_closed_tx.send(bytes).unwrap();
    });
    let external_host = SocketAddr::from((Ipv4Addr::LOCALHOST, 20_005));
    let settings = AriExternalMediaConfig::new(
        format!("http://{address}/ari/"),
        "lucy-test".to_string(),
        "secret".to_string(),
        "lucy-voice".to_string(),
        external_host,
        AriMediaFormat::Slin16,
        Duration::from_millis(50),
        true,
    )
    .unwrap();
    let client = AriExternalMediaClient::new(settings).unwrap();
    let request = tokio::spawn(async move { client.connect("caller-1").await });
    let keep_time_frozen = tokio::spawn(async {
        loop {
            tokio::task::yield_now().await;
        }
    });

    request_received_rx.await.unwrap();
    keep_time_frozen.abort();
    tokio::time::advance(Duration::from_millis(51)).await;
    tokio::task::yield_now().await;
    let error = request.await.unwrap().unwrap_err().to_string();
    assert!(error.contains("create mixing bridge"), "{error}");
    assert!(error.contains("timed out"), "{error}");
    assert!(!error.contains("secret"), "{error}");
    assert_eq!(peer_closed_rx.await.unwrap(), 0);
    server.await.unwrap();
}

async fn receive_invalid_rtp(packet: Vec<u8>) -> String {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "invalid-rtp".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    sender
        .send_to(&packet, receiver.local_addr().unwrap())
        .await
        .unwrap();
    receiver.receive(0).await.unwrap_err().to_string()
}

#[tokio::test]
async fn malformed_first_rtp_packet_does_not_poison_source_lock() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "poison-test".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let attacker = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let asterisk = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();

    attacker
        .send_to(&[0x80], receiver.local_addr().unwrap())
        .await
        .unwrap();
    assert!(receiver.receive(0).await.is_err());

    asterisk
        .send_to(&rtp_packet(1, 0, &[1, 2]), receiver.local_addr().unwrap())
        .await
        .unwrap();
    let frame = receiver.receive(20).await.unwrap();
    assert_eq!(frame.sequence, 1);
    assert_eq!(frame.payload, [1, 2]);
}

#[tokio::test]
async fn rejected_attacker_packet_cannot_lock_rtp_playback_destination() {
    let mut receiver = RtpReceiver::bind_allowed(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "attacker-first".to_string(),
        118,
        16_000,
        "127.0.0.2/32",
    )
    .await
    .unwrap();
    let attacker = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let asterisk = UdpSocket::bind((Ipv4Addr::new(127, 0, 0, 2), 0))
        .await
        .unwrap();
    let destination = receiver.local_addr().unwrap();

    attacker
        .send_to(&rtp_packet(1, 0, &[1, 2]), destination)
        .await
        .unwrap();
    assert!(receiver.send_payload(&[3, 4]).await.is_err());

    asterisk
        .send_to(&rtp_packet(1, 0, &[1, 2]), destination)
        .await
        .unwrap();
    let frame = receiver.receive(20).await.unwrap();
    assert_eq!(frame.payload, [1, 2]);
    receiver.send_payload(&[3, 4]).await.unwrap();
    let mut playback = [0_u8; 64];
    let (_, playback_source) = asterisk.recv_from(&mut playback).await.unwrap();
    assert_eq!(playback_source, destination);
}

#[test]
fn rtp_allowed_cidrs_reject_empty_and_invalid_values() {
    for allowed_cidrs in ["", "not-a-cidr"] {
        let error = RtpReceiver::validate_allowed_cidrs(allowed_cidrs).unwrap_err();
        assert!(error.to_string().contains("RTP allowed CIDRs"));
    }
}

#[tokio::test]
async fn malformed_rtp_packets_fail_closed() {
    let mut wrong_version = rtp_packet(1, 0, &[1, 2]);
    wrong_version[0] = 0x40;
    let mut wrong_payload_type = rtp_packet(1, 0, &[1, 2]);
    wrong_payload_type[1] = 117;

    for (packet, expected) in [
        (vec![0; 11], "RTP packet is truncated"),
        (wrong_version, "RTP version must be 2"),
        (wrong_payload_type, "unexpected RTP payload type"),
        (rtp_packet(1, 0, &[]), "RTP payload is empty"),
    ] {
        let error = receive_invalid_rtp(packet).await;
        assert!(error.contains(expected), "{error}");
    }
}

#[tokio::test]
async fn rtp_source_changes_and_duplicate_sequences_are_rejected() {
    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "source-lock".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let first_sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    let second_sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    first_sender
        .send_to(&rtp_packet(1, 0, &[1, 2]), receiver.local_addr().unwrap())
        .await
        .unwrap();
    receiver.receive(0).await.unwrap();
    second_sender
        .send_to(&rtp_packet(2, 320, &[3, 4]), receiver.local_addr().unwrap())
        .await
        .unwrap();
    let error = receiver.receive(20).await.unwrap_err();
    assert!(error.to_string().contains("packet source changed"));

    let mut receiver = RtpReceiver::bind(
        SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        "sequence-lock".to_string(),
        118,
        16_000,
    )
    .await
    .unwrap();
    let sender = UdpSocket::bind((Ipv4Addr::LOCALHOST, 0)).await.unwrap();
    for arrival_ms in [0, 20] {
        sender
            .send_to(&rtp_packet(7, 0, &[1, 2]), receiver.local_addr().unwrap())
            .await
            .unwrap();
        let result = receiver.receive(arrival_ms).await;
        if arrival_ms == 0 {
            result.unwrap();
        } else {
            assert!(result
                .unwrap_err()
                .to_string()
                .contains("duplicate or out of order"));
        }
    }
}

#[test]
fn ari_event_mapper_rejects_invalid_dtmf_and_events_after_hangup() {
    let mut mapper = AriEventMapper::new("external-1".to_string());
    let error = mapper
        .handle(
            json!({
                "type": "ChannelDtmfReceived",
                "digit": "invalid",
                "channel": {"id": "external-1"}
            }),
            5_000,
        )
        .unwrap_err();
    assert!(error.to_string().contains("one valid digit"));

    mapper
        .handle(
            json!({
                "type": "StasisEnd",
                "channel": {"id": "external-1"}
            }),
            5_010,
        )
        .unwrap();
    let error = mapper
        .handle(
            json!({
                "type": "ChannelDestroyed",
                "channel": {"id": "external-1"}
            }),
            5_020,
        )
        .unwrap_err();
    assert!(error
        .to_string()
        .contains("event received after channel destruction"));
}
