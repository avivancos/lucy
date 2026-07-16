use super::audiosocket::{
    encode_dtmf_frames, encode_hangup_frame, encode_slin8_frame, AdapterOutput, AudioSocketAdapter,
};
use super::directives::{
    parse_asterisk_directive, validate_asterisk_directive_envelope, AsteriskDirective,
};
use super::media_plane::FixtureMediaPlaneConfig;
use super::media_session::{MediaSession, PlaybackTick};
use super::metrics::TransportMetricsSampler;
use super::provider_media::{
    DeepgramConfig, ElevenLabsConfig, ProviderMediaConfig, PRODUCTION_MEDIA_BACKEND,
};
use crate::control::schema::canonical_json;
use crate::control::schema::ControlMessage;
use futures_util::{Sink, SinkExt, StreamExt};
use ipnet::IpNet;
use serde::Serialize;
use std::env;
use std::fmt;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::AsyncWriteExt;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Semaphore;
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, http::HeaderValue, Message},
};
use url::Url;

const AUDIO_SOCKET_BIND_ENV: &str = "LUCY_GATEWAY_AUDIO_SOCKET_BIND";
const AUDIO_SOCKET_ALLOWED_CIDRS_ENV: &str = "LUCY_GATEWAY_AUDIO_SOCKET_ALLOWED_CIDRS";
const AUDIO_SOCKET_HANDSHAKE_TIMEOUT_MS_ENV: &str =
    "LUCY_GATEWAY_AUDIO_SOCKET_HANDSHAKE_TIMEOUT_MS";
const AUDIO_SOCKET_IDLE_TIMEOUT_MS_ENV: &str = "LUCY_GATEWAY_AUDIO_SOCKET_IDLE_TIMEOUT_MS";
const AUDIO_SOCKET_MAX_SESSIONS_ENV: &str = "LUCY_GATEWAY_AUDIO_SOCKET_MAX_SESSIONS";
const CONTROL_CONNECT_TIMEOUT_MS_ENV: &str = "LUCY_GATEWAY_CONTROL_CONNECT_TIMEOUT_MS";
const CONTROL_TOKEN_ENV: &str = "LUCY_GATEWAY_CONTROL_TOKEN";
const ALLOW_INSECURE_CONTROL_WS_ENV: &str = "LUCY_GATEWAY_ALLOW_INSECURE_CONTROL_WS";
const CONTROL_WS_URL_ENV: &str = "LUCY_SESSION_WS_URL";
pub(crate) const MEDIA_BACKEND_ENV: &str = "LUCY_GATEWAY_MEDIA_BACKEND";
const MEDIA_FIXTURE_WAV_ENV: &str = "LUCY_GATEWAY_MEDIA_FIXTURE_WAV";
const MEDIA_FIXTURE_TIMELINE_ENV: &str = "LUCY_GATEWAY_MEDIA_FIXTURE_TIMELINE";
const MEDIA_TRANSCRIPT_TRIGGER_BYTES_ENV: &str = "LUCY_GATEWAY_MEDIA_TRANSCRIPT_TRIGGER_BYTES";
const MEDIA_PLAYBACK_CHUNK_BYTES_ENV: &str = "LUCY_GATEWAY_MEDIA_PLAYBACK_CHUNK_BYTES";
const MEDIA_PLAYBACK_PACING_MS_ENV: &str = "LUCY_GATEWAY_MEDIA_PLAYBACK_PACING_MS";
const DEFAULT_AUDIO_SOCKET_BIND: &str = "0.0.0.0:9092";
const DEFAULT_ALLOWED_CIDRS: &str = "127.0.0.0/8,::1/128";
const DEFAULT_HANDSHAKE_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_IDLE_TIMEOUT_MS: u64 = 30_000;
const DEFAULT_CONTROL_CONNECT_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_MAX_SESSIONS: usize = 1_024;
const FIXTURE_MEDIA_BACKEND: &str = "fixture";
const DEEPGRAM_URL_ENV: &str = "LUCY_GATEWAY_DEEPGRAM_URL";
const DEEPGRAM_MODEL_ENV: &str = "LUCY_GATEWAY_DEEPGRAM_MODEL";
const DEEPGRAM_API_KEY_ENV: &str = "LUCY_GATEWAY_DEEPGRAM_API_KEY";
const DEEPGRAM_SAMPLE_RATE_ENV: &str = "LUCY_GATEWAY_DEEPGRAM_SAMPLE_RATE_HZ";
const DEEPGRAM_FRAME_BYTES_ENV: &str = "LUCY_GATEWAY_DEEPGRAM_FRAME_BYTES";
const DEEPGRAM_ENDPOINTING_ENV: &str = "LUCY_GATEWAY_DEEPGRAM_ENDPOINTING_MS";
const ELEVENLABS_URL_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_URL";
const ELEVENLABS_MODEL_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_MODEL";
const ELEVENLABS_VOICE_ID_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_VOICE_ID";
const ELEVENLABS_API_KEY_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_API_KEY";
const ELEVENLABS_OUTPUT_FORMAT_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_OUTPUT_FORMAT";
const ELEVENLABS_SAMPLE_RATE_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_SAMPLE_RATE_HZ";
const ELEVENLABS_PLAYBACK_FRAME_MS_ENV: &str = "LUCY_GATEWAY_ELEVENLABS_PLAYBACK_FRAME_MS";
const PROVIDER_CONNECT_TIMEOUT_ENV: &str = "LUCY_GATEWAY_PROVIDER_CONNECT_TIMEOUT_MS";
const PROVIDER_IDLE_TIMEOUT_ENV: &str = "LUCY_GATEWAY_PROVIDER_IDLE_TIMEOUT_MS";
const PROVIDER_LOCAL_PROTOCOL_TEST_ENV: &str = "LUCY_GATEWAY_PROVIDER_LOCAL_PROTOCOL_TEST";
const DEFAULT_TRANSCRIPT_TRIGGER_BYTES: usize = 3_200;
const DEFAULT_PLAYBACK_CHUNK_BYTES: usize = 320;
const DEFAULT_PLAYBACK_PACING_MS: u64 = 20;
const DEFAULT_PROVIDER_SAMPLE_RATE_HZ: u64 = 16_000;
const DEFAULT_DEEPGRAM_FRAME_BYTES: usize = 640;
const DEFAULT_DEEPGRAM_ENDPOINTING_MS: u64 = 300;

