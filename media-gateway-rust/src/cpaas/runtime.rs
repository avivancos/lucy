use super::call_control::{CpaasCallControlClient, CpaasCallControlConfig};
use super::{CpaasMediaStreamAdapter, CpaasOutput, CpaasProvider, MAX_CPAAAS_MESSAGE_BYTES};
use crate::asterisk::directives::CONTROL_SCHEMA_VERSION;
use crate::asterisk::media_plane::{FixtureMediaPlaneConfig, MediaPlaneEvent};
use crate::asterisk::media_session::{MediaSession, PlaybackTick};
use crate::asterisk::provider_media::ProviderMediaConfig;
use crate::asterisk::server::{
    gateway_media_backend_from_env, ControlWebSocketPolicy, GatewayMediaBackend,
};
use crate::control::schema::{canonical_json, ControlMessage};
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use futures_util::{Sink, SinkExt, StreamExt};
use hmac::{Hmac, Mac};
use ipnet::IpNet;
use sha1::Sha1;
use std::env;
use std::fmt;
use std::net::{IpAddr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use subtle::ConstantTimeEq;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Semaphore;
use tokio_tungstenite::{
    accept_hdr_async_with_config, connect_async,
    tungstenite::{
        handshake::server::{ErrorResponse, Request},
        http::StatusCode,
        protocol::WebSocketConfig,
        Message,
    },
};
use url::Url;

const PROVIDER_ENV: &str = "LUCY_CPAAAS_PROVIDER";
const MEDIA_BIND_ENV: &str = "LUCY_CPAAAS_MEDIA_BIND";
const PUBLIC_WS_URL_ENV: &str = "LUCY_CPAAAS_PUBLIC_WS_URL";
const STREAM_AUTH_TOKEN_ENV: &str = "LUCY_CPAAAS_STREAM_AUTH_TOKEN";
const ALLOWED_CIDRS_ENV: &str = "LUCY_CPAAAS_ALLOWED_CIDRS";
const HANDSHAKE_TIMEOUT_ENV: &str = "LUCY_CPAAAS_HANDSHAKE_TIMEOUT_MS";
const IDLE_TIMEOUT_ENV: &str = "LUCY_CPAAAS_IDLE_TIMEOUT_MS";
const MAX_SESSIONS_ENV: &str = "LUCY_CPAAAS_MAX_SESSIONS";
const CALL_CONTROL_BASE_URL_ENV: &str = "LUCY_CPAAAS_API_BASE_URL";
const ACCOUNT_ID_ENV: &str = "LUCY_CPAAAS_ACCOUNT_ID";
const API_KEY_ENV: &str = "LUCY_CPAAAS_API_KEY";
const ALLOW_INSECURE_LOCAL_ENV: &str = "LUCY_CPAAAS_ALLOW_INSECURE_LOCAL";
const CONTROL_WS_URL_ENV: &str = "LUCY_SESSION_WS_URL";
const CONTROL_TOKEN_ENV: &str = "LUCY_GATEWAY_CONTROL_TOKEN";
const DEFAULT_MEDIA_BIND: &str = "0.0.0.0:9094";
const DEFAULT_ALLOWED_CIDRS: &str = "127.0.0.0/8,::1/128";
const DEFAULT_HANDSHAKE_TIMEOUT_MS: u64 = 5_000;
const DEFAULT_IDLE_TIMEOUT_MS: u64 = 30_000;
const DEFAULT_MAX_SESSIONS: usize = 1_024;
const DEFAULT_CALL_CONTROL_TIMEOUT_MS: u64 = 5_000;
const MAX_AUTH_TOKEN_CHARS: usize = 4_000;

#[derive(Clone)]
pub struct CpaasHandshakeAuth {
    provider: CpaasProvider,
    public_url: String,
    path: String,
    secret: String,
}

impl CpaasHandshakeAuth {
    pub fn new(
        provider: CpaasProvider,
        public_url: &str,
        secret: &str,
        allow_insecure_loopback: bool,
    ) -> Result<Self, CpaasRuntimeError> {
        let url = Url::parse(public_url)
            .map_err(|_| CpaasRuntimeError::config("CPaaS public WebSocket URL is invalid"))?;
        if !matches!(url.scheme(), "ws" | "wss") {
            return Err(CpaasRuntimeError::config(
                "CPaaS public WebSocket URL must use ws or wss",
            ));
        }
        if !url.username().is_empty()
            || url.password().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(CpaasRuntimeError::config(
                "CPaaS public WebSocket URL cannot contain credentials, query, or fragment",
            ));
        }
        let loopback = url.host_str().is_some_and(|host| {
            host.eq_ignore_ascii_case("localhost")
                || host
                    .parse::<IpAddr>()
                    .is_ok_and(|address| address.is_loopback())
        });
        if url.scheme() == "ws" && !(allow_insecure_loopback && loopback) {
            return Err(CpaasRuntimeError::config(
                "CPaaS public WebSocket URL must use wss outside local protocol tests",
            ));
        }
        validate_secret(secret)?;
        Ok(Self {
            provider,
            public_url: public_url.to_string(),
            path: url.path().to_string(),
            secret: secret.to_string(),
        })
    }

    pub fn authorize(&self, request: &Request) -> bool {
        if request.uri().path() != self.path {
            return false;
        }
        match self.provider {
            CpaasProvider::Telnyx => request
                .headers()
                .get("x-telnyx-streaming-auth-token")
                .and_then(|value| value.to_str().ok())
                .is_some_and(|candidate| {
                    candidate.len() == self.secret.len()
                        && candidate.as_bytes().ct_eq(self.secret.as_bytes()).into()
                }),
            CpaasProvider::Twilio => {
                let Some(signature) = request
                    .headers()
                    .get("x-twilio-signature")
                    .and_then(|value| value.to_str().ok())
                else {
                    return false;
                };
                let Ok(signature) = BASE64.decode(signature) else {
                    return false;
                };
                let Ok(mut mac) = Hmac::<Sha1>::new_from_slice(self.secret.as_bytes()) else {
                    return false;
                };
                mac.update(self.public_url.as_bytes());
                mac.verify_slice(&signature).is_ok()
            }
        }
    }
}

