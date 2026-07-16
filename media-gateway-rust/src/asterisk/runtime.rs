use super::{
    ari_external_media::{
        AriExternalMediaClient, AriExternalMediaConfig, AriMediaFormat, RtpReceiver,
    },
    directives::{
        parse_asterisk_directive, validate_asterisk_directive_envelope, AsteriskDirective,
    },
    media_plane::{FixtureMediaPlaneConfig, MediaPlaneEvent},
    media_session::{MediaSession, PlaybackTick},
    media_websocket::{
        MediaWebSocketAdapter, MediaWebSocketOutput, MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES,
    },
    metrics::TransportMetricsSampler,
    provider_media::ProviderMediaConfig,
    server::{
        gateway_media_backend_from_env, serve_audio_socket, AudioSocketServerConfig,
        ControlWebSocketPolicy, GatewayMediaBackend, GatewayMetrics,
    },
    CONTROL_SCHEMA_VERSION, VALID_DTMF,
};
use crate::control::schema::canonical_json;
use futures_util::{SinkExt, StreamExt};
use ipnet::IpNet;
use serde::Serialize;
use std::{
    env, fmt,
    net::{IpAddr, SocketAddr},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    net::{lookup_host, TcpListener, TcpStream},
    sync::Semaphore,
};
use tokio_tungstenite::{
    accept_async_with_config, connect_async,
    tungstenite::{protocol::WebSocketConfig, Message},
};

const TRANSPORT_MODE_ENV: &str = "LUCY_TELEPHONY_TRANSPORT_MODE";
const MEDIA_WEBSOCKET_BIND_ENV: &str = "LUCY_GATEWAY_MEDIA_WEBSOCKET_BIND";
const ARI_BASE_URL_ENV: &str = "LUCY_ASTERISK_ARI_BASE_URL";
const ARI_USERNAME_ENV: &str = "LUCY_ASTERISK_ARI_USERNAME";
const ARI_PASSWORD_ENV: &str = "LUCY_ASTERISK_ARI_PASSWORD";
const ARI_APP_ENV: &str = "LUCY_ASTERISK_ARI_APP";
const ARI_RTP_BIND_ENV: &str = "LUCY_GATEWAY_ARI_RTP_BIND";
const ARI_RTP_ADVERTISED_ENV: &str = "LUCY_GATEWAY_ARI_RTP_ADVERTISED";
const ARI_RTP_PAYLOAD_TYPE_ENV: &str = "LUCY_GATEWAY_ARI_RTP_PAYLOAD_TYPE";
const ARI_RTP_CLOCK_RATE_ENV: &str = "LUCY_GATEWAY_ARI_RTP_CLOCK_RATE_HZ";
const ARI_REQUEST_TIMEOUT_ENV: &str = "LUCY_ASTERISK_ARI_REQUEST_TIMEOUT_MS";
const ARI_RTP_IDLE_TIMEOUT_ENV: &str = "LUCY_GATEWAY_ARI_RTP_IDLE_TIMEOUT_MS";
const ARI_RTP_ALLOWED_CIDRS_ENV: &str = "LUCY_GATEWAY_ARI_RTP_ALLOWED_CIDRS";
const ARI_EVENTS_WS_URL_ENV: &str = "LUCY_ASTERISK_ARI_EVENTS_WS_URL";
const ARI_EVENTS_HANDSHAKE_TIMEOUT_ENV: &str = "LUCY_ASTERISK_ARI_EVENTS_HANDSHAKE_TIMEOUT_MS";
const ARI_EVENTS_IDLE_TIMEOUT_ENV: &str = "LUCY_ASTERISK_ARI_EVENTS_IDLE_TIMEOUT_MS";
const ARI_CALLER_CHANNEL_ID_ENV: &str = "LUCY_ASTERISK_ARI_CALLER_CHANNEL_ID";
const ARI_ALLOW_INSECURE_HTTP_ENV: &str = "LUCY_ASTERISK_ARI_ALLOW_INSECURE_HTTP";
const CONTROL_WS_URL_ENV: &str = "LUCY_SESSION_WS_URL";
const CONTROL_CONNECT_TIMEOUT_ENV: &str = "LUCY_GATEWAY_CONTROL_CONNECT_TIMEOUT_MS";
const DEFAULT_MEDIA_WEBSOCKET_BIND: &str = "0.0.0.0:9093";
const DEFAULT_MEDIA_WEBSOCKET_ALLOWED_CIDRS: &str = "127.0.0.0/8,::1/128";
const DEFAULT_MEDIA_WEBSOCKET_HANDSHAKE_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_MEDIA_WEBSOCKET_IDLE_TIMEOUT_MS: u64 = 30_000;
const DEFAULT_MEDIA_WEBSOCKET_CONTROL_CONNECT_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_MEDIA_WEBSOCKET_MAX_SESSIONS: usize = 1_024;
const DEFAULT_ARI_RTP_PAYLOAD_TYPE: u8 = 118;
const DEFAULT_ARI_RTP_CLOCK_RATE_HZ: u32 = 16_000;
const DEFAULT_ARI_REQUEST_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_ARI_RTP_IDLE_TIMEOUT_MS: u64 = 30_000;
const DEFAULT_ARI_EVENTS_HANDSHAKE_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_ARI_EVENTS_IDLE_TIMEOUT_MS: u64 = 30_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransportMode {
    AudioSocket,
    MediaWebSocket,
    AriExternalMedia,
}

impl TransportMode {
    fn parse(value: &str) -> Result<Self, RuntimeError> {
        match value {
            "audiosocket" => Ok(Self::AudioSocket),
            "media_websocket" => Ok(Self::MediaWebSocket),
            "ari_external_media" => Ok(Self::AriExternalMedia),
            _ => Err(RuntimeError::config(format!(
                "{TRANSPORT_MODE_ENV} must be audiosocket, media_websocket, or ari_external_media"
            ))),
        }
    }
}

#[derive(Clone)]
pub struct AsteriskRuntimeConfig {
    pub mode: TransportMode,
    pub audio_socket: AudioSocketServerConfig,
    pub ari: Option<AriRuntimeConfig>,
}

