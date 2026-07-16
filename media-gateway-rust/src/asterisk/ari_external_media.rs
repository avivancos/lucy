use crate::control::schema::ControlMessage;
use base64::{engine::general_purpose::STANDARD, Engine as _};
use futures_util::StreamExt;
use ipnet::IpNet;
use reqwest::{Client, Url};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::fmt;
use std::net::{IpAddr, SocketAddr};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;
use tokio::net::UdpSocket;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, http::HeaderValue};
use url::Host;

use super::{CONTROL_SCHEMA_VERSION, VALID_DTMF};

const ARI_BRIDGES_PATH: &str = "bridges";
const ARI_EXTERNAL_MEDIA_PATH: &str = "channels/externalMedia";
const ARI_CHANNELS_PATH: &str = "channels";
const MIXING_BRIDGE_TYPE: &str = "mixing";
const RTP_TRANSPORT: &str = "udp";
const RTP_ENCAPSULATION: &str = "rtp";
const BOTH_DIRECTIONS: &str = "both";
const CLIENT_CONNECTION_TYPE: &str = "client";
const ARI_DESTROYED_REASON: &str = "ari_channel_destroyed";
const RTP_HEADER_BYTES: usize = 12;
const RTP_VERSION: u8 = 2;
const MAX_RTP_DATAGRAM_BYTES: usize = 65_535;
const MAX_ARI_RESPONSE_BYTES: usize = 65_536;
const RTP_REORDER_BUFFER_PACKETS: usize = 2;
pub(crate) const DEFAULT_ARI_RTP_ALLOWED_CIDRS: &str =
    "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16";
static NEXT_OUTBOUND_RTP_SSRC: AtomicU32 = AtomicU32::new(1);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AriMediaFormat {
    Slin16,
}

impl AriMediaFormat {
    fn as_str(self) -> &'static str {
        match self {
            Self::Slin16 => "slin16",
        }
    }
}

#[derive(Clone)]
pub struct AriExternalMediaConfig {
    base_url: Url,
    username: String,
    password: String,
    app: String,
    external_host: String,
    format: AriMediaFormat,
    request_timeout: Duration,
    allow_insecure_http: bool,
}

impl fmt::Debug for AriExternalMediaConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AriExternalMediaConfig")
            .field("base_url", &self.base_url)
            .field("username", &self.username)
            .field("password", &"[redacted]")
            .field("app", &self.app)
            .field("external_host", &self.external_host)
            .field("format", &self.format)
            .field("request_timeout", &self.request_timeout)
            .field("allow_insecure_http", &self.allow_insecure_http)
            .finish()
    }
}