#[derive(Clone)]
pub struct CpaasRuntimeConfig {
    pub provider: CpaasProvider,
    pub bind_address: SocketAddr,
    auth: CpaasHandshakeAuth,
    control_policy: ControlWebSocketPolicy,
    pub allowed_peer_cidrs: Arc<[IpNet]>,
    pub handshake_timeout: Duration,
    pub idle_timeout: Duration,
    pub max_sessions: usize,
    pub media_plane: Option<FixtureMediaPlaneConfig>,
    pub provider_media: Option<ProviderMediaConfig>,
    pub call_control: Option<CpaasCallControlClient>,
}

impl CpaasRuntimeConfig {
    pub fn new(
        provider: CpaasProvider,
        public_ws_url: &str,
        stream_auth_secret: &str,
        control_ws_url: &str,
        control_token: &str,
        allow_insecure_local: bool,
    ) -> Result<Self, CpaasRuntimeError> {
        Ok(Self {
            provider,
            bind_address: "127.0.0.1:0"
                .parse()
                .expect("static loopback socket address"),
            auth: CpaasHandshakeAuth::new(
                provider,
                public_ws_url,
                stream_auth_secret,
                allow_insecure_local,
            )?,
            control_policy: ControlWebSocketPolicy::new(
                control_ws_url,
                false,
                Some(control_token.to_string()),
            )
            .map_err(|error| CpaasRuntimeError::config(error.to_string()))?,
            allowed_peer_cidrs: parse_cidrs(DEFAULT_ALLOWED_CIDRS)?.into(),
            handshake_timeout: Duration::from_millis(DEFAULT_HANDSHAKE_TIMEOUT_MS),
            idle_timeout: Duration::from_millis(DEFAULT_IDLE_TIMEOUT_MS),
            max_sessions: DEFAULT_MAX_SESSIONS,
            media_plane: None,
            provider_media: None,
            call_control: None,
        })
    }