impl AsteriskRuntimeConfig {
    pub fn from_values(
        mode: &str,
        audio_socket_bind: &str,
        control_ws_url: &str,
        control_token: &str,
    ) -> Result<Self, RuntimeError> {
        Ok(Self {
            mode: TransportMode::parse(mode)?,
            audio_socket: AudioSocketServerConfig::new(
                audio_socket_bind,
                control_ws_url,
                control_token,
            )
            .map_err(RuntimeError::from_server)?,
            ari: None,
        })
    }

    pub fn from_env() -> Result<Self, RuntimeError> {
        let mode = TransportMode::parse(
            &env::var(TRANSPORT_MODE_ENV).unwrap_or_else(|_| "audiosocket".to_string()),
        )?;
        let audio_socket =
            AudioSocketServerConfig::from_env().map_err(RuntimeError::from_server)?;
        let ari = (mode == TransportMode::AriExternalMedia)
            .then(AriRuntimeConfig::from_env)
            .transpose()?;
        Ok(Self {
            mode,
            audio_socket,
            ari,
        })
    }
}

#[derive(Clone)]
pub struct MediaWebSocketRuntimeConfig {
    pub bind_address: SocketAddr,
    pub control_ws_url: String,
    control_policy: ControlWebSocketPolicy,
    pub allowed_peer_cidrs: Arc<[IpNet]>,
    pub handshake_timeout: Duration,
    pub idle_timeout: Duration,
    pub control_connect_timeout: Duration,
    pub max_sessions: usize,
    pub media_plane: Option<FixtureMediaPlaneConfig>,
    pub provider_media: Option<ProviderMediaConfig>,
}

impl MediaWebSocketRuntimeConfig {
    pub fn new(
        bind_address: SocketAddr,
        control_ws_url: String,
        control_token: String,
    ) -> Result<Self, RuntimeError> {
        let control_policy =
            ControlWebSocketPolicy::new(&control_ws_url, false, Some(control_token))
                .map_err(RuntimeError::from_server)?;
        Ok(Self {
            bind_address,
            control_ws_url,
            control_policy,
            allowed_peer_cidrs: parse_peer_cidrs(DEFAULT_MEDIA_WEBSOCKET_ALLOWED_CIDRS)?,
            handshake_timeout: Duration::from_millis(DEFAULT_MEDIA_WEBSOCKET_HANDSHAKE_TIMEOUT_MS),
            idle_timeout: Duration::from_millis(DEFAULT_MEDIA_WEBSOCKET_IDLE_TIMEOUT_MS),
            control_connect_timeout: Duration::from_millis(
                DEFAULT_MEDIA_WEBSOCKET_CONTROL_CONNECT_TIMEOUT_MS,
            ),
            max_sessions: DEFAULT_MEDIA_WEBSOCKET_MAX_SESSIONS,
            media_plane: None,
            provider_media: None,
        })
    }

    pub fn from_env() -> Result<Self, RuntimeError> {
        let bind_address = env::var(MEDIA_WEBSOCKET_BIND_ENV)
            .unwrap_or_else(|_| DEFAULT_MEDIA_WEBSOCKET_BIND.to_string())
            .parse()
            .map_err(|_| {
                RuntimeError::config(format!(
                    "{MEDIA_WEBSOCKET_BIND_ENV} must be a socket address"
                ))
            })?;
        let control_ws_url = env::var(CONTROL_WS_URL_ENV).map_err(|_| {
            RuntimeError::config(format!(
                "{CONTROL_WS_URL_ENV} is required for media_websocket mode"
            ))
        })?;
        let control_policy =
            ControlWebSocketPolicy::from_env(&control_ws_url).map_err(RuntimeError::from_server)?;
        let mut config = Self {
            bind_address,
            control_ws_url,
            control_policy,
            allowed_peer_cidrs: parse_peer_cidrs(DEFAULT_MEDIA_WEBSOCKET_ALLOWED_CIDRS)?,
            handshake_timeout: Duration::from_millis(DEFAULT_MEDIA_WEBSOCKET_HANDSHAKE_TIMEOUT_MS),
            idle_timeout: Duration::from_millis(DEFAULT_MEDIA_WEBSOCKET_IDLE_TIMEOUT_MS),
            control_connect_timeout: Duration::from_millis(
                DEFAULT_MEDIA_WEBSOCKET_CONTROL_CONNECT_TIMEOUT_MS,
            ),
            max_sessions: DEFAULT_MEDIA_WEBSOCKET_MAX_SESSIONS,
            media_plane: None,
            provider_media: None,
        };
        match gateway_media_backend_from_env().map_err(RuntimeError::from_server)? {
            GatewayMediaBackend::Fixture(media_plane) => config.media_plane = Some(media_plane),
            GatewayMediaBackend::Provider(provider_media) => {
                config.provider_media = Some(*provider_media)
            }
        }
        Ok(config)
    }

    fn peer_allowed(&self, address: std::net::IpAddr) -> bool {
        self.allowed_peer_cidrs
            .iter()
            .any(|cidr| cidr.contains(&address))
    }
}

#[derive(Clone)]
pub struct AriRuntimeConfig {
    ari: AriExternalMediaConfig,
    rtp_bind: SocketAddr,
    payload_type: u8,
    clock_rate_hz: u32,
    rtp_idle_timeout: Duration,
    caller_channel_id: Option<String>,
    control_ws_url: Option<String>,
    control_policy: Option<ControlWebSocketPolicy>,
    rtp_allowed_cidrs: String,
    events_ws_url: Option<String>,
    events_handshake_timeout: Duration,
    events_idle_timeout: Duration,
    control_connect_timeout: Duration,
    media_plane: Option<FixtureMediaPlaneConfig>,
    provider_media: Option<ProviderMediaConfig>,
}

