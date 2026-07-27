use lucy_media_gateway::cpaas::call_control::{CpaasCallControlClient, CpaasCallControlConfig};
use lucy_media_gateway::cpaas::CpaasProvider;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;

const OVERSIZED_RESPONSE_BYTES: usize = 65_537;

async fn local_http_server() -> (String, tokio::sync::oneshot::Receiver<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let (request_tx, request_rx) = tokio::sync::oneshot::channel();
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
            .write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 24\r\nConnection: close\r\n\r\n{\"data\":{\"result\":\"ok\"}}",
            )
            .await
            .unwrap();
    });
    (format!("http://{address}"), request_rx)
}

async fn response_server(response: Vec<u8>) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = [0; 2_048];
        let _ = stream.read(&mut request).await.unwrap();
        stream.write_all(&response).await.unwrap();
    });
    format!("http://{address}")
}

const FIXTURE_TELNYX_KEY: &str = "fixture-telnyx-api-credential";
const FIXTURE_TWILIO_KEY: &str = "fixture-twilio-auth-token";

fn telnyx_client(base_url: &str, timeout: Duration) -> CpaasCallControlClient {
    CpaasCallControlClient::new(
        CpaasCallControlConfig::new(
            CpaasProvider::Telnyx,
            base_url,
            None,
            FIXTURE_TELNYX_KEY,
            true,
            timeout,
        )
        .unwrap(),
    )
    .unwrap()
}

#[tokio::test]
async fn telnyx_hangup_uses_bearer_action_without_logging_credentials() {
    let (base_url, request) = local_http_server().await;
    let config = CpaasCallControlConfig::new(
        CpaasProvider::Telnyx,
        &base_url,
        None,
        FIXTURE_TELNYX_KEY,
        true,
        Duration::from_secs(1),
    )
    .unwrap();
    CpaasCallControlClient::new(config)
        .unwrap()
        .hangup("v2:call/control")
        .await
        .unwrap();

    let request = request.await.unwrap();
    assert!(request.starts_with("POST /v2/calls/v2:call%2Fcontrol/actions/hangup HTTP/1.1"));
    assert!(
        request.contains(&format!("authorization: Bearer {FIXTURE_TELNYX_KEY}"))
    );
    assert_eq!(request.matches(FIXTURE_TELNYX_KEY).count(), 1);
}

#[tokio::test]
async fn twilio_hangup_uses_basic_auth_and_completed_status() {
    let (base_url, request) = local_http_server().await;
    let config = CpaasCallControlConfig::new(
        CpaasProvider::Twilio,
        &base_url,
        Some("ACREDACTED"),
        FIXTURE_TWILIO_KEY,
        true,
        Duration::from_secs(1),
    )
    .unwrap();
    CpaasCallControlClient::new(config)
        .unwrap()
        .hangup("CAREDACTED")
        .await
        .unwrap();

    let request = request.await.unwrap();
    assert!(
        request.starts_with("POST /2010-04-01/Accounts/ACREDACTED/Calls/CAREDACTED.json HTTP/1.1")
    );
    assert!(request.contains("authorization: Basic "));
    assert!(request.ends_with("Status=completed"));
    assert!(!request.contains(FIXTURE_TWILIO_KEY));
}

#[test]
fn call_control_urls_and_credentials_fail_closed() {
    for base_url in [
        "http://api.example.test",
        "https://user:pass@example.test",
        "https://example.test/path?secret=value",
        "file:///tmp/socket",
    ] {
        assert!(CpaasCallControlConfig::new(
            CpaasProvider::Telnyx,
            base_url,
            None,
            "key",
            false,
            Duration::from_secs(1),
        )
        .is_err());
    }
    assert!(CpaasCallControlConfig::new(
        CpaasProvider::Twilio,
        "https://api.example.test",
        None,
        "key",
        false,
        Duration::from_secs(1),
    )
    .is_err());
    assert!(CpaasCallControlConfig::new(
        CpaasProvider::Telnyx,
        "https://api.example.test",
        None,
        "  ",
        false,
        Duration::from_secs(1),
    )
    .is_err());
}

#[tokio::test]
async fn hangup_rejects_provider_failure_without_exposing_response_body() {
    let base_url = response_server(
        b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 24\r\nConnection: close\r\n\r\nfixture provider response"
            .to_vec(),
    )
    .await;

    let error = telnyx_client(&base_url, Duration::from_secs(1))
        .hangup("call-redacted")
        .await
        .unwrap_err()
        .to_string();

    assert_eq!(error, "call-control request returned HTTP 503");
    assert!(!error.contains("fixture provider response"));
}

#[tokio::test]
async fn hangup_rejects_oversized_success_response() {
    let body = vec![b'x'; OVERSIZED_RESPONSE_BYTES];
    let mut response = format!(
        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    )
    .into_bytes();
    response.extend_from_slice(&body);
    let base_url = response_server(response).await;

    let error = telnyx_client(&base_url, Duration::from_secs(1))
        .hangup("call-redacted")
        .await
        .unwrap_err()
        .to_string();

    assert_eq!(error, "call-control response is too large");
}

#[tokio::test]
async fn hangup_timeout_advances_without_wall_clock_sleep() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let (request_tx, request_rx) = oneshot::channel();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = [0; 2_048];
        let _ = stream.read(&mut request).await.unwrap();
        request_tx.send(()).unwrap();
        std::future::pending::<()>().await;
    });
    let client = telnyx_client(&format!("http://{address}"), Duration::from_secs(1));
    let request = tokio::spawn(async move { client.hangup("call-redacted").await });
    request_rx.await.unwrap();
    tokio::time::pause();
    tokio::time::advance(Duration::from_millis(1_001)).await;

    let error = request.await.unwrap().unwrap_err().to_string();
    assert_eq!(error, "call-control request failed");
    server.abort();
}