    pub fn from_env() -> Result<Self, CpaasRuntimeError> {
        let provider = parse_provider(&required_env(PROVIDER_ENV)?)?;
        let allow_insecure_local = bool_env(ALLOW_INSECURE_LOCAL_ENV, false)?;
        let mut config = Self::new(
            provider,
            &required_env(PUBLIC_WS_URL_ENV)?,
            &required_env(STREAM_AUTH_TOKEN_ENV)?,
            &required_env(CONTROL_WS_URL_ENV)?,
            &required_env(CONTROL_TOKEN_ENV)?,
            allow_insecure_local,
        )?;
        config.bind_address = env::var(MEDIA_BIND_ENV)
            .unwrap_or_else(|_| DEFAULT_MEDIA_BIND.to_string())
            .parse()
            .map_err(|_| CpaasRuntimeError::config("CPaaS media bind must be a socket address"))?;
        config.allowed_peer_cidrs = parse_cidrs(
            &env::var(ALLOWED_CIDRS_ENV).unwrap_or_else(|_| DEFAULT_ALLOWED_CIDRS.to_string()),
        )?
        .into();
        config.handshake_timeout = Duration::from_millis(u64_env(
            HANDSHAKE_TIMEOUT_ENV,
            DEFAULT_HANDSHAKE_TIMEOUT_MS,
        )?);
        config.idle_timeout =
            Duration::from_millis(u64_env(IDLE_TIMEOUT_ENV, DEFAULT_IDLE_TIMEOUT_MS)?);
        config.max_sessions = usize_env(MAX_SESSIONS_ENV, DEFAULT_MAX_SESSIONS)?;
        if config.handshake_timeout.is_zero()
            || config.idle_timeout.is_zero()
            || config.max_sessions == 0
        {
            return Err(CpaasRuntimeError::config(
                "CPaaS timeouts and max sessions must be positive",
            ));
        }
        match gateway_media_backend_from_env()
            .map_err(|error| CpaasRuntimeError::config(error.to_string()))?
        {
            GatewayMediaBackend::Fixture(media_plane) => config.media_plane = Some(media_plane),
            GatewayMediaBackend::Provider(provider_media) => {
                config.provider_media = Some(*provider_media)
            }
        }
        let call_control_config = CpaasCallControlConfig::new(
            provider,
            &required_env(CALL_CONTROL_BASE_URL_ENV)?,
            env::var(ACCOUNT_ID_ENV).ok().as_deref(),
            &required_env(API_KEY_ENV)?,
            allow_insecure_local,
            Duration::from_millis(DEFAULT_CALL_CONTROL_TIMEOUT_MS),
        )
        .map_err(|error| CpaasRuntimeError::config(error.to_string()))?;
        config.call_control = Some(
            CpaasCallControlClient::new(call_control_config)
                .map_err(|error| CpaasRuntimeError::config(error.to_string()))?,
        );
        Ok(config)
    }

    fn peer_allowed(&self, address: IpAddr) -> bool {
        self.allowed_peer_cidrs
            .iter()
            .any(|cidr| cidr.contains(&address))
    }
}

pub async fn serve_cpaas(config: CpaasRuntimeConfig) -> Result<(), CpaasRuntimeError> {
    let listener = TcpListener::bind(config.bind_address)
        .await
        .map_err(|_| CpaasRuntimeError::operation("bind CPaaS media listener failed"))?;
    let admission = Arc::new(Semaphore::new(config.max_sessions));
    loop {
        let (stream, peer) = listener
            .accept()
            .await
            .map_err(|_| CpaasRuntimeError::operation("accept CPaaS media connection failed"))?;
        if !config.peer_allowed(peer.ip()) {
            continue;
        }
        let Ok(permit) = admission.clone().try_acquire_owned() else {
            continue;
        };
        let config = config.clone();
        tokio::spawn(async move {
            let _permit = permit;
            if let Err(error) = serve_cpaas_connection(stream, config).await {
                eprintln!("CPaaS media session failed: {error}");
            }
        });
    }
}