impl AriRuntimeConfig {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base_url: String,
        username: &str,
        password: &str,
        app: &str,
        rtp_bind: SocketAddr,
        rtp_advertised: impl fmt::Display,
        payload_type: u8,
        clock_rate_hz: u32,
        timeout: Duration,
        allow_insecure_http: bool,
    ) -> Result<Self, RuntimeError> {
        if timeout.is_zero() {
            return Err(RuntimeError::config(
                "ARI RTP idle timeout must be positive",
            ));
        }
        let ari = AriExternalMediaConfig::new(
            base_url,
            username.to_string(),
            password.to_string(),
            app.to_string(),
            rtp_advertised,
            AriMediaFormat::Slin16,
            timeout,
            allow_insecure_http,
        )
        .map_err(RuntimeError::from_ari)?;
        Ok(Self {
            ari,
            rtp_bind,
            payload_type,
            clock_rate_hz,
            rtp_idle_timeout: timeout,
            caller_channel_id: None,
            control_ws_url: None,
            control_policy: None,
            rtp_allowed_cidrs: super::ari_external_media::DEFAULT_ARI_RTP_ALLOWED_CIDRS.to_string(),
            events_ws_url: None,
            events_handshake_timeout: timeout,
            events_idle_timeout: timeout,
            control_connect_timeout: timeout,
            media_plane: None,
            provider_media: None,
        })
    }

    pub fn from_env() -> Result<Self, RuntimeError> {
        let base_url = required_env(ARI_BASE_URL_ENV)?;
        let username = required_env(ARI_USERNAME_ENV)?;
        let password = required_env(ARI_PASSWORD_ENV)?;
        let app = required_env(ARI_APP_ENV)?;
        let rtp_bind = socket_env(ARI_RTP_BIND_ENV)?;
        let rtp_advertised = required_env(ARI_RTP_ADVERTISED_ENV)?;
        let request_timeout = Duration::from_millis(u64_env(
            ARI_REQUEST_TIMEOUT_ENV,
            DEFAULT_ARI_REQUEST_TIMEOUT_MS,
        )?);
        let mut config = Self::new(
            base_url,
            &username,
            &password,
            &app,
            rtp_bind,
            rtp_advertised,
            u8_env(ARI_RTP_PAYLOAD_TYPE_ENV, DEFAULT_ARI_RTP_PAYLOAD_TYPE)?,
            u32_env(ARI_RTP_CLOCK_RATE_ENV, DEFAULT_ARI_RTP_CLOCK_RATE_HZ)?,
            request_timeout,
            bool_env(ARI_ALLOW_INSECURE_HTTP_ENV, false)?,
        )?;
        config.rtp_idle_timeout = Duration::from_millis(u64_env(
            ARI_RTP_IDLE_TIMEOUT_ENV,
            DEFAULT_ARI_RTP_IDLE_TIMEOUT_MS,
        )?);
        if config.rtp_idle_timeout.is_zero() {
            return Err(RuntimeError::config(
                "ARI RTP idle timeout must be positive",
            ));
        }
        config.caller_channel_id = Some(required_env(ARI_CALLER_CHANNEL_ID_ENV)?);
        config.control_ws_url = Some(required_env(CONTROL_WS_URL_ENV)?);
        config.events_ws_url = Some(required_env(ARI_EVENTS_WS_URL_ENV)?);
        config.control_policy = Some(
            ControlWebSocketPolicy::from_env(config.control_ws_url.as_deref().expect("required"))
                .map_err(RuntimeError::from_server)?,
        );
        config.rtp_allowed_cidrs = env::var(ARI_RTP_ALLOWED_CIDRS_ENV).unwrap_or_else(|_| {
            super::ari_external_media::DEFAULT_ARI_RTP_ALLOWED_CIDRS.to_string()
        });
        RtpReceiver::validate_allowed_cidrs(&config.rtp_allowed_cidrs)
            .map_err(RuntimeError::from_ari)?;
        config.events_handshake_timeout = Duration::from_millis(u64_env(
            ARI_EVENTS_HANDSHAKE_TIMEOUT_ENV,
            DEFAULT_ARI_EVENTS_HANDSHAKE_TIMEOUT_MS,
        )?);
        config.events_idle_timeout = Duration::from_millis(u64_env(
            ARI_EVENTS_IDLE_TIMEOUT_ENV,
            DEFAULT_ARI_EVENTS_IDLE_TIMEOUT_MS,
        )?);
        config.control_connect_timeout = Duration::from_millis(u64_env(
            CONTROL_CONNECT_TIMEOUT_ENV,
            DEFAULT_MEDIA_WEBSOCKET_CONTROL_CONNECT_TIMEOUT_MS,
        )?);
        for (name, value) in [
            (
                ARI_EVENTS_HANDSHAKE_TIMEOUT_ENV,
                config.events_handshake_timeout,
            ),
            (ARI_EVENTS_IDLE_TIMEOUT_ENV, config.events_idle_timeout),
            (CONTROL_CONNECT_TIMEOUT_ENV, config.control_connect_timeout),
        ] {
            if value.is_zero() {
                return Err(RuntimeError::config(format!("{name} must be positive")));
            }
        }
        match gateway_media_backend_from_env().map_err(RuntimeError::from_server)? {
            GatewayMediaBackend::Fixture(media_plane) => config.media_plane = Some(media_plane),
            GatewayMediaBackend::Provider(provider_media) => {
                config.provider_media = Some(*provider_media)
            }
        }
        Ok(config)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn with_lifecycle(
        mut self,
        caller_channel_id: String,
        control_ws_url: String,
        control_token: String,
        events_ws_url: String,
        events_handshake_timeout: Duration,
        events_idle_timeout: Duration,
        control_connect_timeout: Duration,
        media_plane: FixtureMediaPlaneConfig,
    ) -> Result<Self, RuntimeError> {
        let control_policy =
            ControlWebSocketPolicy::new(&control_ws_url, false, Some(control_token))
                .map_err(RuntimeError::from_server)?;
        if caller_channel_id.trim().is_empty()
            || events_handshake_timeout.is_zero()
            || events_idle_timeout.is_zero()
            || control_connect_timeout.is_zero()
        {
            return Err(RuntimeError::config(
                "ARI lifecycle identities and deadlines must be positive",
            ));
        }
        self.ari
            .events_request(&events_ws_url)
            .map_err(RuntimeError::from_ari)?;
        media_plane.open().map_err(RuntimeError::from_media)?;
        self.caller_channel_id = Some(caller_channel_id);
        self.control_ws_url = Some(control_ws_url);
        self.control_policy = Some(control_policy);
        self.events_ws_url = Some(events_ws_url);
        self.events_handshake_timeout = events_handshake_timeout;
        self.events_idle_timeout = events_idle_timeout;
        self.control_connect_timeout = control_connect_timeout;
        self.media_plane = Some(media_plane);
        Ok(self)
    }

    pub fn with_rtp_allowed_cidrs(mut self, allowed_cidrs: &str) -> Result<Self, RuntimeError> {
        RtpReceiver::validate_allowed_cidrs(allowed_cidrs).map_err(RuntimeError::from_ari)?;
        self.rtp_allowed_cidrs = allowed_cidrs.to_string();
        Ok(self)
    }
}