impl AriExternalMediaConfig {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base_url: String,
        username: String,
        password: String,
        app: String,
        external_host: impl fmt::Display,
        format: AriMediaFormat,
        request_timeout: Duration,
        allow_insecure_http: bool,
    ) -> Result<Self, AriExternalMediaError> {
        let mut base_url = Url::parse(&base_url)
            .map_err(|_| AriExternalMediaError::config("ARI base URL is invalid"))?;
        if !matches!(base_url.scheme(), "http" | "https") {
            return Err(AriExternalMediaError::config(
                "ARI base URL must use http or https",
            ));
        }
        if base_url.host_str().is_none() {
            return Err(AriExternalMediaError::config(
                "ARI base URL must include a host",
            ));
        }
        if base_url.scheme() == "http" && !allow_insecure_http {
            return Err(AriExternalMediaError::config(
                "ARI HTTP requires an explicit local-lab override",
            ));
        }
        if !base_url.username().is_empty()
            || base_url.password().is_some()
            || base_url.query().is_some()
            || base_url.fragment().is_some()
        {
            return Err(AriExternalMediaError::config(
                "ARI base URL cannot contain credentials, query, or fragment",
            ));
        }
        if !base_url.path().ends_with('/') {
            let normalized = format!("{}/", base_url.path());
            base_url.set_path(&normalized);
        }
        let external_host = normalize_external_host(&external_host.to_string())?;
        validate_identity("ARI username", &username)?;
        validate_secret(&password)?;
        validate_identity("ARI app", &app)?;
        if request_timeout.is_zero() {
            return Err(AriExternalMediaError::config(
                "ARI request timeout must be positive",
            ));
        }
        Ok(Self {
            base_url,
            username,
            password,
            app,
            external_host,
            format,
            request_timeout,
            allow_insecure_http,
        })
    }

    pub(crate) fn origin_host(&self) -> &str {
        self.base_url
            .host_str()
            .expect("ARI base URL host is validated at construction")
    }

    pub(crate) fn events_request(
        &self,
        events_url: &str,
    ) -> Result<tokio_tungstenite::tungstenite::http::Request<()>, AriExternalMediaError> {
        let url = Url::parse(events_url)
            .map_err(|_| AriExternalMediaError::config("ARI events WebSocket URL is invalid"))?;
        if !matches!(url.scheme(), "ws" | "wss")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.fragment().is_some()
        {
            return Err(AriExternalMediaError::config(
                "ARI events WebSocket URL must be a credential-free ws or wss URL",
            ));
        }
        if !url.host_str().is_some_and(|host| {
            self.base_url
                .host_str()
                .is_some_and(|ari_host| host.eq_ignore_ascii_case(ari_host))
        }) {
            return Err(AriExternalMediaError::config(
                "ARI events WebSocket must use the configured ARI host",
            ));
        }
        let expected_scheme = if self.base_url.scheme() == "https" {
            "wss"
        } else {
            "ws"
        };
        if url.scheme() != expected_scheme {
            return Err(AriExternalMediaError::config(
                "ARI events WebSocket scheme must match the configured ARI origin",
            ));
        }
        if url.port_or_known_default() != self.base_url.port_or_known_default() {
            return Err(AriExternalMediaError::config(
                "ARI events WebSocket must use the configured ARI port",
            ));
        }
        if url.scheme() == "ws" && !url_host_is_loopback(&url) && !self.allow_insecure_http {
            return Err(AriExternalMediaError::config(
                "non-loopback ARI events WebSocket requires the local-lab override",
            ));
        }
        let mut request = events_url.into_client_request().map_err(|_| {
            AriExternalMediaError::config("ARI events WebSocket request is invalid")
        })?;
        let token = STANDARD.encode(format!("{}:{}", self.username, self.password));
        let header = HeaderValue::from_str(&format!("Basic {token}"))
            .map_err(|_| AriExternalMediaError::config("ARI events authorization is invalid"))?;
        request.headers_mut().insert("authorization", header);
        Ok(request)
    }
}