pub async fn serve_cpaas_connection(
    stream: TcpStream,
    config: CpaasRuntimeConfig,
) -> Result<(), CpaasRuntimeError> {
    let peer = stream
        .peer_addr()
        .map_err(|_| CpaasRuntimeError::operation("read CPaaS peer failed"))?;
    if !config.peer_allowed(peer.ip()) {
        return Err(CpaasRuntimeError::config(
            "CPaaS peer is not in the configured allowlist",
        ));
    }
    let auth = config.auth.clone();
    let websocket_config = WebSocketConfig::default()
        .max_message_size(Some(MAX_CPAAAS_MESSAGE_BYTES))
        .max_frame_size(Some(MAX_CPAAAS_MESSAGE_BYTES));
    let mut provider = tokio::time::timeout(
        config.handshake_timeout,
        accept_hdr_async_with_config(
            stream,
            move |request: &Request, response| {
                if auth.authorize(request) {
                    Ok(response)
                } else {
                    Err(unauthorized_response())
                }
            },
            Some(websocket_config),
        ),
    )
    .await
    .map_err(|_| CpaasRuntimeError::config("CPaaS WebSocket handshake timed out"))?
    .map_err(|_| CpaasRuntimeError::config("CPaaS WebSocket authentication failed"))?;

    let mut adapter = CpaasMediaStreamAdapter::new(config.provider);
    let started = loop {
        let incoming = tokio::time::timeout(config.handshake_timeout, provider.next())
            .await
            .map_err(|_| CpaasRuntimeError::config("CPaaS start handshake timed out"))?
            .ok_or_else(|| CpaasRuntimeError::config("CPaaS stream closed before start"))?
            .map_err(|_| CpaasRuntimeError::operation("read CPaaS handshake failed"))?;
        let outputs = adapter
            .handle_message(incoming, now_ms())
            .map_err(|error| CpaasRuntimeError::config(error.to_string()))?;
        if let Some(message) = outputs.into_iter().find_map(|output| match output {
            CpaasOutput::Control(message) if message.message_type() == "session.started" => {
                Some(message)
            }
            _ => None,
        }) {
            break message;
        }
    };

    let (control, _) = tokio::time::timeout(
        config.handshake_timeout,
        connect_async(
            config
                .control_policy
                .request()
                .map_err(|error| CpaasRuntimeError::config(error.to_string()))?,
        ),
    )
    .await
    .map_err(|_| CpaasRuntimeError::config("control WebSocket connection timed out"))?
    .map_err(|_| CpaasRuntimeError::config("control WebSocket connection failed"))?;
    let (mut control_sink, mut control_source) = control.split();
    send_control(&mut control_sink, &started).await?;
    let session_id = adapter
        .session_id()
        .ok_or_else(|| CpaasRuntimeError::config("CPaaS session id is missing"))?
        .to_string();
    let mut last_directive_seq = None;
    let mut media_session =
        MediaSession::new(config.media_plane.clone(), config.provider_media.clone())
            .map_err(|error| CpaasRuntimeError::operation(error.to_string()))?;
    let mut playback_tick = tokio::time::interval(media_session.playback_pacing());
    playback_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let idle = tokio::time::sleep(config.idle_timeout);
    tokio::pin!(idle);

    loop {
        tokio::select! {
            _ = &mut idle => return Err(CpaasRuntimeError::config("CPaaS media session idle timeout elapsed")),
            incoming = control_source.next() => {
                let incoming = incoming.ok_or_else(|| CpaasRuntimeError::config("control WebSocket closed unexpectedly"))?.map_err(|_| CpaasRuntimeError::operation("read control directive failed"))?;
                idle.as_mut().reset(tokio::time::Instant::now() + config.idle_timeout);
                match incoming {
                    Message::Text(text) => {
                        let message: ControlMessage = serde_json::from_str(text.as_ref()).map_err(|_| CpaasRuntimeError::config("control directive is invalid"))?;
                        let directive = parse_directive(&message, &session_id, &mut last_directive_seq)?;
                        match directive {
                            Some(CpaasDirective::Playback { utterance_id, text, flush }) => {
                                for event in media_session.submit_playback(utterance_id, text, flush).map_err(|error| CpaasRuntimeError::operation(error.to_string()))? {
                                    send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await?;
                                }
                            }
                            Some(CpaasDirective::Cancel { utterance_id }) => {
                                let cancellation = media_session.cancel_playback(&utterance_id).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?;
                                if cancellation.active_cancelled {
                                    provider.send(adapter.clear_message().map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await.map_err(|_| CpaasRuntimeError::operation("write CPaaS clear failed"))?;
                                }
                                for event in cancellation.events {
                                    send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await?;
                                }
                            }
                            Some(CpaasDirective::Hangup) => {
                                let call_control = config.call_control.as_ref().ok_or_else(|| CpaasRuntimeError::config("CPaaS call-control client is required for hangup"))?;
                                let call_id = adapter.call_id().ok_or_else(|| CpaasRuntimeError::config("CPaaS call id is missing"))?;
                                call_control.hangup(call_id).await.map_err(|error| CpaasRuntimeError::operation(error.to_string()))?;
                                provider.send(adapter.hangup_message().map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await.map_err(|_| CpaasRuntimeError::operation("close CPaaS media stream failed"))?;
                                let outputs = adapter.handle_message(Message::Close(None), now_ms()).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?;
                                for output in outputs {
                                    if let CpaasOutput::Control(message) = output { send_control(&mut control_sink, &message).await?; }
                                }
                                break;
                            }
                            None => {}
                        }
                    }
                    Message::Ping(payload) => control_sink.send(Message::Pong(payload)).await.map_err(|_| CpaasRuntimeError::operation("reply to control ping failed"))?,
                    Message::Close(_) => return Err(CpaasRuntimeError::config("control WebSocket closed unexpectedly")),
                    Message::Pong(_) | Message::Binary(_) | Message::Frame(_) => {}
                }
            }
            incoming = provider.next() => {
                let incoming = incoming.ok_or_else(|| CpaasRuntimeError::config("CPaaS WebSocket closed unexpectedly"))?.map_err(|_| CpaasRuntimeError::operation("read CPaaS media failed"))?;
                idle.as_mut().reset(tokio::time::Instant::now() + config.idle_timeout);
                for output in adapter.handle_message(incoming, now_ms()).map_err(|error| CpaasRuntimeError::config(error.to_string()))? {
                    match output {
                        CpaasOutput::Control(message) => {
                            let ended = message.message_type() == "session.ended";
                            send_control(&mut control_sink, &message).await?;
                            if ended { let _ = control_sink.close().await; return Ok(()); }
                        }
                        CpaasOutput::Media(frame) if media_session.has_backend() => {
                            for event in media_session.push_audio(&frame.pcm_s16le, frame.sample_rate_hz, now_ms()).map_err(|error| CpaasRuntimeError::operation(error.to_string()))? {
                                send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await?;
                            }
                        }
                        CpaasOutput::ReplyPong(payload) => provider.send(Message::Pong(payload.into())).await.map_err(|_| CpaasRuntimeError::operation("reply to CPaaS ping failed"))?,
                        CpaasOutput::Media(_) => {}
                    }
                }
            }
            _ = playback_tick.tick() => {
                for event in media_session.drain_provider_events().map_err(|error| CpaasRuntimeError::operation(error.to_string()))? {
                    send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await?;
                }
                let sample_rate_hz = adapter.sample_rate_hz().ok_or_else(|| CpaasRuntimeError::config("CPaaS sample rate is missing"))?;
                match media_session.next_playback(sample_rate_hz).map_err(|error| CpaasRuntimeError::operation(error.to_string()))? {
                    PlaybackTick::Audio(pcm) => provider.send(adapter.playback_message(&pcm).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await.map_err(|_| CpaasRuntimeError::operation("write CPaaS playback failed"))?,
                    PlaybackTick::Finished { event, next_started } => {
                        let MediaPlaneEvent::PlaybackFinished { utterance_id, mark_chars } = event else { return Err(CpaasRuntimeError::config("CPaaS playback finished with an invalid event")); };
                        provider.send(adapter.mark_message(&utterance_id, mark_chars).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await.map_err(|_| CpaasRuntimeError::operation("write CPaaS mark failed"))?;
                        if let Some(event) = next_started { send_control(&mut control_sink, &adapter.media_plane_event(event).map_err(|error| CpaasRuntimeError::operation(error.to_string()))?).await?; }
                    }
                    PlaybackTick::Idle => {}
                }
            }
        }
    }
    let _ = control_sink.close().await;
    Ok(())
}

enum CpaasDirective {
    Playback {
        utterance_id: String,
        text: String,
        flush: bool,
    },
    Cancel {
        utterance_id: String,
    },
    Hangup,
}

fn parse_directive(
    message: &ControlMessage,
    session_id: &str,
    last_seq: &mut Option<u64>,
) -> Result<Option<CpaasDirective>, CpaasRuntimeError> {
    let value = message.as_value();
    if value.get("v").and_then(serde_json::Value::as_u64) != Some(CONTROL_SCHEMA_VERSION) {
        return Err(CpaasRuntimeError::config(
            "control schema version does not match",
        ));
    }
    if value.get("session_id").and_then(serde_json::Value::as_str) != Some(session_id) {
        return Err(CpaasRuntimeError::config(
            "control session id does not match",
        ));
    }
    let seq = value
        .get("seq")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| CpaasRuntimeError::config("control sequence is invalid"))?;
    if last_seq.is_some_and(|previous| seq <= previous) {
        return Err(CpaasRuntimeError::config("control sequence must increase"));
    }
    *last_seq = Some(seq);
    let directive = match message.message_type() {
        "tts.speak" => CpaasDirective::Playback {
            utterance_id: directive_text(value, "utterance_id")?.to_string(),
            text: directive_text(value, "text")?.to_string(),
            flush: value
                .get("flush")
                .and_then(serde_json::Value::as_bool)
                .unwrap_or(false),
        },
        "tts.cancel" => CpaasDirective::Cancel {
            utterance_id: directive_text(value, "utterance_id")?.to_string(),
        },
        "session.end" => CpaasDirective::Hangup,
        "dtmf.send" | "transfer" => {
            return Err(CpaasRuntimeError::config(
                "CPaaS media stream does not support outbound DTMF or transfer",
            ))
        }
        _ => return Ok(None),
    };
    Ok(Some(directive))
}

fn directive_text<'a>(
    value: &'a serde_json::Value,
    field: &str,
) -> Result<&'a str, CpaasRuntimeError> {
    value
        .get(field)
        .and_then(serde_json::Value::as_str)
        .filter(|text| !text.trim().is_empty() && !text.chars().any(char::is_control))
        .ok_or_else(|| CpaasRuntimeError::config(format!("control {field} is invalid")))
}

async fn send_control<S, E>(sink: &mut S, message: &ControlMessage) -> Result<(), CpaasRuntimeError>
where
    S: Sink<Message, Error = E> + Unpin,
    E: fmt::Display,
{
    let text = canonical_json(message)
        .map_err(|_| CpaasRuntimeError::operation("encode control event failed"))?;
    sink.send(Message::Text(text.into()))
        .await
        .map_err(|_| CpaasRuntimeError::operation("write control event failed"))
}

fn unauthorized_response() -> ErrorResponse {
    tokio_tungstenite::tungstenite::http::Response::builder()
        .status(StatusCode::UNAUTHORIZED)
        .body(Some("unauthorized".to_string()))
        .expect("static unauthorized response")
}

fn parse_provider(value: &str) -> Result<CpaasProvider, CpaasRuntimeError> {
    match value {
        "telnyx" => Ok(CpaasProvider::Telnyx),
        "twilio" => Ok(CpaasProvider::Twilio),
        _ => Err(CpaasRuntimeError::config(
            "LUCY_CPAAAS_PROVIDER must be telnyx or twilio",
        )),
    }
}

fn parse_cidrs(value: &str) -> Result<Vec<IpNet>, CpaasRuntimeError> {
    let cidrs = value
        .split(',')
        .map(str::trim)
        .map(|value| {
            value
                .parse::<IpNet>()
                .map_err(|_| CpaasRuntimeError::config("CPaaS allowed CIDR is invalid"))
        })
        .collect::<Result<Vec<_>, _>>()?;
    if cidrs.is_empty() {
        return Err(CpaasRuntimeError::config(
            "CPaaS allowed CIDRs cannot be empty",
        ));
    }
    Ok(cidrs)
}

fn required_env(name: &str) -> Result<String, CpaasRuntimeError> {
    env::var(name)
        .ok()
        .filter(|value| !value.trim().is_empty())
        .ok_or_else(|| CpaasRuntimeError::config(format!("{name} is required")))
}

fn u64_env(name: &str, default: u64) -> Result<u64, CpaasRuntimeError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| CpaasRuntimeError::config(format!("{name} must be an integer")))
    })
}

fn usize_env(name: &str, default: usize) -> Result<usize, CpaasRuntimeError> {
    env::var(name).map_or(Ok(default), |value| {
        value
            .parse()
            .map_err(|_| CpaasRuntimeError::config(format!("{name} must be an integer")))
    })
}

fn bool_env(name: &str, default: bool) -> Result<bool, CpaasRuntimeError> {
    env::var(name).map_or(Ok(default), |value| match value.as_str() {
        "1" | "true" | "yes" => Ok(true),
        "0" | "false" | "no" => Ok(false),
        _ => Err(CpaasRuntimeError::config(format!(
            "{name} must be a boolean"
        ))),
    })
}

fn validate_secret(value: &str) -> Result<(), CpaasRuntimeError> {
    if value.trim().is_empty()
        || value.len() > MAX_AUTH_TOKEN_CHARS
        || value
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
    {
        return Err(CpaasRuntimeError::config(
            "CPaaS stream auth token is invalid",
        ));
    }
    Ok(())
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpaasRuntimeError(String);

impl CpaasRuntimeError {
    fn config(message: impl Into<String>) -> Self {
        Self(message.into())
    }
    fn operation(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for CpaasRuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for CpaasRuntimeError {}