#[derive(Debug, Serialize, PartialEq, Eq)]
pub struct AriSessionReport {
    pub caller_channel_id: String,
    pub rtp_payload_bytes: usize,
}

pub async fn serve_transport(
    config: AsteriskRuntimeConfig,
    metrics: GatewayMetrics,
) -> Result<(), RuntimeError> {
    match config.mode {
        TransportMode::AudioSocket => {
            let listener = TcpListener::bind(config.audio_socket.bind_address)
                .await
                .map_err(|error| RuntimeError::operation("bind AudioSocket listener", error))?;
            serve_audio_socket(listener, config.audio_socket, metrics)
                .await
                .map_err(RuntimeError::from_server)
        }
        TransportMode::MediaWebSocket => {
            let mut media = MediaWebSocketRuntimeConfig::from_env()?;
            media.allowed_peer_cidrs = config.audio_socket.allowed_peer_cidrs.clone();
            media.handshake_timeout = config.audio_socket.handshake_timeout;
            media.idle_timeout = config.audio_socket.idle_timeout;
            media.control_connect_timeout = config.audio_socket.control_connect_timeout;
            media.max_sessions = config.audio_socket.max_sessions;
            media.media_plane = config.audio_socket.media_plane.clone();
            serve_media_websocket(media).await
        }
        TransportMode::AriExternalMedia => {
            serve_ari_external_media(
                config
                    .ari
                    .ok_or_else(|| RuntimeError::config("ARI runtime configuration is required"))?,
            )
            .await
        }
    }
}

async fn serve_ari_external_media(config: AriRuntimeConfig) -> Result<(), RuntimeError> {
    let caller = config
        .caller_channel_id
        .clone()
        .ok_or_else(|| RuntimeError::config("ARI caller channel is required"))?;
    run_ari_external_media_lifecycle(&config, &caller)
        .await
        .map(|_| ())
}

pub async fn serve_media_websocket(
    config: MediaWebSocketRuntimeConfig,
) -> Result<(), RuntimeError> {
    let listener = TcpListener::bind(config.bind_address)
        .await
        .map_err(|error| RuntimeError::operation("bind media WebSocket listener", error))?;
    let admission = Arc::new(Semaphore::new(config.max_sessions));
    loop {
        let (stream, peer) = listener
            .accept()
            .await
            .map_err(|error| RuntimeError::operation("accept media WebSocket", error))?;
        if !config.peer_allowed(peer.ip()) {
            continue;
        }
        let Ok(permit) = admission.clone().try_acquire_owned() else {
            continue;
        };
        let config = config.clone();
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(error) = serve_media_websocket_connection(stream, config).await {
                eprintln!("media WebSocket session failed: {error}");
            }
        });
    }
}