#[derive(Clone)]
pub(crate) struct ControlWebSocketPolicy {
    url: Url,
    token: String,
}

impl ControlWebSocketPolicy {
    pub(crate) fn new(
        value: &str,
        allow_insecure_ws: bool,
        token: Option<String>,
    ) -> Result<Self, GatewayServerError> {
        let url = Url::parse(value)
            .map_err(|_| GatewayServerError::config("control WebSocket URL is invalid"))?;
        if !matches!(url.scheme(), "ws" | "wss") {
            return Err(GatewayServerError::config(
                "control WebSocket URL must use ws or wss",
            ));
        }
        if !url.username().is_empty() || url.password().is_some() || url.fragment().is_some() {
            return Err(GatewayServerError::config(
                "control WebSocket URL cannot contain credentials or a fragment",
            ));
        }
        let loopback = url.host_str().is_some_and(|host| {
            host.eq_ignore_ascii_case("localhost")
                || host
                    .parse::<IpAddr>()
                    .is_ok_and(|address| address.is_loopback())
        });
        if url.scheme() == "ws" && !loopback && !allow_insecure_ws {
            return Err(GatewayServerError::config(
                "non-loopback control WebSocket must use wss",
            ));
        }
        let token = token.ok_or_else(|| {
            GatewayServerError::config(format!("{CONTROL_TOKEN_ENV} is required"))
        })?;
        if token.trim().is_empty()
            || token
                .chars()
                .any(|character| character.is_whitespace() || character.is_control())
        {
            return Err(GatewayServerError::config("control token is invalid"));
        }
        Ok(Self { url, token })
    }

    pub(crate) fn from_env(value: &str) -> Result<Self, GatewayServerError> {
        Self::new(
            value,
            env_bool(ALLOW_INSECURE_CONTROL_WS_ENV, false)?,
            env::var(CONTROL_TOKEN_ENV).ok(),
        )
    }

    pub(crate) fn request(
        &self,
    ) -> Result<tokio_tungstenite::tungstenite::http::Request<()>, GatewayServerError> {
        let mut request = self
            .url
            .as_str()
            .into_client_request()
            .map_err(|_| GatewayServerError::config("control WebSocket request is invalid"))?;
        let value = HeaderValue::from_str(&format!("Bearer {}", self.token))
            .map_err(|_| GatewayServerError::config("control token is invalid"))?;
        request.headers_mut().insert("authorization", value);
        Ok(request)
    }

    fn redacted_url(&self) -> String {
        let mut url = self.url.clone();
        if url.query().is_some() {
            url.set_query(Some("[redacted]"));
        }
        url.to_string()
    }
}