fn url_host_is_loopback(url: &Url) -> bool {
    match url.host() {
        Some(Host::Ipv4(address)) => address.is_loopback(),
        Some(Host::Ipv6(address)) => address.is_loopback(),
        Some(Host::Domain(domain)) => domain.eq_ignore_ascii_case("localhost"),
        None => false,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AriExternalMediaSession {
    pub bridge_id: String,
    pub external_channel_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AriExternalMediaError(String);

impl AriExternalMediaError {
    fn config(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    fn operation(operation: &str, detail: impl fmt::Display) -> Self {
        Self(format!("ARI {operation} failed: {detail}"))
    }
}

impl fmt::Display for AriExternalMediaError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for AriExternalMediaError {}

pub struct AriExternalMediaClient {
    config: AriExternalMediaConfig,
    client: Client,
}

impl AriExternalMediaClient {
    pub fn new(config: AriExternalMediaConfig) -> Result<Self, AriExternalMediaError> {
        let client = Client::builder()
            .timeout(config.request_timeout)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| {
                AriExternalMediaError::operation("client construction", "transport error")
            })?;
        Ok(Self { config, client })
    }

    pub async fn connect(
        &self,
        caller_channel_id: &str,
    ) -> Result<AriExternalMediaSession, AriExternalMediaError> {
        validate_identity("caller channel id", caller_channel_id)?;
        let bridge_id = self
            .post_for_id(
                ARI_BRIDGES_PATH,
                &[("type", MIXING_BRIDGE_TYPE.to_string())],
                "create mixing bridge",
            )
            .await?;
        let external_query = [
            ("app", self.config.app.clone()),
            ("external_host", self.config.external_host.to_string()),
            ("format", self.config.format.as_str().to_string()),
            ("transport", RTP_TRANSPORT.to_string()),
            ("encapsulation", RTP_ENCAPSULATION.to_string()),
            ("direction", BOTH_DIRECTIONS.to_string()),
            ("connection_type", CLIENT_CONNECTION_TYPE.to_string()),
        ];
        let external_channel_id = match self
            .post_for_id(
                ARI_EXTERNAL_MEDIA_PATH,
                &external_query,
                "create external media channel",
            )
            .await
        {
            Ok(channel_id) => channel_id,
            Err(error) => {
                let _ = self.delete_bridge(&bridge_id).await;
                return Err(error);
            }
        };
        let channels = format!("{caller_channel_id},{external_channel_id}");
        let add_path = format!("bridges/{bridge_id}/addChannel");
        if let Err(error) = self
            .send_empty(
                reqwest::Method::POST,
                &add_path,
                &[("channel", channels)],
                "add external media to bridge",
            )
            .await
        {
            let session = AriExternalMediaSession {
                bridge_id,
                external_channel_id,
            };
            let _ = self.cleanup(&session).await;
            return Err(error);
        }
        Ok(AriExternalMediaSession {
            bridge_id,
            external_channel_id,
        })
    }

    pub async fn cleanup(
        &self,
        session: &AriExternalMediaSession,
    ) -> Result<(), AriExternalMediaError> {
        let channel_result = self.delete_channel(&session.external_channel_id).await;
        let bridge_result = self.delete_bridge(&session.bridge_id).await;
        channel_result.and(bridge_result)
    }

    pub async fn send_dtmf(
        &self,
        channel_id: &str,
        digits: &str,
    ) -> Result<(), AriExternalMediaError> {
        validate_identity("DTMF channel id", channel_id)?;
        if digits.is_empty()
            || !digits
                .as_bytes()
                .iter()
                .all(|digit| VALID_DTMF.contains(digit))
        {
            return Err(AriExternalMediaError::config(
                "ARI DTMF contains an invalid digit",
            ));
        }
        let path = format!("{ARI_CHANNELS_PATH}/{channel_id}/dtmf");
        self.send_empty(
            reqwest::Method::POST,
            &path,
            &[("dtmf", digits.to_ascii_uppercase())],
            "send DTMF",
        )
        .await
    }

    pub async fn transfer(
        &self,
        channel_id: &str,
        target: &str,
    ) -> Result<(), AriExternalMediaError> {
        validate_identity("transfer channel id", channel_id)?;
        validate_endpoint(target)?;
        let path = format!("{ARI_CHANNELS_PATH}/{channel_id}/redirect");
        self.send_empty(
            reqwest::Method::POST,
            &path,
            &[("endpoint", target.to_string())],
            "redirect channel",
        )
        .await
    }

    pub async fn hangup(&self, channel_id: &str) -> Result<(), AriExternalMediaError> {
        validate_identity("hangup channel id", channel_id)?;
        self.delete_channel(channel_id).await
    }

    async fn post_for_id(
        &self,
        path: &str,
        query: &[(&str, String)],
        operation: &str,
    ) -> Result<String, AriExternalMediaError> {
        let response = self
            .request(reqwest::Method::POST, path, query, operation)
            .await?;
        if response
            .content_length()
            .is_some_and(|length| length > MAX_ARI_RESPONSE_BYTES as u64)
        {
            return Err(AriExternalMediaError::operation(
                operation,
                "response exceeds size limit",
            ));
        }
        let mut chunks = response.bytes_stream();
        let mut body = Vec::new();
        while let Some(chunk) = chunks.next().await {
            let chunk = chunk.map_err(|error| ari_transport_error(operation, &error))?;
            if body.len().saturating_add(chunk.len()) > MAX_ARI_RESPONSE_BYTES {
                return Err(AriExternalMediaError::operation(
                    operation,
                    "response exceeds size limit",
                ));
            }
            body.extend_from_slice(&chunk);
        }
        let payload: Value = serde_json::from_slice(&body)
            .map_err(|_| AriExternalMediaError::operation(operation, "invalid JSON response"))?;
        payload
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .map(str::to_string)
            .ok_or_else(|| AriExternalMediaError::operation(operation, "response has no id"))
    }

    async fn send_empty(
        &self,
        method: reqwest::Method,
        path: &str,
        query: &[(&str, String)],
        operation: &str,
    ) -> Result<(), AriExternalMediaError> {
        self.request(method, path, query, operation).await?;
        Ok(())
    }

    async fn request(
        &self,
        method: reqwest::Method,
        path: &str,
        query: &[(&str, String)],
        operation: &str,
    ) -> Result<reqwest::Response, AriExternalMediaError> {
        let url = self
            .config
            .base_url
            .join(path)
            .map_err(|error| AriExternalMediaError::operation(operation, error))?;
        let response = self
            .client
            .request(method, url)
            .basic_auth(&self.config.username, Some(&self.config.password))
            .query(query)
            .send()
            .await
            .map_err(|error| ari_transport_error(operation, &error))?;
        if !response.status().is_success() {
            return Err(AriExternalMediaError::operation(
                operation,
                format!("HTTP {}", response.status().as_u16()),
            ));
        }
        Ok(response)
    }

    async fn delete_channel(&self, channel_id: &str) -> Result<(), AriExternalMediaError> {
        validate_identity("external channel id", channel_id)?;
        let path = format!("{ARI_CHANNELS_PATH}/{channel_id}");
        self.send_empty(
            reqwest::Method::DELETE,
            &path,
            &[],
            "delete external media channel",
        )
        .await
    }

    async fn delete_bridge(&self, bridge_id: &str) -> Result<(), AriExternalMediaError> {
        validate_identity("bridge id", bridge_id)?;
        let path = format!("{ARI_BRIDGES_PATH}/{bridge_id}");
        self.send_empty(reqwest::Method::DELETE, &path, &[], "delete mixing bridge")
            .await
    }
}

fn ari_transport_error(operation: &str, error: &reqwest::Error) -> AriExternalMediaError {
    let detail = if error.is_timeout() {
        "request timed out"
    } else {
        "transport error"
    };
    AriExternalMediaError::operation(operation, detail)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RtpMediaFrame {
    pub session_id: String,
    pub payload_type: u8,
    pub sequence: u16,
    pub timestamp: u32,
    pub payload: Vec<u8>,
}

struct BufferedRtp {
    payload_type: u8,
    sequence: u16,
    timestamp: u32,
    payload: Vec<u8>,
}

pub struct RtpReceiver {
    socket: UdpSocket,
    session_id: String,
    expected_payload_type: u8,
    clock_rate_hz: u32,
    expected_source: Option<SocketAddr>,
    expected_ssrc: Option<u32>,
    allowed_source_cidrs: Box<[IpNet]>,
    trusted_source_ips: Box<[IpAddr]>,
    source: Option<SocketAddr>,
    ssrc: Option<u32>,
    next_sequence: Option<u16>,
    last_emitted_timestamp: Option<u32>,
    last_payload_len: usize,
    reorder_buffer: HashMap<u16, BufferedRtp>,
    last_transit: Option<i64>,
    received: u64,
    lost: u64,
    jitter_timestamp_units: f64,
    control_seq: u64,
    outbound_ssrc: u32,
    transmit_sequence: u16,
    transmit_timestamp: u32,
}

impl RtpReceiver {
    pub async fn bind(
        address: SocketAddr,
        session_id: String,
        expected_payload_type: u8,
        clock_rate_hz: u32,
    ) -> Result<Self, AriExternalMediaError> {
        Self::bind_expected(
            address,
            session_id,
            expected_payload_type,
            clock_rate_hz,
            None,
            None,
        )
        .await
    }

    pub async fn bind_expected(
        address: SocketAddr,
        session_id: String,
        expected_payload_type: u8,
        clock_rate_hz: u32,
        expected_source: Option<SocketAddr>,
        expected_ssrc: Option<u32>,
    ) -> Result<Self, AriExternalMediaError> {
        Self::bind_with_allowed(
            address,
            session_id,
            expected_payload_type,
            clock_rate_hz,
            DEFAULT_ARI_RTP_ALLOWED_CIDRS,
            expected_source,
            expected_ssrc,
        )
        .await
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn bind_with_allowed(
        address: SocketAddr,
        session_id: String,
        expected_payload_type: u8,
        clock_rate_hz: u32,
        allowed_cidrs: &str,
        expected_source: Option<SocketAddr>,
        expected_ssrc: Option<u32>,
    ) -> Result<Self, AriExternalMediaError> {
        Self::bind_with_allowed_and_trusted_ips(
            address,
            session_id,
            expected_payload_type,
            clock_rate_hz,
            allowed_cidrs,
            &[],
            expected_source,
            expected_ssrc,
        )
        .await
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn bind_with_allowed_and_trusted_ips(
        address: SocketAddr,
        session_id: String,
        expected_payload_type: u8,
        clock_rate_hz: u32,
        allowed_cidrs: &str,
        trusted_source_ips: &[IpAddr],
        expected_source: Option<SocketAddr>,
        expected_ssrc: Option<u32>,
    ) -> Result<Self, AriExternalMediaError> {
        validate_identity("RTP session id", &session_id)?;
        if expected_payload_type > 127 {
            return Err(AriExternalMediaError::config(
                "RTP payload type must be at most 127",
            ));
        }
        if clock_rate_hz == 0 {
            return Err(AriExternalMediaError::config(
                "RTP clock rate must be positive",
            ));
        }
        let allowed_source_cidrs = Self::validate_allowed_cidrs(allowed_cidrs)?;
        let socket = UdpSocket::bind(address)
            .await
            .map_err(|error| AriExternalMediaError::operation("bind RTP socket", error))?;
        Ok(Self {
            socket,
            session_id,
            expected_payload_type,
            clock_rate_hz,
            expected_source,
            expected_ssrc,
            allowed_source_cidrs: allowed_source_cidrs.into_boxed_slice(),
            trusted_source_ips: trusted_source_ips.to_vec().into_boxed_slice(),
            source: None,
            ssrc: None,
            next_sequence: None,
            last_emitted_timestamp: None,
            last_payload_len: 0,
            reorder_buffer: HashMap::with_capacity(RTP_REORDER_BUFFER_PACKETS),
            last_transit: None,
            received: 0,
            lost: 0,
            jitter_timestamp_units: 0.0,
            control_seq: 0,
            outbound_ssrc: next_outbound_ssrc(),
            transmit_sequence: 0,
            transmit_timestamp: 0,
        })
    }

    pub async fn bind_allowed(
        address: SocketAddr,
        session_id: String,
        expected_payload_type: u8,
        clock_rate_hz: u32,
        allowed_cidrs: &str,
    ) -> Result<Self, AriExternalMediaError> {
        Self::bind_with_allowed(
            address,
            session_id,
            expected_payload_type,
            clock_rate_hz,
            allowed_cidrs,
            None,
            None,
        )
        .await
    }

    pub fn validate_allowed_cidrs(
        allowed_cidrs: &str,
    ) -> Result<Vec<IpNet>, AriExternalMediaError> {
        let cidrs = allowed_cidrs
            .split(',')
            .map(str::trim)
            .filter(|cidr| !cidr.is_empty())
            .map(|cidr| {
                cidr.parse::<IpNet>().map_err(|_| {
                    AriExternalMediaError::config("RTP allowed CIDRs contains an invalid CIDR")
                })
            })
            .collect::<Result<Vec<_>, _>>()?;
        if cidrs.is_empty() {
            return Err(AriExternalMediaError::config(
                "RTP allowed CIDRs cannot be empty",
            ));
        }
        Ok(cidrs)
    }

    pub fn local_addr(&self) -> Result<SocketAddr, AriExternalMediaError> {
        self.socket
            .local_addr()
            .map_err(|error| AriExternalMediaError::operation("read RTP local address", error))
    }

    pub async fn receive(
        &mut self,
        arrival_ms: u64,
    ) -> Result<RtpMediaFrame, AriExternalMediaError> {
        loop {
            if let Some(sequence) = self.next_sequence {
                if let Some(packet) = self.reorder_buffer.remove(&sequence) {
                    return Ok(self.emit_packet(packet));
                }
                if self.reorder_buffer.len() >= RTP_REORDER_BUFFER_PACKETS {
                    return self.emit_silence();
                }
            }

            let mut packet = vec![0_u8; MAX_RTP_DATAGRAM_BYTES];
            let (length, source) = self
                .socket
                .recv_from(&mut packet)
                .await
                .map_err(|error| AriExternalMediaError::operation("receive RTP", error))?;
            if !self
                .allowed_source_cidrs
                .iter()
                .any(|cidr| cidr.contains(&source.ip()))
            {
                continue;
            }
            if !self.trusted_source_ips.is_empty()
                && !self.trusted_source_ips.contains(&source.ip())
            {
                continue;
            }
            if self
                .expected_source
                .is_some_and(|expected| expected != source)
            {
                return Err(AriExternalMediaError::config(
                    "RTP packet source does not match configured peer",
                ));
            }
            packet.truncate(length);
            let parsed = parse_rtp(&packet, self.expected_payload_type)?;
            if self
                .expected_ssrc
                .is_some_and(|expected| expected != parsed.ssrc)
            {
                return Err(AriExternalMediaError::config(
                    "RTP SSRC does not match configured stream",
                ));
            }
            if self.source.is_some_and(|expected| expected != source) {
                return Err(AriExternalMediaError::config(
                    "RTP packet source changed during session",
                ));
            }
            if self.ssrc.is_some_and(|expected| expected != parsed.ssrc) {
                return Err(AriExternalMediaError::config(
                    "RTP SSRC changed during session",
                ));
            }
            self.buffer_packet(
                BufferedRtp {
                    payload_type: parsed.payload_type,
                    sequence: parsed.sequence,
                    timestamp: parsed.timestamp,
                    payload: parsed.payload.to_vec(),
                },
                arrival_ms,
            )?;
            if self.ssrc.is_none() && self.outbound_ssrc == parsed.ssrc {
                self.outbound_ssrc = next_outbound_ssrc_distinct_from(parsed.ssrc);
            }
            self.source.get_or_insert(source);
            self.ssrc.get_or_insert(parsed.ssrc);
        }
    }

    pub async fn send_payload(&mut self, payload: &[u8]) -> Result<(), AriExternalMediaError> {
        if payload.is_empty() || payload.len() % 2 != 0 {
            return Err(AriExternalMediaError::config(
                "RTP playback payload must contain complete PCM16 samples",
            ));
        }
        let destination = self.source.ok_or_else(|| {
            AriExternalMediaError::config(
                "RTP playback cannot start before an inbound peer is locked",
            )
        })?;
        self.transmit_sequence = self.transmit_sequence.wrapping_add(1);
        self.transmit_timestamp = self
            .transmit_timestamp
            .wrapping_add((payload.len() / 2) as u32);
        let mut packet = Vec::with_capacity(RTP_HEADER_BYTES + payload.len());
        packet.extend([0x80, self.expected_payload_type]);
        packet.extend(self.transmit_sequence.to_be_bytes());
        packet.extend(self.transmit_timestamp.to_be_bytes());
        packet.extend(self.outbound_ssrc.to_be_bytes());
        packet.extend(payload);
        self.socket
            .send_to(&packet, destination)
            .await
            .map_err(|error| AriExternalMediaError::operation("send RTP playback", error))?;
        Ok(())
    }

    pub fn transport_metrics(
        &mut self,
        ts_ms: u64,
    ) -> Result<ControlMessage, AriExternalMediaError> {
        self.control_seq += 1;
        let denominator = self.received + self.lost;
        let packet_loss = if denominator == 0 {
            0.0
        } else {
            self.lost as f64 / denominator as f64
        };
        let jitter_ms = self.jitter_timestamp_units * 1_000.0 / self.clock_rate_hz as f64;
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "transport.metrics",
            "session_id": self.session_id,
            "seq": self.control_seq,
            "ts_ms": ts_ms,
            "jitter_ms": jitter_ms,
            "rtt_ms": 0.0,
            "packet_loss": packet_loss
        }))
    }

    pub fn metrics_snapshot(&self) -> (f64, f64) {
        let denominator = self.received + self.lost;
        let packet_loss = if denominator == 0 {
            0.0
        } else {
            self.lost as f64 / denominator as f64
        };
        let jitter_ms = self.jitter_timestamp_units * 1_000.0 / self.clock_rate_hz as f64;
        (jitter_ms, packet_loss)
    }

    fn buffer_packet(
        &mut self,
        packet: BufferedRtp,
        arrival_ms: u64,
    ) -> Result<(), AriExternalMediaError> {
        if let Some(expected) = self.next_sequence {
            let delta = packet.sequence.wrapping_sub(expected);
            if delta >= 0x8000 {
                return Err(AriExternalMediaError::config(
                    "RTP sequence is duplicate or out of order",
                ));
            }
            if delta as usize > RTP_REORDER_BUFFER_PACKETS {
                return Err(AriExternalMediaError::config(
                    "RTP sequence is outside the reorder window",
                ));
            }
        } else {
            self.next_sequence = Some(packet.sequence);
        }
        if self.reorder_buffer.contains_key(&packet.sequence) {
            return Err(AriExternalMediaError::config(
                "RTP sequence is duplicate or out of order",
            ));
        }
        self.received += 1;
        let arrival_units = arrival_ms as i64 * i64::from(self.clock_rate_hz) / 1_000;
        let transit = arrival_units - i64::from(packet.timestamp);
        if let Some(previous) = self.last_transit {
            let variation = (transit - previous).unsigned_abs() as f64;
            self.jitter_timestamp_units += (variation - self.jitter_timestamp_units) / 16.0;
        }
        self.last_transit = Some(transit);
        self.reorder_buffer.insert(packet.sequence, packet);
        Ok(())
    }

    fn emit_packet(&mut self, packet: BufferedRtp) -> RtpMediaFrame {
        self.next_sequence = Some(packet.sequence.wrapping_add(1));
        self.last_emitted_timestamp = Some(packet.timestamp);
        self.last_payload_len = packet.payload.len();
        RtpMediaFrame {
            session_id: self.session_id.clone(),
            payload_type: packet.payload_type,
            sequence: packet.sequence,
            timestamp: packet.timestamp,
            payload: packet.payload,
        }
    }

    fn emit_silence(&mut self) -> Result<RtpMediaFrame, AriExternalMediaError> {
        let sequence = self.next_sequence.ok_or_else(|| {
            AriExternalMediaError::config("RTP loss concealment requires an initial packet")
        })?;
        let timestamp = self
            .last_emitted_timestamp
            .ok_or_else(|| {
                AriExternalMediaError::config("RTP loss concealment requires an emitted packet")
            })?
            .wrapping_add((self.last_payload_len / 2) as u32);
        self.next_sequence = Some(sequence.wrapping_add(1));
        self.last_emitted_timestamp = Some(timestamp);
        self.lost += 1;
        Ok(RtpMediaFrame {
            session_id: self.session_id.clone(),
            payload_type: self.expected_payload_type,
            sequence,
            timestamp,
            payload: vec![0; self.last_payload_len],
        })
    }
}

fn next_outbound_ssrc() -> u32 {
    NEXT_OUTBOUND_RTP_SSRC.fetch_add(1, Ordering::Relaxed)
}

fn next_outbound_ssrc_distinct_from(inbound_ssrc: u32) -> u32 {
    loop {
        let outbound_ssrc = next_outbound_ssrc();
        if outbound_ssrc != inbound_ssrc {
            return outbound_ssrc;
        }
    }
}

struct ParsedRtp<'a> {
    payload_type: u8,
    sequence: u16,
    timestamp: u32,
    ssrc: u32,
    payload: &'a [u8],
}

fn parse_rtp(
    packet: &[u8],
    expected_payload_type: u8,
) -> Result<ParsedRtp<'_>, AriExternalMediaError> {
    if packet.len() < RTP_HEADER_BYTES {
        return Err(AriExternalMediaError::config("RTP packet is truncated"));
    }
    if packet[0] >> 6 != RTP_VERSION {
        return Err(AriExternalMediaError::config("RTP version must be 2"));
    }
    let has_padding = packet[0] & 0x20 != 0;
    let has_extension = packet[0] & 0x10 != 0;
    let csrc_count = usize::from(packet[0] & 0x0f);
    let mut payload_start = RTP_HEADER_BYTES + csrc_count * 4;
    if payload_start > packet.len() {
        return Err(AriExternalMediaError::config("RTP CSRC list is truncated"));
    }
    if has_extension {
        if payload_start + 4 > packet.len() {
            return Err(AriExternalMediaError::config("RTP extension is truncated"));
        }
        let words = usize::from(u16::from_be_bytes([
            packet[payload_start + 2],
            packet[payload_start + 3],
        ]));
        payload_start += 4 + words * 4;
        if payload_start > packet.len() {
            return Err(AriExternalMediaError::config("RTP extension is truncated"));
        }
    }
    let payload_type = packet[1] & 0x7f;
    if payload_type != expected_payload_type {
        return Err(AriExternalMediaError::config(format!(
            "unexpected RTP payload type {payload_type}"
        )));
    }
    let padding = if has_padding {
        usize::from(*packet.last().unwrap_or(&0))
    } else {
        0
    };
    if padding > packet.len().saturating_sub(payload_start) {
        return Err(AriExternalMediaError::config("RTP padding is invalid"));
    }
    let payload_end = packet.len() - padding;
    if payload_start == payload_end {
        return Err(AriExternalMediaError::config("RTP payload is empty"));
    }
    Ok(ParsedRtp {
        payload_type,
        sequence: u16::from_be_bytes([packet[2], packet[3]]),
        timestamp: u32::from_be_bytes([packet[4], packet[5], packet[6], packet[7]]),
        ssrc: u32::from_be_bytes([packet[8], packet[9], packet[10], packet[11]]),
        payload: &packet[payload_start..payload_end],
    })
}

pub struct AriEventMapper {
    session_id: String,
    seq: u64,
    ended: bool,
}

impl AriEventMapper {
    pub fn new(session_id: String) -> Self {
        Self {
            session_id,
            seq: 0,
            ended: false,
        }
    }

    pub fn handle(
        &mut self,
        event: Value,
        ts_ms: u64,
    ) -> Result<Option<ControlMessage>, AriExternalMediaError> {
        if self.ended {
            return Err(AriExternalMediaError::config(
                "ARI event received after channel destruction",
            ));
        }
        let object = event
            .as_object()
            .ok_or_else(|| AriExternalMediaError::config("ARI event must be an object"))?;
        let event_type = string_field(object, "type")?;
        let channel_id = object
            .get("channel")
            .and_then(Value::as_object)
            .and_then(|channel| channel.get("id"))
            .and_then(Value::as_str)
            .ok_or_else(|| AriExternalMediaError::config("ARI event channel id is required"))?;
        if channel_id != self.session_id {
            return Ok(None);
        }
        match event_type {
            "ChannelDtmfReceived" => self.dtmf(object, ts_ms).map(Some),
            "ChannelDestroyed" | "StasisEnd" => self.ended(ts_ms).map(Some),
            _ => Ok(None),
        }
    }

    fn dtmf(
        &mut self,
        object: &Map<String, Value>,
        ts_ms: u64,
    ) -> Result<ControlMessage, AriExternalMediaError> {
        let digit = string_field(object, "digit")?;
        let bytes = digit.as_bytes();
        if bytes.len() != 1 || !VALID_DTMF.contains(&bytes[0]) {
            return Err(AriExternalMediaError::config(
                "ARI DTMF digit must be one valid digit",
            ));
        }
        let digit = char::from(bytes[0]).to_ascii_uppercase().to_string();
        self.seq += 1;
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "dtmf",
            "session_id": self.session_id,
            "seq": self.seq,
            "ts_ms": ts_ms,
            "digit": digit
        }))
    }

    fn ended(&mut self, ts_ms: u64) -> Result<ControlMessage, AriExternalMediaError> {
        self.seq += 1;
        let message = control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.ended",
            "session_id": self.session_id,
            "seq": self.seq,
            "ts_ms": ts_ms,
            "reason": ARI_DESTROYED_REASON
        }))?;
        self.ended = true;
        Ok(message)
    }
}