pub async fn serve_media_websocket_connection(
    stream: TcpStream,
    config: MediaWebSocketRuntimeConfig,
) -> Result<(), RuntimeError> {
    let peer = stream
        .peer_addr()
        .map_err(|error| RuntimeError::operation("read media WebSocket peer", error))?;
    if !config.peer_allowed(peer.ip()) {
        return Err(RuntimeError::config(
            "media WebSocket peer is not in the configured allowlist",
        ));
    }
    let websocket_config = WebSocketConfig::default()
        .max_message_size(Some(MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES))
        .max_frame_size(Some(MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES));
    let mut media = tokio::time::timeout(
        config.handshake_timeout,
        accept_async_with_config(stream, Some(websocket_config)),
    )
    .await
    .map_err(|_| RuntimeError::config("media WebSocket handshake timed out"))?
    .map_err(|error| RuntimeError::operation("accept media WebSocket", error))?;
    let mut adapter = MediaWebSocketAdapter::default();
    let first = tokio::time::timeout(config.handshake_timeout, media.next())
        .await
        .map_err(|_| RuntimeError::config("media WebSocket MEDIA_START handshake timed out"))?
        .ok_or_else(|| RuntimeError::config("media WebSocket closed before MEDIA_START"))?
        .map_err(|error| RuntimeError::operation("read media WebSocket", error))?;
    let Some(MediaWebSocketOutput::Control(started)) = adapter
        .handle_message(first, now_ms())
        .map_err(RuntimeError::from_media)?
    else {
        return Err(RuntimeError::config(
            "media WebSocket first frame did not start a session",
        ));
    };
    if started.message_type() != "session.started" {
        return Err(RuntimeError::config(
            "media WebSocket first frame did not start a session",
        ));
    }
    let (control, _) = tokio::time::timeout(
        config.control_connect_timeout,
        connect_async(
            config
                .control_policy
                .request()
                .map_err(RuntimeError::from_server)?,
        ),
    )
    .await
    .map_err(|_| RuntimeError::config("control WebSocket connection timed out"))?
    .map_err(|_| RuntimeError::config("control WebSocket connection failed"))?;
    let (mut control_sink, mut control_source) = control.split();
    send_control(&mut control_sink, &started).await?;
    let session_id = started.as_value()["session_id"]
        .as_str()
        .ok_or_else(|| RuntimeError::config("media WebSocket session has no id"))?
        .to_string();
    let mut last_directive_seq = None;
    let mut media_session =
        MediaSession::new(config.media_plane.clone(), config.provider_media.clone())
            .map_err(RuntimeError::from_media)?;
    let mut transport_metrics =
        TransportMetricsSampler::new(super::metrics::DEFAULT_METRICS_EMIT_INTERVAL_MS);
    let mut playback_tick = tokio::time::interval(media_session.playback_pacing());
    playback_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut playback_paused = false;
    let idle_timeout = tokio::time::sleep(config.idle_timeout);
    tokio::pin!(idle_timeout);
    loop {
        tokio::select! {
            _ = &mut idle_timeout => return Err(RuntimeError::config("media WebSocket session idle timeout elapsed")),
            incoming = control_source.next() => {
                let incoming = incoming.ok_or_else(|| RuntimeError::config("control WebSocket closed unexpectedly"))?.map_err(|error| RuntimeError::operation("read control directive", error))?;
                idle_timeout.as_mut().reset(tokio::time::Instant::now() + config.idle_timeout);
                match incoming {
                    Message::Text(text) => {
                        let directive: crate::control::schema::ControlMessage = serde_json::from_str(text.as_ref()).map_err(|error| RuntimeError::operation("decode control directive", error))?;
                        validate_asterisk_directive_envelope(
                            &directive,
                            &session_id,
                            &mut last_directive_seq,
                        )
                        .map_err(|error| RuntimeError::config(error.to_string()))?;
                        match parse_asterisk_directive(&directive).map_err(RuntimeError::from_media)? {
                            Some(AsteriskDirective::Playback { utterance_id, text, flush }) => {
                                for event in media_session.submit_playback(utterance_id, text, flush).map_err(RuntimeError::from_media)? {
                                    send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(RuntimeError::from_media)?).await?;
                                }
                            }
                            Some(AsteriskDirective::CancelPlayback { utterance_id }) => {
                                let cancellation = media_session.cancel_playback(&utterance_id).map_err(RuntimeError::from_media)?;
                                if cancellation.active_cancelled { media.send(MediaWebSocketAdapter::cancel_command()).await.map_err(|error| RuntimeError::operation("write Media WebSocket cancel", error))?; }
                                for event in cancellation.events {
                                    send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(RuntimeError::from_media)?).await?;
                                }
                            }
                            Some(AsteriskDirective::Hangup { reason }) => { media.send(MediaWebSocketAdapter::hangup_command()).await.map_err(|error| RuntimeError::operation("write Media WebSocket hangup", error))?; let ended = adapter.handle_message(Message::Close(None), now_ms()).map_err(RuntimeError::from_media)?.expect("close emits control"); if let MediaWebSocketOutput::Control(message) = ended { send_control(&mut control_sink, &message).await?; } let _ = reason; break; }
                            Some(AsteriskDirective::SendDtmf { .. }) => return Err(RuntimeError::config("media WebSocket dtmf.send requires ARI control")),
                            Some(AsteriskDirective::Transfer { .. }) => return Err(RuntimeError::config("media WebSocket transfer requires ARI control")), None => {}
                        }
                    }
                    Message::Ping(payload) => control_sink.send(Message::Pong(payload)).await.map_err(|error| RuntimeError::operation("reply to control ping", error))?,
                    Message::Pong(_) => {}
                    Message::Close(_) => return Err(RuntimeError::config("control WebSocket closed unexpectedly")),
                    Message::Binary(_) | Message::Frame(_) => {}
                }
            }
            incoming = media.next() => {
                let message = incoming.ok_or_else(|| RuntimeError::config("media WebSocket closed unexpectedly"))?.map_err(|error| RuntimeError::operation("read media WebSocket", error))?;
                idle_timeout.as_mut().reset(tokio::time::Instant::now() + config.idle_timeout);
                if let Message::Pong(payload) = &message {
                    if let Some(sample) = transport_metrics.complete_probe(payload.as_ref(), now_ms()) {
                        if let Some(control) = adapter.transport_metrics(sample, now_ms()).map_err(RuntimeError::from_media)? {
                            send_control(&mut control_sink, &control).await?;
                        }
                    }
                    continue;
                }
                match adapter.handle_message(message, now_ms()).map_err(RuntimeError::from_media)? {
                    Some(MediaWebSocketOutput::Control(message)) => { send_control(&mut control_sink, &message).await?; if message.message_type() == "session.ended" { break; } }
                    Some(MediaWebSocketOutput::Media(frame)) => if media_session.has_backend() { let arrived_at_ms = now_ms(); transport_metrics.observe_pcm(arrived_at_ms, pcm_duration_ms(frame.bytes.len(), frame.sample_rate_hz)); let events = media_session.push_audio(&frame.bytes, frame.sample_rate_hz, arrived_at_ms).map_err(RuntimeError::from_media)?; for event in events { send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(RuntimeError::from_media)?).await?; } if let Some(payload) = transport_metrics.begin_probe(arrived_at_ms) { media.send(Message::Ping(payload.into())).await.map_err(|error| RuntimeError::operation("send media transport metrics ping", error))?; } }
                    Some(MediaWebSocketOutput::FlowControl(signal)) => {
                        playback_paused = matches!(signal, super::media_websocket::MediaWebSocketSignal::Pause);
                    }
                    Some(MediaWebSocketOutput::ReplyPong(payload)) => media.send(Message::Pong(payload.into())).await.map_err(|error| RuntimeError::operation("reply to media WebSocket ping", error))?,
                    None => {}
                }
            }
            _ = playback_tick.tick(), if !playback_paused => { for event in media_session.drain_provider_events().map_err(RuntimeError::from_media)? { send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(RuntimeError::from_media)?).await?; } match media_session.next_playback(super::media_websocket::SLIN_SAMPLE_RATE_HZ).map_err(RuntimeError::from_media)? { PlaybackTick::Audio(pcm) => media.send(adapter.media_command(pcm).map_err(RuntimeError::from_media)?).await.map_err(|error| RuntimeError::operation("write Media WebSocket playback", error))?, PlaybackTick::Finished { event, next_started } => {
                let MediaPlaneEvent::PlaybackFinished { utterance_id, mark_chars } = event else {
                    return Err(RuntimeError::config("media WebSocket playback finished with an invalid event"));
                };
                media.send(adapter.mark_command(&utterance_id, mark_chars).map_err(RuntimeError::from_media)?).await.map_err(|error| RuntimeError::operation("write Media WebSocket media mark", error))?;
                if let Some(event) = next_started {
                    send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(RuntimeError::from_media)?).await?;
                }
            }, PlaybackTick::Idle => {} } }
        }
    }
    let _ = control_sink.close().await;
    Ok(())
}