#[derive(Clone)]
pub struct AudioSocketServerConfig {
    pub bind_address: SocketAddr,
    pub control_ws_url: String,
    pub allowed_peer_cidrs: Arc<[IpNet]>,
    pub handshake_timeout: Duration,
    pub idle_timeout: Duration,
    pub control_connect_timeout: Duration,
    pub max_sessions: usize,
    pub media_plane: Option<FixtureMediaPlaneConfig>,
    pub provider_media: Option<ProviderMediaConfig>,
    control_policy: ControlWebSocketPolicy,
}

impl AudioSocketServerConfig {
    pub fn new(
        bind_address: &str,
        control_ws_url: &str,
        control_token: &str,
    ) -> Result<Self, GatewayServerError> {
        Self::from_values(
            bind_address,
            control_ws_url,
            DEFAULT_ALLOWED_CIDRS,
            false,
            Some(control_token.to_string()),
            DEFAULT_HANDSHAKE_TIMEOUT_MS,
            DEFAULT_IDLE_TIMEOUT_MS,
            DEFAULT_CONTROL_CONNECT_TIMEOUT_MS,
            DEFAULT_MAX_SESSIONS,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn from_values(
        bind_address: &str,
        control_ws_url: &str,
        allowed_cidrs: &str,
        allow_insecure_control_ws: bool,
        control_token: Option<String>,
        handshake_timeout_ms: u64,
        idle_timeout_ms: u64,
        control_connect_timeout_ms: u64,
        max_sessions: usize,
    ) -> Result<Self, GatewayServerError> {
        let bind_address = bind_address
            .parse()
            .map_err(|_| GatewayServerError::config("invalid AudioSocket bind address"))?;
        let control_policy =
            ControlWebSocketPolicy::new(control_ws_url, allow_insecure_control_ws, control_token)?;
        let allowed_peer_cidrs = parse_cidrs(allowed_cidrs)?;
        for (name, value) in [
            (AUDIO_SOCKET_HANDSHAKE_TIMEOUT_MS_ENV, handshake_timeout_ms),
            (AUDIO_SOCKET_IDLE_TIMEOUT_MS_ENV, idle_timeout_ms),
            (CONTROL_CONNECT_TIMEOUT_MS_ENV, control_connect_timeout_ms),
        ] {
            if value == 0 {
                return Err(GatewayServerError::config(format!(
                    "{name} must be positive",
                )));
            }
        }
        if max_sessions == 0 {
            return Err(GatewayServerError::config(format!(
                "{AUDIO_SOCKET_MAX_SESSIONS_ENV} must be positive",
            )));
        }
        Ok(Self {
            bind_address,
            control_ws_url: control_ws_url.to_string(),
            allowed_peer_cidrs: allowed_peer_cidrs.into(),
            handshake_timeout: Duration::from_millis(handshake_timeout_ms),
            idle_timeout: Duration::from_millis(idle_timeout_ms),
            control_connect_timeout: Duration::from_millis(control_connect_timeout_ms),
            max_sessions,
            media_plane: None,
            provider_media: None,
            control_policy,
        })
    }

    pub fn from_env() -> Result<Self, GatewayServerError> {
        let bind_address = env::var(AUDIO_SOCKET_BIND_ENV)
            .unwrap_or_else(|_| DEFAULT_AUDIO_SOCKET_BIND.to_string());
        let control_ws_url = env::var(CONTROL_WS_URL_ENV).map_err(|_| {
            GatewayServerError::config(format!("{CONTROL_WS_URL_ENV} is required in serve mode"))
        })?;
        let mut config = Self::from_values(
            &bind_address,
            &control_ws_url,
            &env::var(AUDIO_SOCKET_ALLOWED_CIDRS_ENV)
                .unwrap_or_else(|_| DEFAULT_ALLOWED_CIDRS.to_string()),
            env_bool(ALLOW_INSECURE_CONTROL_WS_ENV, false)?,
            env::var(CONTROL_TOKEN_ENV).ok(),
            env_u64(
                AUDIO_SOCKET_HANDSHAKE_TIMEOUT_MS_ENV,
                DEFAULT_HANDSHAKE_TIMEOUT_MS,
            )?,
            env_u64(AUDIO_SOCKET_IDLE_TIMEOUT_MS_ENV, DEFAULT_IDLE_TIMEOUT_MS)?,
            env_u64(
                CONTROL_CONNECT_TIMEOUT_MS_ENV,
                DEFAULT_CONTROL_CONNECT_TIMEOUT_MS,
            )?,
            env_usize(AUDIO_SOCKET_MAX_SESSIONS_ENV, DEFAULT_MAX_SESSIONS)?,
        )?;
        match gateway_media_backend_from_env()? {
            GatewayMediaBackend::Fixture(media_plane) => config.media_plane = Some(media_plane),
            GatewayMediaBackend::Provider(provider_media) => {
                config.provider_media = Some(*provider_media)
            }
        }
        Ok(config)
    }

    fn peer_allowed(&self, address: IpAddr) -> bool {
        self.allowed_peer_cidrs
            .iter()
            .any(|network| network.contains(&address))
    }

    fn control_request(
        &self,
    ) -> Result<tokio_tungstenite::tungstenite::http::Request<()>, GatewayServerError> {
        self.control_policy.request()
    }
}

fn provider_media_from_env() -> Result<ProviderMediaConfig, GatewayServerError> {
    let connect_timeout = Duration::from_millis(env_u64(
        PROVIDER_CONNECT_TIMEOUT_ENV,
        DEFAULT_CONTROL_CONNECT_TIMEOUT_MS,
    )?);
    let idle_timeout =
        Duration::from_millis(env_u64(PROVIDER_IDLE_TIMEOUT_ENV, DEFAULT_IDLE_TIMEOUT_MS)?);
    let deepgram = DeepgramConfig::new(
        required_env(DEEPGRAM_URL_ENV)?,
        required_env(DEEPGRAM_MODEL_ENV)?,
        required_env(DEEPGRAM_API_KEY_ENV)?,
        env_u64(DEEPGRAM_SAMPLE_RATE_ENV, DEFAULT_PROVIDER_SAMPLE_RATE_HZ)? as u32,
        env_usize(DEEPGRAM_FRAME_BYTES_ENV, DEFAULT_DEEPGRAM_FRAME_BYTES)?,
        env_u64(DEEPGRAM_ENDPOINTING_ENV, DEFAULT_DEEPGRAM_ENDPOINTING_MS)?,
        connect_timeout,
        idle_timeout,
    );
    let elevenlabs = ElevenLabsConfig::new(
        required_env(ELEVENLABS_URL_ENV)?,
        required_env(ELEVENLABS_MODEL_ENV)?,
        required_env(ELEVENLABS_VOICE_ID_ENV)?,
        required_env(ELEVENLABS_API_KEY_ENV)?,
        required_env(ELEVENLABS_OUTPUT_FORMAT_ENV)?,
        env_u64(ELEVENLABS_SAMPLE_RATE_ENV, DEFAULT_PROVIDER_SAMPLE_RATE_HZ)? as u32,
        connect_timeout,
        idle_timeout,
    )
    .with_playback_frame_ms(env_u64(
        ELEVENLABS_PLAYBACK_FRAME_MS_ENV,
        super::provider_media::DEFAULT_PROVIDER_PLAYBACK_FRAME_MS,
    )?);
    let local = env_bool(PROVIDER_LOCAL_PROTOCOL_TEST_ENV, false)?;
    if local {
        ProviderMediaConfig::local_protocol_test(deepgram, elevenlabs)
    } else {
        ProviderMediaConfig::new(deepgram, elevenlabs)
    }
    .map_err(|error| GatewayServerError::operation("validate provider media config", error))
}

pub(crate) enum GatewayMediaBackend {
    Fixture(FixtureMediaPlaneConfig),
    Provider(Box<ProviderMediaConfig>),
}

pub(crate) fn gateway_media_backend_from_env() -> Result<GatewayMediaBackend, GatewayServerError> {
    let backend = env::var(MEDIA_BACKEND_ENV).map_err(|_| {
        GatewayServerError::config(format!(
            "{MEDIA_BACKEND_ENV} is required and must name a Rust media backend",
        ))
    })?;
    if backend == PRODUCTION_MEDIA_BACKEND {
        return provider_media_from_env()
            .map(Box::new)
            .map(GatewayMediaBackend::Provider);
    }
    if backend != FIXTURE_MEDIA_BACKEND {
        return Err(GatewayServerError::config(format!(
            "unsupported {MEDIA_BACKEND_ENV}: {backend}",
        )));
    }
    let media_plane = FixtureMediaPlaneConfig {
        playback_wav: required_path(MEDIA_FIXTURE_WAV_ENV)?,
        transcript_timeline: required_path(MEDIA_FIXTURE_TIMELINE_ENV)?,
        transcript_trigger_bytes: env_usize(
            MEDIA_TRANSCRIPT_TRIGGER_BYTES_ENV,
            DEFAULT_TRANSCRIPT_TRIGGER_BYTES,
        )?,
        playback_chunk_bytes: env_usize(
            MEDIA_PLAYBACK_CHUNK_BYTES_ENV,
            DEFAULT_PLAYBACK_CHUNK_BYTES,
        )?,
        playback_pacing_ms: env_u64(MEDIA_PLAYBACK_PACING_MS_ENV, DEFAULT_PLAYBACK_PACING_MS)?,
    };
    media_plane
        .open()
        .map_err(|error| GatewayServerError::operation("open media-plane fixture", error))?;
    Ok(GatewayMediaBackend::Fixture(media_plane))
}

fn required_env(name: &str) -> Result<String, GatewayServerError> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| GatewayServerError::config(format!("{name} is required")))
}