fn validate_identity(field: &str, value: &str) -> Result<(), AriExternalMediaError> {
    if value.trim().is_empty()
        || value.chars().any(char::is_control)
        || !value
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "-_.:".contains(character))
    {
        return Err(AriExternalMediaError::config(format!(
            "{field} contains invalid characters"
        )));
    }
    Ok(())
}

fn normalize_external_host(value: &str) -> Result<String, AriExternalMediaError> {
    if value.trim() != value
        || value.is_empty()
        || value.chars().any(char::is_control)
        || value.chars().any(char::is_whitespace)
    {
        return Err(AriExternalMediaError::config(
            "ARI external RTP host must be a valid host:port",
        ));
    }
    let url = Url::parse(&format!("udp://{value}"))
        .map_err(|_| AriExternalMediaError::config("ARI external RTP host is invalid"))?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
        || url.port().is_none()
        || url.port() == Some(0)
    {
        return Err(AriExternalMediaError::config(
            "ARI external RTP host must be a valid host:port",
        ));
    }
    let port = url.port().expect("port checked above");
    let host = url
        .host()
        .ok_or_else(|| AriExternalMediaError::config("ARI external RTP host is invalid"))?;
    Ok(match host {
        Host::Domain(domain) => format!("{domain}:{port}"),
        Host::Ipv4(address) => format!("{address}:{port}"),
        Host::Ipv6(address) => format!("[{address}]:{port}"),
    })
}

fn validate_secret(value: &str) -> Result<(), AriExternalMediaError> {
    if value.is_empty() || value.chars().any(char::is_control) {
        return Err(AriExternalMediaError::config("ARI password is invalid"));
    }
    Ok(())
}

fn validate_endpoint(value: &str) -> Result<(), AriExternalMediaError> {
    if value.trim().is_empty()
        || value.len() > 256
        || value.chars().any(char::is_control)
        || value.chars().any(char::is_whitespace)
    {
        return Err(AriExternalMediaError::config(
            "ARI transfer endpoint is invalid",
        ));
    }
    Ok(())
}

fn string_field<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a str, AriExternalMediaError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| AriExternalMediaError::config(format!("ARI field {field} is required")))
}

fn control(value: Value) -> Result<ControlMessage, AriExternalMediaError> {
    serde_json::from_value(value)
        .map_err(|error| AriExternalMediaError::operation("control-schema conversion", error))
}