pub async fn run_ari_external_media_session(
    config: &AriRuntimeConfig,
    caller_channel_id: &str,
) -> Result<AriSessionReport, RuntimeError> {
    let trusted_source_ips = resolve_ari_rtp_peer_ips(&config.ari).await?;
    let mut receiver = RtpReceiver::bind_with_allowed_and_trusted_ips(
        config.rtp_bind,
        caller_channel_id.to_string(),
        config.payload_type,
        config.clock_rate_hz,
        &config.rtp_allowed_cidrs,
        &trusted_source_ips,
        None,
        None,
    )
    .await
    .map_err(RuntimeError::from_ari)?;
    let client = AriExternalMediaClient::new(config.ari.clone()).map_err(RuntimeError::from_ari)?;
    let session = client
        .connect(caller_channel_id)
        .await
        .map_err(RuntimeError::from_ari)?;
    let received = tokio::time::timeout(config.rtp_idle_timeout, receiver.receive(now_ms())).await;
    let cleanup = client
        .cleanup(&session)
        .await
        .map_err(RuntimeError::from_ari);
    let frame = received
        .map_err(|_| RuntimeError::operation("receive ARI RTP", "idle timeout"))?
        .map_err(RuntimeError::from_ari)?;
    cleanup?;
    Ok(AriSessionReport {
        caller_channel_id: caller_channel_id.to_string(),
        rtp_payload_bytes: frame.payload.len(),
    })
}

/// Runs the production ARI fallback. RTP remains local; the only WebSocket
/// payloads are schema-validated control events and directives.
pub async fn run_ari_external_media_lifecycle(
    config: &AriRuntimeConfig,
    caller_channel_id: &str,
) -> Result<AriSessionReport, RuntimeError> {
    if config.control_ws_url.is_none() {
        return Err(RuntimeError::config(
            "ARI control WebSocket URL is required",
        ));
    }
    let events_url = config
        .events_ws_url
        .as_deref()
        .ok_or_else(|| RuntimeError::config("ARI events WebSocket URL is required"))?;
    if config.media_plane.is_none() && config.provider_media.is_none() {
        return Err(RuntimeError::config("ARI media backend is required"));
    }
    let trusted_source_ips = resolve_ari_rtp_peer_ips(&config.ari).await?;
    let mut receiver = RtpReceiver::bind_with_allowed_and_trusted_ips(
        config.rtp_bind,
        caller_channel_id.to_string(),
        config.payload_type,
        config.clock_rate_hz,
        &config.rtp_allowed_cidrs,
        &trusted_source_ips,
        None,
        None,
    )
    .await
    .map_err(RuntimeError::from_ari)?;
    let event_request = config
        .ari
        .events_request(events_url)
        .map_err(RuntimeError::from_ari)?;
    let (events, _) = tokio::time::timeout(
        config.events_handshake_timeout,
        connect_async(event_request),
    )
    .await
    .map_err(|_| RuntimeError::config("ARI events WebSocket connection timed out"))?
    .map_err(|_| RuntimeError::config("ARI events WebSocket connection failed"))?;
    let control_request = config
        .control_policy
        .as_ref()
        .ok_or_else(|| RuntimeError::config("ARI control WebSocket policy is required"))?
        .request()
        .map_err(RuntimeError::from_server)?;
    let (control, _) = tokio::time::timeout(
        config.control_connect_timeout,
        connect_async(control_request),
    )
    .await
    .map_err(|_| RuntimeError::config("control WebSocket connection timed out"))?
    .map_err(|_| RuntimeError::config("control WebSocket connection failed"))?;
    let (mut control_sink, mut control_source) = control.split();
    let (_, mut events_source) = events.split();
    let client = AriExternalMediaClient::new(config.ari.clone()).map_err(RuntimeError::from_ari)?;
    let session = client
        .connect(caller_channel_id)
        .await
        .map_err(RuntimeError::from_ari)?;
    let mut emitter = AriControlEmitter::new(caller_channel_id);
    let result = async {
        send_control(&mut control_sink, &emitter.started()).await?;
        let mut media_session = MediaSession::new(config.media_plane.clone(), config.provider_media.clone())
            .map_err(RuntimeError::from_media)?;
        let mut playback_tick = tokio::time::interval(media_session.playback_pacing());
        playback_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let idle = tokio::time::sleep(config.rtp_idle_timeout.min(config.events_idle_timeout));
        tokio::pin!(idle);
        let mut last_directive_seq = None;
        let mut payload_bytes = 0usize;
        loop {
            tokio::select! {
                _ = &mut idle => return Err(RuntimeError::config("ARI externalMedia session idle timeout elapsed")),
                event = events_source.next() => {
                    let event = event.ok_or_else(|| RuntimeError::config("ARI events WebSocket closed unexpectedly"))?.map_err(|error| RuntimeError::operation("read ARI event", error))?;
                    idle.as_mut().reset(tokio::time::Instant::now() + config.events_idle_timeout);
                    if let Message::Text(text) = event {
                        if let Some(message) = emitter.ari_event(serde_json::from_str(text.as_ref()).map_err(|_| RuntimeError::config("ARI event is invalid JSON"))?, now_ms())? {
                            let ended = message.message_type() == "session.ended";
                            send_control(&mut control_sink, &message).await?;
                            if ended { break; }
                        }
                    }
                }
                incoming = control_source.next() => {
                    let incoming = incoming.ok_or_else(|| RuntimeError::config("control WebSocket closed unexpectedly"))?.map_err(|error| RuntimeError::operation("read control directive", error))?;
                    idle.as_mut().reset(tokio::time::Instant::now() + config.rtp_idle_timeout);
                    if let Message::Text(text) = incoming {
                        let directive: crate::control::schema::ControlMessage = serde_json::from_str(text.as_ref()).map_err(|error| RuntimeError::operation("decode control directive", error))?;
                        validate_asterisk_directive_envelope(
                            &directive,
                            caller_channel_id,
                            &mut last_directive_seq,
                        )
                        .map_err(|error| RuntimeError::config(error.to_string()))?;
                        match parse_asterisk_directive(&directive).map_err(RuntimeError::from_media)? {
                            Some(AsteriskDirective::Playback { utterance_id, text, flush }) => {
                                for event in media_session.submit_playback(utterance_id, text, flush).map_err(RuntimeError::from_media)? {
                                    send_control(&mut control_sink, &emitter.media_event(event, now_ms())?).await?;
                                }
                            },
                            Some(AsteriskDirective::CancelPlayback { utterance_id }) => {
                                for event in media_session.cancel_playback(&utterance_id).map_err(RuntimeError::from_media)?.events {
                                    send_control(&mut control_sink, &emitter.media_event(event, now_ms())?).await?;
                                }
                            },
                            Some(AsteriskDirective::SendDtmf { digits }) => client.send_dtmf(caller_channel_id, &digits).await.map_err(RuntimeError::from_ari)?,
                            Some(AsteriskDirective::Transfer { target }) => client.transfer(caller_channel_id, &target).await.map_err(RuntimeError::from_ari)?,
                            Some(AsteriskDirective::Hangup { reason }) => { client.hangup(caller_channel_id).await.map_err(RuntimeError::from_ari)?; send_control(&mut control_sink, &emitter.ended(&reason, now_ms())?).await?; break; }
                            None => {}
                        }
                    }
                }
                frame = receiver.receive(now_ms()) => {
                    let frame = frame.map_err(RuntimeError::from_ari)?;
                    idle.as_mut().reset(tokio::time::Instant::now() + config.rtp_idle_timeout);
                    payload_bytes += frame.payload.len();
                    let at_ms = now_ms();
                    let events = media_session.push_audio(&frame.payload, config.clock_rate_hz, at_ms).map_err(RuntimeError::from_media)?;
                    for event in events { send_control(&mut control_sink, &emitter.media_event(event, at_ms)?).await?; }
                    let (jitter_ms, packet_loss) = receiver.metrics_snapshot();
                    if let Some(metrics) = emitter.metrics(jitter_ms, packet_loss, now_ms())? {
                        send_control(&mut control_sink, &metrics).await?;
                    }
                }
                _ = playback_tick.tick() => { for event in media_session.drain_provider_events().map_err(RuntimeError::from_media)? { send_control(&mut control_sink, &emitter.media_event(event, now_ms())?).await?; } match media_session.next_playback(config.clock_rate_hz).map_err(RuntimeError::from_media)? {
                    PlaybackTick::Audio(pcm) => receiver.send_payload(&pcm).await.map_err(RuntimeError::from_ari)?,
                    PlaybackTick::Finished { event, next_started } => {
                        send_control(&mut control_sink, &emitter.media_event(event, now_ms())?).await?;
                        if let Some(event) = next_started {
                            send_control(&mut control_sink, &emitter.media_event(event, now_ms())?).await?;
                        }
                    },
                    PlaybackTick::Idle => {}
                } }
            }
        }
        Ok(AriSessionReport { caller_channel_id: caller_channel_id.to_string(), rtp_payload_bytes: payload_bytes })
    }.await;
    let cleanup = client
        .cleanup(&session)
        .await
        .map_err(RuntimeError::from_ari);
    let _ = control_sink.close().await;
    match (result, cleanup) {
        (Ok(report), Ok(())) => Ok(report),
        (Err(error), _) => Err(error),
        (Ok(_), Err(error)) => Err(error),
    }
}