impl fmt::Debug for AudioSocketServerConfig {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AudioSocketServerConfig")
            .field("bind_address", &self.bind_address)
            .field("control_ws_url", &self.control_policy.redacted_url())
            .field("allowed_peer_cidrs", &self.allowed_peer_cidrs)
            .field("handshake_timeout", &self.handshake_timeout)
            .field("idle_timeout", &self.idle_timeout)
            .field("control_connect_timeout", &self.control_connect_timeout)
            .field("max_sessions", &self.max_sessions)
            .field("media_plane", &self.media_plane)
            .field("control_token", &"[redacted]")
            .finish()
    }
}

fn required_path(name: &str) -> Result<PathBuf, GatewayServerError> {
    env::var_os(name)
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| GatewayServerError::config(format!("{name} is required")))
}

fn parse_cidrs(value: &str) -> Result<Vec<IpNet>, GatewayServerError> {
    let cidrs = value
        .split(',')
        .map(str::trim)
        .map(|cidr| {
            cidr.parse::<IpNet>().map_err(|_| {
                GatewayServerError::config(format!(
                    "{AUDIO_SOCKET_ALLOWED_CIDRS_ENV} contains an invalid CIDR",
                ))
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    if cidrs.is_empty() {
        return Err(GatewayServerError::config(format!(
            "{AUDIO_SOCKET_ALLOWED_CIDRS_ENV} cannot be empty",
        )));
    }
    Ok(cidrs)
}

fn env_u64(name: &str, default: u64) -> Result<u64, GatewayServerError> {
    env::var(name).map_or_else(
        |_| Ok(default),
        |value| {
            value
                .parse()
                .map_err(|_| GatewayServerError::config(format!("{name} must be an integer")))
        },
    )
}

fn env_usize(name: &str, default: usize) -> Result<usize, GatewayServerError> {
    env::var(name).map_or_else(
        |_| Ok(default),
        |value| {
            value
                .parse()
                .map_err(|_| GatewayServerError::config(format!("{name} must be an integer")))
        },
    )
}

fn env_bool(name: &str, default: bool) -> Result<bool, GatewayServerError> {
    match env::var(name).as_deref() {
        Ok("true") => Ok(true),
        Ok("false") => Ok(false),
        Ok(_) => Err(GatewayServerError::config(format!(
            "{name} must be true or false",
        ))),
        Err(_) => Ok(default),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GatewayServerError(String);

impl GatewayServerError {
    fn config(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    fn operation(operation: &str, detail: impl fmt::Display) -> Self {
        Self(format!("{operation} failed: {detail}"))
    }
}

impl fmt::Display for GatewayServerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for GatewayServerError {}

#[derive(Default)]
struct GatewayMetricCounters {
    asterisk_sessions_started: AtomicU64,
    asterisk_sessions_completed: AtomicU64,
    asterisk_sessions_failed: AtomicU64,
    asterisk_audio_bytes_received: AtomicU64,
    asterisk_audio_bytes_sent: AtomicU64,
    control_messages_forwarded: AtomicU64,
}

#[derive(Clone, Default)]
pub struct GatewayMetrics(Arc<GatewayMetricCounters>);

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct GatewayMetricsSnapshot {
    pub asterisk_sessions_started: u64,
    pub asterisk_sessions_completed: u64,
    pub asterisk_sessions_failed: u64,
    pub asterisk_audio_bytes_received: u64,
    pub asterisk_audio_bytes_sent: u64,
    pub control_messages_forwarded: u64,
}

impl GatewayMetrics {
    pub fn snapshot(&self) -> GatewayMetricsSnapshot {
        GatewayMetricsSnapshot {
            asterisk_sessions_started: self.0.asterisk_sessions_started.load(Ordering::Relaxed),
            asterisk_sessions_completed: self.0.asterisk_sessions_completed.load(Ordering::Relaxed),
            asterisk_sessions_failed: self.0.asterisk_sessions_failed.load(Ordering::Relaxed),
            asterisk_audio_bytes_received: self
                .0
                .asterisk_audio_bytes_received
                .load(Ordering::Relaxed),
            asterisk_audio_bytes_sent: self.0.asterisk_audio_bytes_sent.load(Ordering::Relaxed),
            control_messages_forwarded: self.0.control_messages_forwarded.load(Ordering::Relaxed),
        }
    }
}

pub async fn serve_audio_socket(
    listener: TcpListener,
    config: AudioSocketServerConfig,
    metrics: GatewayMetrics,
) -> Result<(), GatewayServerError> {
    let admission = Arc::new(Semaphore::new(config.max_sessions));
    loop {
        let (stream, peer) = listener
            .accept()
            .await
            .map_err(|error| GatewayServerError::operation("accept AudioSocket", error))?;
        if !config.peer_allowed(peer.ip()) {
            metrics
                .0
                .asterisk_sessions_failed
                .fetch_add(1, Ordering::Relaxed);
            continue;
        }
        let Ok(permit) = admission.clone().try_acquire_owned() else {
            metrics
                .0
                .asterisk_sessions_failed
                .fetch_add(1, Ordering::Relaxed);
            continue;
        };
        let connection_config = config.clone();
        let connection_metrics = metrics.clone();
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(error) = handle_audio_socket_connection_with_config(
                stream,
                connection_config,
                connection_metrics,
                system_time_ms,
            )
            .await
            {
                eprintln!("AudioSocket session failed: {error}");
            }
        });
    }
}

pub async fn handle_audio_socket_connection<F>(
    stream: TcpStream,
    control_ws_url: String,
    control_token: String,
    metrics: GatewayMetrics,
    now_ms: F,
) -> Result<(), GatewayServerError>
where
    F: FnMut() -> u64 + Send,
{
    let config = AudioSocketServerConfig::new("127.0.0.1:0", &control_ws_url, &control_token)?;
    handle_audio_socket_connection_with_config(stream, config, metrics, now_ms).await
}

pub async fn handle_audio_socket_connection_with_config<F>(
    stream: TcpStream,
    config: AudioSocketServerConfig,
    metrics: GatewayMetrics,
    now_ms: F,
) -> Result<(), GatewayServerError>
where
    F: FnMut() -> u64 + Send,
{
    let result = handle_connection_inner(stream, config, metrics.clone(), now_ms).await;
    if result.is_err() {
        metrics
            .0
            .asterisk_sessions_failed
            .fetch_add(1, Ordering::Relaxed);
    }
    result
}

async fn handle_connection_inner<F>(
    stream: TcpStream,
    config: AudioSocketServerConfig,
    metrics: GatewayMetrics,
    mut now_ms: F,
) -> Result<(), GatewayServerError>
where
    F: FnMut() -> u64 + Send,
{
    let peer = stream
        .peer_addr()
        .map_err(|error| GatewayServerError::operation("read AudioSocket peer", error))?;
    if !config.peer_allowed(peer.ip()) {
        return Err(GatewayServerError::config(
            "AudioSocket peer is not in the configured allowlist",
        ));
    }
    let (mut audio_reader, mut audio_writer) = stream.into_split();
    let mut adapter = AudioSocketAdapter::default();
    let first = tokio::time::timeout(
        config.handshake_timeout,
        adapter.read_next(&mut audio_reader, now_ms()),
    )
    .await
    .map_err(|_| GatewayServerError::config("AudioSocket UUID handshake timed out"))?
    .map_err(|error| GatewayServerError::operation("decode AudioSocket UUID", error))?
    .ok_or_else(|| GatewayServerError::config("AudioSocket closed before UUID handshake"))?;
    let AdapterOutput::Control(started) = first else {
        return Err(GatewayServerError::config(
            "AudioSocket first frame did not start a session",
        ));
    };
    if started.message_type() != "session.started" {
        return Err(GatewayServerError::config(
            "AudioSocket first frame did not start a session",
        ));
    }
    let request = config.control_request()?;
    let (socket, _) = tokio::time::timeout(config.control_connect_timeout, connect_async(request))
        .await
        .map_err(|_| GatewayServerError::config("control WebSocket connection timed out"))?
        .map_err(|_| GatewayServerError::config("control WebSocket connection failed"))?;
    let (mut control_sink, mut control_source) = socket.split();
    forward_control(&mut control_sink, &started, &metrics).await?;
    let session_id = adapter
        .active_session_id()
        .ok_or_else(|| GatewayServerError::config("AudioSocket session has no UUID"))?
        .to_string();
    let mut last_directive_seq = None;
    let mut media_session =
        MediaSession::new(config.media_plane.clone(), config.provider_media.clone())
            .map_err(|error| GatewayServerError::operation("open media-plane session", error))?;
    let mut transport_metrics =
        TransportMetricsSampler::new(super::metrics::DEFAULT_METRICS_EMIT_INTERVAL_MS);
    let mut playback_tick = tokio::time::interval(media_session.playback_pacing());
    playback_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let idle_timeout = tokio::time::sleep(config.idle_timeout);
    tokio::pin!(idle_timeout);
    loop {
        tokio::select! {
            _ = &mut idle_timeout => {
                return Err(GatewayServerError::config("AudioSocket session idle timeout elapsed"));
            }
            output = adapter.read_next(&mut audio_reader, now_ms()) => {
                let output = output
                    .map_err(|error| GatewayServerError::operation("decode AudioSocket", error))?;
                idle_timeout
                    .as_mut()
                    .reset(tokio::time::Instant::now() + config.idle_timeout);
                let output = match output {
                    Some(output) => output,
                    None => adapter
                        .stream_closed(now_ms())
                        .map_err(|error| GatewayServerError::operation("close AudioSocket", error))?,
                };
                match output {
                    AdapterOutput::Media(frame) => {
                        let arrived_at_ms = now_ms();
                        transport_metrics.observe_pcm(
                            arrived_at_ms,
                            pcm_duration_ms(
                                frame.pcm_s16le.len(),
                                super::audiosocket::SLIN8_SAMPLE_RATE_HZ,
                            ),
                        );
                        metrics
                            .0
                            .asterisk_audio_bytes_received
                            .fetch_add(frame.pcm_s16le.len() as u64, Ordering::Relaxed);
                        if media_session.has_backend() {
                            let events = media_session
                                .push_audio(
                                    &frame.pcm_s16le,
                                    super::audiosocket::SLIN8_SAMPLE_RATE_HZ,
                                    arrived_at_ms,
                                )
                                .map_err(|error| GatewayServerError::operation("send PCM to media provider", error))?;
                            for event in events {
                                let AdapterOutput::Control(message) = adapter
                                    .media_plane_event(event)
                                    .map_err(|error| GatewayServerError::operation("map media-plane event", error))?
                                else {
                                    unreachable!("media-plane events are control messages")
                                };
                                forward_control(&mut control_sink, &message, &metrics).await?;
                            }
                            if let Some(sample) = transport_metrics.sample_without_rtt(arrived_at_ms) {
                                if let Some(AdapterOutput::Control(message)) = adapter
                                    .transport_metrics(sample, arrived_at_ms)
                                    .map_err(|error| GatewayServerError::operation("emit transport metrics", error))?
                                {
                                    forward_control(&mut control_sink, &message, &metrics).await?;
                                }
                            }
                        }
                    }
                    AdapterOutput::Control(message) => {
                        if forward_control(&mut control_sink, &message, &metrics).await? {
                            break;
                        }
                    }
                }
            }
            incoming = control_source.next() => {
                let Some(incoming) = incoming else {
                    return Err(GatewayServerError::config("control WebSocket closed unexpectedly"));
                };
                let incoming = incoming
                    .map_err(|error| GatewayServerError::operation("read control directive", error))?;
                match incoming {
                    Message::Text(text) => {
                        let message: ControlMessage = serde_json::from_str(text.as_ref())
                            .map_err(|error| GatewayServerError::operation("decode control directive", error))?;
                        validate_asterisk_directive_envelope(
                            &message,
                            &session_id,
                            &mut last_directive_seq,
                        )
                        .map_err(|error| GatewayServerError::config(error.to_string()))?;
                        match parse_asterisk_directive(&message)
                            .map_err(|error| GatewayServerError::operation("map control directive", error))?
                        {
                            Some(AsteriskDirective::SendDtmf { digits }) => {
                                let frames = encode_dtmf_frames(&digits)
                                    .map_err(|error| GatewayServerError::operation("encode AudioSocket DTMF", error))?;
                                audio_writer.write_all(&frames).await
                                    .map_err(|error| GatewayServerError::operation("write AudioSocket DTMF", error))?;
                            }
                            Some(AsteriskDirective::Hangup { reason }) => {
                                audio_writer.write_all(&encode_hangup_frame()).await
                                    .map_err(|error| GatewayServerError::operation("write AudioSocket hangup", error))?;
                                let ended = adapter.end(now_ms(), &reason)
                                    .map_err(|error| GatewayServerError::operation("end AudioSocket session", error))?;
                                let AdapterOutput::Control(ended) = ended else {
                                    unreachable!("ending a session always creates control")
                                };
                                forward_control(&mut control_sink, &ended, &metrics).await?;
                                break;
                            }
                            Some(AsteriskDirective::CancelPlayback { utterance_id }) => {
                                let cancellation = media_session.cancel_playback(&utterance_id)
                                    .map_err(|error| GatewayServerError::operation("cancel queued playback", error))?;
                                for event in cancellation.events {
                                    let AdapterOutput::Control(message) = adapter.media_plane_event(event).map_err(|error| GatewayServerError::operation("map playback cancellation", error))? else { unreachable!("playback cancellation is control") };
                                    forward_control(&mut control_sink, &message, &metrics).await?;
                                }
                            }
                            Some(AsteriskDirective::Playback { utterance_id, text, flush }) => {
                                for event in media_session.submit_playback(utterance_id, text, flush)
                                    .map_err(|error| GatewayServerError::operation("queue media playback", error))? {
                                    let AdapterOutput::Control(message) = adapter.media_plane_event(event).map_err(|error| GatewayServerError::operation("map playback start", error))? else { unreachable!("playback start is control") };
                                    forward_control(&mut control_sink, &message, &metrics).await?;
                                }
                            }
                            Some(AsteriskDirective::Transfer { .. }) => {
                                return Err(GatewayServerError::config(
                                    "AudioSocket transfer requires the ARI control path",
                                ));
                            }
                            None => {}
                        }
                    }
                    Message::Ping(payload) => {
                        control_sink.send(Message::Pong(payload)).await
                            .map_err(|error| GatewayServerError::operation("reply to control ping", error))?;
                    }
                    Message::Close(_) => {
                        return Err(GatewayServerError::config("control WebSocket closed unexpectedly"));
                    }
                    Message::Pong(_) => {}
                    Message::Binary(_) | Message::Frame(_) => {}
                }
            }
            _ = playback_tick.tick() => {
                for event in media_session.drain_provider_events().map_err(|error| GatewayServerError::operation("read Deepgram event", error))? {
                    let AdapterOutput::Control(message) = adapter.media_plane_event(event).map_err(|error| GatewayServerError::operation("map Deepgram event", error))? else { unreachable!() };
                    forward_control(&mut control_sink, &message, &metrics).await?;
                }
                match media_session.next_playback(super::audiosocket::SLIN8_SAMPLE_RATE_HZ)
                    .map_err(|error| GatewayServerError::operation("read media playback", error))?
                {
                    PlaybackTick::Idle => {}
                    PlaybackTick::Audio(pcm) => {
                        let frame = encode_slin8_frame(&pcm)
                            .map_err(|error| GatewayServerError::operation("encode AudioSocket playback", error))?;
                        audio_writer.write_all(&frame).await
                            .map_err(|error| GatewayServerError::operation("write AudioSocket playback", error))?;
                        metrics
                            .0
                            .asterisk_audio_bytes_sent
                            .fetch_add(pcm.len() as u64, Ordering::Relaxed);
                    }
                    PlaybackTick::Finished { event, next_started } => {
                        let AdapterOutput::Control(message) = adapter
                            .media_plane_event(event)
                            .map_err(|error| GatewayServerError::operation("map playback completion", error))?
                        else {
                            unreachable!("playback completion is control")
                        };
                        forward_control(&mut control_sink, &message, &metrics).await?;
                        if let Some(event) = next_started {
                            let AdapterOutput::Control(message) = adapter.media_plane_event(event).map_err(|error| GatewayServerError::operation("map next playback start", error))? else { unreachable!("playback start is control") };
                            forward_control(&mut control_sink, &message, &metrics).await?;
                        }
                    }
                }
            }
        }
    }
    let _ = control_sink.close().await;
    Ok(())
}

async fn forward_control<S, E>(
    sink: &mut S,
    message: &ControlMessage,
    metrics: &GatewayMetrics,
) -> Result<bool, GatewayServerError>
where
    S: Sink<Message, Error = E> + Unpin,
    E: fmt::Display,
{
    let message_type = message.message_type();
    sink.send(Message::Text(
        canonical_json(message)
            .map_err(|error| GatewayServerError::operation("serialize control message", error))?
            .into(),
    ))
    .await
    .map_err(|error| GatewayServerError::operation("forward control message", error))?;
    metrics
        .0
        .control_messages_forwarded
        .fetch_add(1, Ordering::Relaxed);
    if message_type == "session.started" {
        metrics
            .0
            .asterisk_sessions_started
            .fetch_add(1, Ordering::Relaxed);
    } else if message_type == "session.ended" {
        metrics
            .0
            .asterisk_sessions_completed
            .fetch_add(1, Ordering::Relaxed);
        return Ok(true);
    }
    Ok(false)
}

fn pcm_duration_ms(bytes: usize, sample_rate_hz: u32) -> u64 {
    let bytes_per_sample = std::mem::size_of::<i16>() as u64;
    let duration = (bytes as u64).saturating_mul(1_000)
        / bytes_per_sample.saturating_mul(sample_rate_hz as u64);
    duration.max(1)
}

fn system_time_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