async fn resolve_ari_rtp_peer_ips(
    config: &AriExternalMediaConfig,
) -> Result<Vec<IpAddr>, RuntimeError> {
    let host = config.origin_host();
    let mut addresses = lookup_host((host, 0))
        .await
        .map_err(|error| RuntimeError::operation("resolve ARI RTP peer", error))?
        .map(|address| address.ip())
        .collect::<Vec<_>>();
    addresses.sort_unstable();
    addresses.dedup();
    if addresses.is_empty() {
        return Err(RuntimeError::config(
            "ARI RTP peer resolved to no addresses",
        ));
    }
    Ok(addresses)
}

struct AriControlEmitter {
    session_id: String,
    seq: u64,
    turn: Option<String>,
    latest_turn: Option<String>,
    next_turn: u64,
    ended: bool,
}
impl AriControlEmitter {
    fn new(session_id: &str) -> Self {
        Self {
            session_id: session_id.to_string(),
            seq: 0,
            turn: None,
            latest_turn: None,
            next_turn: 0,
            ended: false,
        }
    }
    fn message(
        &mut self,
        mut value: serde_json::Value,
    ) -> Result<crate::control::schema::ControlMessage, RuntimeError> {
        value["seq"] = self.seq.into();
        self.seq += 1;
        serde_json::from_value(value)
            .map_err(|error| RuntimeError::operation("control-schema conversion", error))
    }
    fn started(&mut self) -> crate::control::schema::ControlMessage {
        self.message(serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"session.started","session_id":self.session_id,"turn_id":"ari-turn-0","ts_ms":now_ms(),"caller":self.session_id,"transport":"asterisk/ari_external_media","codecs":["pcm16/16000"],"features":["dtmf"]})).expect("static session.started schema")
    }
    fn media_event(
        &mut self,
        event: super::media_plane::MediaPlaneEvent,
        ts: u64,
    ) -> Result<crate::control::schema::ControlMessage, RuntimeError> {
        use super::media_plane::MediaPlaneEvent::*;
        let value = match event {
            SpeechStarted { at_ms } => {
                self.next_turn += 1;
                let turn = format!("turn-{}", self.next_turn);
                self.turn = Some(turn.clone());
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"vad.speech_start","session_id":self.session_id,"turn_id":turn,"ts_ms":ts,"at_ms":at_ms})
            }
            TranscriptPartial {
                text,
                stability,
                provider,
                ..
            } => {
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"stt.partial","session_id":self.session_id,"turn_id":self.turn.clone().ok_or_else(||RuntimeError::config("ARI partial has no active turn"))?,"ts_ms":ts,"text":text,"stability":stability,"provider":provider})
            }
            SpeechEnded { at_ms, speech_ms } => {
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"vad.speech_end","session_id":self.session_id,"turn_id":self.turn.clone().ok_or_else(||RuntimeError::config("ARI speech end has no active turn"))?,"ts_ms":ts,"at_ms":at_ms,"speech_ms":speech_ms})
            }
            TranscriptFinal {
                text,
                stt_ms,
                provider,
                ..
            } => {
                let turn = self
                    .turn
                    .take()
                    .ok_or_else(|| RuntimeError::config("ARI final has no active turn"))?;
                self.latest_turn = Some(turn.clone());
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"stt.final","session_id":self.session_id,"turn_id":turn,"ts_ms":ts,"text":text,"provider":provider,"stt_ms":stt_ms})
            }
            PlaybackStarted { utterance_id } => {
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"tts.playback","session_id":self.session_id,"seq":0,"ts_ms":ts,"utterance_id":utterance_id,"state":"started","mark_chars":0})
            }
            PlaybackFinished {
                utterance_id,
                mark_chars,
            } => {
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"tts.playback","session_id":self.session_id,"seq":0,"ts_ms":ts,"utterance_id":utterance_id,"state":"finished","mark_chars":mark_chars})
            }
            PlaybackFlushed {
                utterance_id,
                mark_chars,
            } => {
                serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"tts.playback","session_id":self.session_id,"seq":0,"ts_ms":ts,"utterance_id":utterance_id,"state":"flushed","mark_chars":mark_chars})
            }
        };
        self.message(value)
    }
    fn metrics(
        &mut self,
        jitter_ms: f64,
        packet_loss: f64,
        ts: u64,
    ) -> Result<Option<crate::control::schema::ControlMessage>, RuntimeError> {
        let Some(turn_id) = self.turn.clone().or_else(|| self.latest_turn.clone()) else {
            return Ok(None);
        };
        self.message(serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"transport.metrics","session_id":self.session_id,"turn_id":turn_id,"ts_ms":ts,"jitter_ms":jitter_ms,"rtt_ms":0.0,"packet_loss":packet_loss})).map(Some)
    }
    fn ended(
        &mut self,
        reason: &str,
        ts: u64,
    ) -> Result<crate::control::schema::ControlMessage, RuntimeError> {
        if self.ended {
            return Err(RuntimeError::config("ARI session already ended"));
        }
        self.ended = true;
        self.message(serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"session.ended","session_id":self.session_id,"turn_id":"ari-turn-0","ts_ms":ts,"reason":reason}))
    }
    fn ari_event(
        &mut self,
        value: serde_json::Value,
        ts: u64,
    ) -> Result<Option<crate::control::schema::ControlMessage>, RuntimeError> {
        let channel = value
            .get("channel")
            .and_then(serde_json::Value::as_object)
            .and_then(|v| v.get("id"))
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| RuntimeError::config("ARI event channel id is required"))?;
        if channel != self.session_id {
            return Ok(None);
        }
        match value.get("type").and_then(serde_json::Value::as_str) {
            Some("ChannelDtmfReceived") => {
                let digit = value
                    .get("digit")
                    .and_then(serde_json::Value::as_str)
                    .filter(|v| v.len() == 1 && VALID_DTMF.contains(&v.as_bytes()[0]))
                    .ok_or_else(|| RuntimeError::config("ARI DTMF digit is invalid"))?;
                Ok(Some(self.message(serde_json::json!({"v":CONTROL_SCHEMA_VERSION,"type":"dtmf","session_id":self.session_id,"turn_id":"ari-turn-0","ts_ms":ts,"digit":digit.to_ascii_uppercase()}))?))
            }
            Some("ChannelDestroyed") | Some("StasisEnd") => {
                Ok(Some(self.ended("ari_channel_destroyed", ts)?))
            }
            _ => Ok(None),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeError(String);

impl RuntimeError {
    fn config(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    fn operation(operation: &str, detail: impl fmt::Display) -> Self {
        Self(format!("{operation} failed: {detail}"))
    }

    fn from_server(error: impl fmt::Display) -> Self {
        Self(error.to_string())
    }

    fn from_media(error: impl fmt::Display) -> Self {
        Self(error.to_string())
    }

    fn from_ari(error: impl fmt::Display) -> Self {
        Self(error.to_string())
    }
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for RuntimeError {}

fn parse_peer_cidrs(value: &str) -> Result<Arc<[IpNet]>, RuntimeError> {
    let cidrs = value
        .split(',')
        .map(str::trim)
        .map(|cidr| {
            cidr.parse::<IpNet>().map_err(|_| {
                RuntimeError::config("media WebSocket allowlist contains an invalid CIDR")
            })
        })
        .collect::<Result<Vec<_>, _>>()?;
    if cidrs.is_empty() {
        return Err(RuntimeError::config(
            "media WebSocket allowlist cannot be empty",
        ));
    }
    Ok(cidrs.into())
}

async fn send_control<S, E>(
    sink: &mut S,
    message: &crate::control::schema::ControlMessage,
) -> Result<(), RuntimeError>
where
    S: futures_util::Sink<Message, Error = E> + Unpin,
    E: fmt::Display,
{
    sink.send(Message::Text(
        canonical_json(message)
            .map_err(|error| RuntimeError::operation("encode media WebSocket control", error))?
            .into(),
    ))
    .await
    .map_err(|error| RuntimeError::operation("forward media WebSocket control", error))
}

fn required_env(name: &str) -> Result<String, RuntimeError> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| RuntimeError::config(format!("{name} is required")))
}

fn socket_env(name: &str) -> Result<SocketAddr, RuntimeError> {
    required_env(name)?
        .parse()
        .map_err(|_| RuntimeError::config(format!("{name} must be a socket address")))
}

fn u64_env(name: &str, default: u64) -> Result<u64, RuntimeError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| RuntimeError::config(format!("{name} must be an integer")))
    })
}

fn u32_env(name: &str, default: u32) -> Result<u32, RuntimeError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| RuntimeError::config(format!("{name} must be an integer")))
    })
}

fn u8_env(name: &str, default: u8) -> Result<u8, RuntimeError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| RuntimeError::config(format!("{name} must be an integer")))
    })
}

fn bool_env(name: &str, default: bool) -> Result<bool, RuntimeError> {
    match env::var(name).as_deref() {
        Ok("true") => Ok(true),
        Ok("false") => Ok(false),
        Ok(_) => Err(RuntimeError::config(format!(
            "{name} must be true or false"
        ))),
        Err(_) => Ok(default),
    }
}

fn pcm_duration_ms(bytes: usize, sample_rate_hz: u32) -> u64 {
    let bytes_per_sample = std::mem::size_of::<i16>() as u64;
    let duration = (bytes as u64).saturating_mul(1_000)
        / bytes_per_sample.saturating_mul(sample_rate_hz as u64);
    duration.max(1)
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
