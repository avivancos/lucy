//! Direct, Rust-owned Deepgram/ElevenLabs media backend.
//!
//! This module deliberately exposes PCM and provider protocol handling only to
//! the media plane.  Its result is the existing coarse `MediaPlaneEvent` /
//! `PlaybackStep` contract; it never constructs control-channel audio payloads.

use super::media_plane::{MediaPlaneError, MediaPlaneEvent, PlaybackStep};
use base64::{engine::general_purpose::STANDARD, Engine};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{collections::VecDeque, fmt, time::Duration};
use tokio::{
    sync::{mpsc, oneshot},
    time::timeout,
};
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, http::HeaderValue, Message},
};
use url::Url;

pub const PRODUCTION_MEDIA_BACKEND: &str = "deepgram_elevenlabs";
pub const DEEPGRAM_PROVIDER: &str = "deepgram";
pub const ELEVENLABS_PROVIDER: &str = "elevenlabs";
const ELEVENLABS_STREAM_INPUT_PATH: &str = "/v1/text-to-speech";
const PROVIDER_PLAYBACK_AUDIO_QUEUE_CAPACITY: usize = 32;
const MIN_PROVIDER_STT_QUEUE_CAPACITY: usize = 32;
const MAX_PROVIDER_STT_QUEUE_CAPACITY: usize = 4_096;
const PROVIDER_EVENT_QUEUE_CAPACITY: usize = 64;
const NANOS_PER_SECOND: u128 = 1_000_000_000;
pub const DEFAULT_PROVIDER_PLAYBACK_FRAME_MS: u64 = 20;

#[derive(Clone)]
pub struct DeepgramConfig {
    pub url: String,
    pub model: String,
    api_key: String,
    pub sample_rate_hz: u32,
    pub frame_bytes: usize,
    pub endpointing_ms: u64,
    pub connect_timeout: Duration,
    pub idle_timeout: Duration,
}

impl DeepgramConfig {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        url: impl Into<String>,
        model: impl Into<String>,
        api_key: impl Into<String>,
        sample_rate_hz: u32,
        frame_bytes: usize,
        endpointing_ms: u64,
        connect_timeout: Duration,
        idle_timeout: Duration,
    ) -> Self {
        Self {
            url: url.into(),
            model: model.into(),
            api_key: api_key.into(),
            sample_rate_hz,
            frame_bytes,
            endpointing_ms,
            connect_timeout,
            idle_timeout,
        }
    }

    fn startup_queue_capacity(&self) -> Result<usize, MediaPlaneError> {
        let bytes_during_connect = self
            .connect_timeout
            .as_nanos()
            .saturating_mul(u128::from(self.sample_rate_hz))
            .saturating_mul(2);
        let frame_denominator = NANOS_PER_SECOND.saturating_mul(self.frame_bytes as u128);
        let timeout_frames = bytes_during_connect
            .div_ceil(frame_denominator)
            .saturating_add(1);
        let capacity = usize::try_from(timeout_frames)
            .unwrap_or(usize::MAX)
            .max(MIN_PROVIDER_STT_QUEUE_CAPACITY);
        if capacity > MAX_PROVIDER_STT_QUEUE_CAPACITY {
            return Err(MediaPlaneError::invalid(
                "Deepgram connect timeout and framing require an excessive startup buffer",
            ));
        }
        Ok(capacity)
    }
}

impl fmt::Debug for DeepgramConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("DeepgramConfig")
            .field("url", &self.url)
            .field("model", &self.model)
            .field("api_key", &"[redacted]")
            .field("sample_rate_hz", &self.sample_rate_hz)
            .field("frame_bytes", &self.frame_bytes)
            .field("endpointing_ms", &self.endpointing_ms)
            .field("connect_timeout", &self.connect_timeout)
            .field("idle_timeout", &self.idle_timeout)
            .finish()
    }
}

#[derive(Clone)]
pub struct ElevenLabsConfig {
    pub url: String,
    pub model: String,
    pub voice_id: String,
    api_key: String,
    pub output_format: String,
    pub output_sample_rate_hz: u32,
    pub playback_frame_ms: u64,
    pub connect_timeout: Duration,
    pub idle_timeout: Duration,
}

impl ElevenLabsConfig {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        url: impl Into<String>,
        model: impl Into<String>,
        voice_id: impl Into<String>,
        api_key: impl Into<String>,
        output_format: impl Into<String>,
        output_sample_rate_hz: u32,
        connect_timeout: Duration,
        idle_timeout: Duration,
    ) -> Self {
        Self {
            url: url.into(),
            model: model.into(),
            voice_id: voice_id.into(),
            api_key: api_key.into(),
            output_format: output_format.into(),
            output_sample_rate_hz,
            playback_frame_ms: DEFAULT_PROVIDER_PLAYBACK_FRAME_MS,
            connect_timeout,
            idle_timeout,
        }
    }

    pub fn with_playback_frame_ms(mut self, playback_frame_ms: u64) -> Self {
        self.playback_frame_ms = playback_frame_ms;
        self
    }
}

impl fmt::Debug for ElevenLabsConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ElevenLabsConfig")
            .field("url", &self.url)
            .field("model", &self.model)
            .field("voice_id", &self.voice_id)
            .field("api_key", &"[redacted]")
            .field("output_format", &self.output_format)
            .field("output_sample_rate_hz", &self.output_sample_rate_hz)
            .field("playback_frame_ms", &self.playback_frame_ms)
            .field("connect_timeout", &self.connect_timeout)
            .field("idle_timeout", &self.idle_timeout)
            .finish()
    }
}

#[derive(Clone)]
pub struct ProviderMediaConfig {
    pub deepgram: DeepgramConfig,
    pub elevenlabs: ElevenLabsConfig,
    local_protocol_test: bool,
    stt_startup_queue_capacity: usize,
}

impl fmt::Debug for ProviderMediaConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ProviderMediaConfig")
            .field("deepgram", &self.deepgram)
            .field("elevenlabs", &self.elevenlabs)
            .field("local_protocol_test", &self.local_protocol_test)
            .field(
                "stt_startup_queue_capacity",
                &self.stt_startup_queue_capacity,
            )
            .finish()
    }
}

impl ProviderMediaConfig {
    pub fn new(
        deepgram: DeepgramConfig,
        elevenlabs: ElevenLabsConfig,
    ) -> Result<Self, MediaPlaneError> {
        Self::validate(deepgram, elevenlabs, false)
    }
    pub fn local_protocol_test(
        deepgram: DeepgramConfig,
        elevenlabs: ElevenLabsConfig,
    ) -> Result<Self, MediaPlaneError> {
        Self::validate(deepgram, elevenlabs, true)
    }
    fn validate(
        deepgram: DeepgramConfig,
        elevenlabs: ElevenLabsConfig,
        local_protocol_test: bool,
    ) -> Result<Self, MediaPlaneError> {
        validate_provider_url(&deepgram.url, local_protocol_test, "Deepgram")?;
        validate_provider_url(&elevenlabs.url, local_protocol_test, "ElevenLabs")?;
        for (name, secret) in [
            ("Deepgram API key", &deepgram.api_key),
            ("ElevenLabs API key", &elevenlabs.api_key),
        ] {
            if secret.trim().is_empty() || secret.chars().any(char::is_control) {
                return Err(MediaPlaneError::invalid(format!("{name} is invalid")));
            }
        }
        if deepgram.model.trim().is_empty()
            || elevenlabs.model.trim().is_empty()
            || elevenlabs.voice_id.trim().is_empty()
            || elevenlabs.output_format.trim().is_empty()
        {
            return Err(MediaPlaneError::invalid(
                "provider model, voice, and output format must be non-empty",
            ));
        }
        if !matches!(deepgram.sample_rate_hz, 8_000 | 16_000)
            || !matches!(elevenlabs.output_sample_rate_hz, 8_000 | 16_000)
            || deepgram.frame_bytes == 0
            || deepgram.frame_bytes % 2 != 0
            || deepgram.endpointing_ms == 0
            || deepgram.connect_timeout.is_zero()
            || deepgram.idle_timeout.is_zero()
            || elevenlabs.connect_timeout.is_zero()
            || elevenlabs.idle_timeout.is_zero()
            || elevenlabs.playback_frame_ms == 0
        {
            return Err(MediaPlaneError::invalid("provider frame, endpointing, sample rate, and timeouts must be positive supported values"));
        }
        if elevenlabs.output_format != format!("pcm_{}", elevenlabs.output_sample_rate_hz) {
            return Err(MediaPlaneError::invalid(
                "ElevenLabs output format must match its PCM sample rate",
            ));
        }
        if u64::from(elevenlabs.output_sample_rate_hz)
            .saturating_mul(2)
            .saturating_mul(elevenlabs.playback_frame_ms)
            % 1_000
            != 0
        {
            return Err(MediaPlaneError::invalid(
                "ElevenLabs playback frame must contain whole PCM16 samples",
            ));
        }
        let stt_startup_queue_capacity = deepgram.startup_queue_capacity()?;
        Ok(Self {
            deepgram,
            elevenlabs,
            local_protocol_test,
            stt_startup_queue_capacity,
        })
    }
}

fn validate_provider_url(
    value: &str,
    local_protocol_test: bool,
    provider: &str,
) -> Result<(), MediaPlaneError> {
    let url = Url::parse(value)
        .map_err(|_| MediaPlaneError::invalid(format!("{provider} URL is invalid")))?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(MediaPlaneError::invalid(format!(
            "{provider} URL cannot contain credentials, query, or fragment"
        )));
    }
    if url.scheme() != "wss"
        && !(local_protocol_test
            && url.scheme() == "ws"
            && url.host_str().is_some_and(|host| {
                host == "localhost"
                    || host
                        .parse::<std::net::IpAddr>()
                        .is_ok_and(|address| address.is_loopback())
            }))
    {
        return Err(MediaPlaneError::invalid(format!(
            "{provider} URL must use wss outside local protocol tests"
        )));
    }
    Ok(())
}

pub struct ProviderMediaPlane {
    config: ProviderMediaConfig,
    stt_tx: Option<mpsc::Sender<(Vec<u8>, u64)>>,
    stt_events: Option<mpsc::Receiver<Result<MediaPlaneEvent, MediaPlaneError>>>,
    playback_rx: Option<mpsc::Receiver<Result<Option<Vec<u8>>, MediaPlaneError>>>,
    playback_cancel: Option<oneshot::Sender<()>>,
    playback_pcm: VecDeque<u8>,
    playback_done: bool,
    active: Option<(String, u64)>,
}

impl ProviderMediaPlane {
    pub fn new(config: ProviderMediaConfig) -> Self {
        Self {
            config,
            stt_tx: None,
            stt_events: None,
            playback_rx: None,
            playback_cancel: None,
            playback_pcm: VecDeque::new(),
            playback_done: false,
            active: None,
        }
    }
    pub fn push_audio(
        &mut self,
        pcm_s16le: &[u8],
        input_rate_hz: u32,
        at_ms: u64,
    ) -> Result<Vec<MediaPlaneEvent>, MediaPlaneError> {
        let pcm = resample_pcm_s16le(
            pcm_s16le,
            input_rate_hz,
            self.config.deepgram.sample_rate_hz,
        )?;
        if pcm.is_empty() {
            return Err(MediaPlaneError::invalid(
                "provider input must contain PCM16 samples",
            ));
        }
        self.ensure_stt_task();
        let sender = self.stt_tx.as_ref().expect("STT task initialized");
        for frame in pcm.chunks(self.config.deepgram.frame_bytes) {
            sender
                .try_send((frame.to_vec(), at_ms))
                .map_err(|_| MediaPlaneError::invalid("Deepgram PCM queue is full or closed"))?;
        }
        self.drain_events()
    }
    pub fn drain_events(&mut self) -> Result<Vec<MediaPlaneEvent>, MediaPlaneError> {
        let mut events = Vec::new();
        if let Some(receiver) = self.stt_events.as_mut() {
            while let Ok(event) = receiver.try_recv() {
                events.push(event?);
            }
        }
        Ok(events)
    }
    pub fn start_playback(
        &mut self,
        utterance_id: &str,
        text: &str,
        flush: bool,
    ) -> Result<MediaPlaneEvent, MediaPlaneError> {
        if utterance_id.trim().is_empty() || text.trim().is_empty() {
            return Err(MediaPlaneError::invalid(
                "provider playback utterance and text must be non-empty",
            ));
        }
        if self.active.is_some() {
            return Err(MediaPlaneError::invalid(
                "provider playback is already active",
            ));
        }
        let (audio_tx, audio_rx) = mpsc::channel(PROVIDER_PLAYBACK_AUDIO_QUEUE_CAPACITY);
        let (cancel_tx, cancel_rx) = oneshot::channel();
        spawn_tts_task(
            self.config.elevenlabs.clone(),
            text.to_string(),
            flush,
            audio_tx,
            cancel_rx,
        );
        self.playback_rx = Some(audio_rx);
        self.playback_cancel = Some(cancel_tx);
        self.playback_pcm.clear();
        self.playback_done = false;
        self.active = Some((utterance_id.to_string(), text.chars().count() as u64));
        Ok(MediaPlaneEvent::PlaybackStarted {
            utterance_id: utterance_id.to_string(),
        })
    }
    pub fn next_playback(&mut self, target_rate_hz: u32) -> Result<PlaybackStep, MediaPlaneError> {
        let Some(receiver) = self.playback_rx.as_mut() else {
            return Ok(PlaybackStep::Idle);
        };
        loop {
            match receiver.try_recv() {
                Ok(Ok(Some(audio))) => self.playback_pcm.extend(audio),
                Ok(Ok(None)) | Err(mpsc::error::TryRecvError::Disconnected) => {
                    self.playback_done = true;
                    break;
                }
                Ok(Err(error)) => return Err(error),
                Err(mpsc::error::TryRecvError::Empty) => break,
            }
        }
        let frame_bytes = (u64::from(self.config.elevenlabs.output_sample_rate_hz)
            * 2
            * self.config.elevenlabs.playback_frame_ms
            / 1_000) as usize;
        let available = if self.playback_pcm.len() >= frame_bytes {
            frame_bytes
        } else if self.playback_done {
            self.playback_pcm.len()
        } else {
            0
        };
        if available > 0 {
            let audio = self.playback_pcm.drain(..available).collect::<Vec<_>>();
            return Ok(PlaybackStep::Audio(resample_pcm_s16le(
                &audio,
                self.config.elevenlabs.output_sample_rate_hz,
                target_rate_hz,
            )?));
        }
        if self.playback_done {
            self.playback_rx = None;
            self.playback_cancel = None;
            self.playback_done = false;
            return Ok(match self.active.take() {
                Some((utterance_id, mark_chars)) => {
                    PlaybackStep::Finished(MediaPlaneEvent::PlaybackFinished {
                        utterance_id,
                        mark_chars,
                    })
                }
                None => PlaybackStep::Idle,
            });
        }
        Ok(PlaybackStep::Idle)
    }
    pub fn cancel_playback(
        &mut self,
        utterance_id: &str,
    ) -> Result<MediaPlaneEvent, MediaPlaneError> {
        let (active, mark_chars) = self
            .active
            .take()
            .ok_or_else(|| MediaPlaneError::invalid("provider playback is not active"))?;
        if utterance_id != "all" && utterance_id != active {
            self.active = Some((active, mark_chars));
            return Err(MediaPlaneError::invalid(
                "provider cancel does not match active utterance",
            ));
        }
        if let Some(cancel) = self.playback_cancel.take() {
            let _ = cancel.send(());
        }
        self.playback_rx = None;
        self.playback_pcm.clear();
        self.playback_done = false;
        Ok(MediaPlaneEvent::PlaybackFlushed {
            utterance_id: active,
            mark_chars: 0,
        })
    }
    fn ensure_stt_task(&mut self) {
        if self.stt_tx.is_some() {
            return;
        }
        let (audio_tx, audio_rx) = mpsc::channel(self.config.stt_startup_queue_capacity);
        let (event_tx, event_rx) = mpsc::channel(PROVIDER_EVENT_QUEUE_CAPACITY);
        spawn_stt_task(self.config.deepgram.clone(), audio_rx, event_tx);
        self.stt_tx = Some(audio_tx);
        self.stt_events = Some(event_rx);
    }
}

fn spawn_stt_task(
    config: DeepgramConfig,
    mut audio_rx: mpsc::Receiver<(Vec<u8>, u64)>,
    event_tx: mpsc::Sender<Result<MediaPlaneEvent, MediaPlaneError>>,
) {
    tokio::spawn(async move {
        let result = async {
            let mut url = Url::parse(&config.url).map_err(|_| MediaPlaneError::invalid("Deepgram URL is invalid"))?;
            url.query_pairs_mut().append_pair("model", &config.model).append_pair("encoding", "linear16").append_pair("sample_rate", &config.sample_rate_hz.to_string()).append_pair("channels", "1").append_pair("interim_results", "true").append_pair("endpointing", &config.endpointing_ms.to_string());
            let mut request = url.as_str().into_client_request().map_err(|_| MediaPlaneError::invalid("Deepgram request is invalid"))?;
            let authorization = HeaderValue::from_str(&format!("Token {}", config.api_key)).map_err(|_| MediaPlaneError::invalid("Deepgram credential is invalid"))?;
            request.headers_mut().insert("authorization", authorization);
            let (mut socket, _) = timeout(config.connect_timeout, connect_async(request)).await.map_err(|_| MediaPlaneError::invalid("Deepgram connection timed out"))?.map_err(|_| MediaPlaneError::invalid("Deepgram connection failed"))?;
            let mut speech_started_at = None;
            let mut speech_audio_ms = 0_u64;
            let mut final_parts = Vec::new();
            loop {
                tokio::select! {
                    maybe_audio = audio_rx.recv() => {
                        let Some((audio, at_ms)) = maybe_audio else { let _ = socket.close(None).await; return Ok(()); };
                        if speech_started_at.is_none() {
                            speech_started_at = Some(at_ms);
                            event_tx.send(Ok(MediaPlaneEvent::SpeechStarted { at_ms })).await.map_err(|_| MediaPlaneError::invalid("Deepgram event receiver closed"))?;
                        }
                        speech_audio_ms = speech_audio_ms.saturating_add(
                            (audio.len() as u64 / 2).saturating_mul(1_000)
                                / u64::from(config.sample_rate_hz),
                        );
                        timeout(config.idle_timeout, socket.send(Message::Binary(audio.into()))).await.map_err(|_| MediaPlaneError::invalid("Deepgram write timed out"))?.map_err(|_| MediaPlaneError::invalid("Deepgram write failed"))?;
                    }
                    incoming = timeout(config.idle_timeout, socket.next()) => {
                        let message = incoming.map_err(|_| MediaPlaneError::invalid("Deepgram read timed out"))?.ok_or_else(|| MediaPlaneError::invalid("Deepgram closed unexpectedly"))?.map_err(|_| MediaPlaneError::invalid("Deepgram read failed"))?;
                        let Message::Text(text) = message else { return Err(MediaPlaneError::invalid("Deepgram returned non-JSON frame")); };
                        let payload: Value = serde_json::from_str(text.as_ref()).map_err(|_| MediaPlaneError::invalid("Deepgram returned malformed JSON"))?;
                        if payload.get("type").and_then(Value::as_str) != Some("Results") { continue; }
                        let transcript = deepgram_transcript(&payload)?;
                        if transcript.is_empty() { continue; }
                        let is_final = payload.get("is_final").and_then(Value::as_bool).ok_or_else(|| MediaPlaneError::invalid("Deepgram final marker is invalid"))?;
                        let speech_final = payload.get("speech_final").and_then(Value::as_bool).unwrap_or(false);
                        let from_finalize = payload.get("from_finalize").and_then(Value::as_bool).unwrap_or(false);
                        let started = speech_started_at.unwrap_or(0);
                        let at_ms = started.saturating_add(speech_audio_ms);
                        if !is_final {
                            event_tx.send(Ok(MediaPlaneEvent::TranscriptPartial { text: transcript, stability: 0.0, at_ms, provider: DEEPGRAM_PROVIDER })).await.map_err(|_| MediaPlaneError::invalid("Deepgram event receiver closed"))?;
                            continue;
                        }
                        final_parts.push(transcript);
                        if speech_final || from_finalize {
                            event_tx.send(Ok(MediaPlaneEvent::SpeechEnded { at_ms, speech_ms: speech_audio_ms })).await.map_err(|_| MediaPlaneError::invalid("Deepgram event receiver closed"))?;
                            event_tx.send(Ok(MediaPlaneEvent::TranscriptFinal { text: final_parts.join(" "), stt_ms: 0, at_ms, provider: DEEPGRAM_PROVIDER })).await.map_err(|_| MediaPlaneError::invalid("Deepgram event receiver closed"))?;
                            speech_started_at = None;
                            speech_audio_ms = 0;
                            final_parts.clear();
                        }
                    }
                }
            }
        }.await;
        if let Err(error) = result {
            let _ = event_tx.send(Err(error)).await;
        }
    });
}

fn spawn_tts_task(
    config: ElevenLabsConfig,
    text: String,
    flush: bool,
    output_tx: mpsc::Sender<Result<Option<Vec<u8>>, MediaPlaneError>>,
    mut cancel_rx: oneshot::Receiver<()>,
) {
    tokio::spawn(async move {
        let result = async {
            let mut url = Url::parse(&config.url).map_err(|_| MediaPlaneError::invalid("ElevenLabs URL is invalid"))?;
            url.set_path(&format!("{ELEVENLABS_STREAM_INPUT_PATH}/{}/stream-input", config.voice_id));
            url.query_pairs_mut().append_pair("model_id", &config.model).append_pair("output_format", &config.output_format).append_pair("sync_alignment", "true");
            let (mut socket, _) = timeout(config.connect_timeout, connect_async(url.as_str())).await.map_err(|_| MediaPlaneError::invalid("ElevenLabs connection timed out"))?.map_err(|_| MediaPlaneError::invalid("ElevenLabs connection failed"))?;
            for payload in [json!({"text": " ", "xi_api_key": config.api_key}), json!({"text": text, "flush": flush}), json!({"text": ""})] { timeout(config.idle_timeout, socket.send(Message::Text(payload.to_string().into()))).await.map_err(|_| MediaPlaneError::invalid("ElevenLabs write timed out"))?.map_err(|_| MediaPlaneError::invalid("ElevenLabs write failed"))?; }
            loop {
                tokio::select! {
                    _ = &mut cancel_rx => { let _ = socket.close(None).await; return Ok(()); }
                    incoming = timeout(config.idle_timeout, socket.next()) => {
                        let message = incoming.map_err(|_| MediaPlaneError::invalid("ElevenLabs read timed out"))?.ok_or_else(|| MediaPlaneError::invalid("ElevenLabs closed before final audio"))?.map_err(|_| MediaPlaneError::invalid("ElevenLabs read failed"))?;
                        let Message::Text(text_frame) = message else { return Err(MediaPlaneError::invalid("ElevenLabs returned non-JSON frame")); };
                        let payload: Value = serde_json::from_str(text_frame.as_ref()).map_err(|_| MediaPlaneError::invalid("ElevenLabs returned malformed JSON"))?;
                        if let Some(audio) = elevenlabs_audio(&payload, &config.output_format)? {
                            output_tx.send(Ok(Some(audio))).await.map_err(|_| MediaPlaneError::invalid("ElevenLabs playback receiver closed"))?;
                        }
                        if payload.get("isFinal").is_some_and(|value| value == true) { return Ok(()); }
                    }
                }
            }
        }.await;
        let _ = match result {
            Ok(()) => output_tx.send(Ok(None)).await,
            Err(error) => output_tx.send(Err(error)).await,
        };
    });
}

fn deepgram_transcript(payload: &Value) -> Result<String, MediaPlaneError> {
    payload
        .pointer("/channel/alternatives/0/transcript")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| MediaPlaneError::invalid("Deepgram result schema is invalid"))
}

fn elevenlabs_audio(
    payload: &Value,
    output_format: &str,
) -> Result<Option<Vec<u8>>, MediaPlaneError> {
    let Some(audio) = payload.get("audio") else {
        return Ok(None);
    };
    if audio.is_null() {
        return Ok(None);
    }
    let encoded = if let Some(encoded) = audio.as_str() {
        encoded
    } else {
        let object = audio
            .as_object()
            .ok_or_else(|| MediaPlaneError::invalid("ElevenLabs audio shape is invalid"))?;
        let codec = object.get("codec").and_then(Value::as_str).ok_or_else(|| {
            MediaPlaneError::invalid("ElevenLabs recorded audio codec is missing")
        })?;
        if codec != output_format {
            return Err(MediaPlaneError::invalid(
                "ElevenLabs audio codec does not match configured output",
            ));
        }
        object.get("b64").and_then(Value::as_str).ok_or_else(|| {
            MediaPlaneError::invalid("ElevenLabs recorded audio payload is missing")
        })?
    };
    if encoded.is_empty() {
        return Ok(None);
    }
    STANDARD
        .decode(encoded)
        .map(Some)
        .map_err(|_| MediaPlaneError::invalid("ElevenLabs returned invalid audio"))
}

pub fn resample_pcm_s16le(
    input: &[u8],
    from_hz: u32,
    to_hz: u32,
) -> Result<Vec<u8>, MediaPlaneError> {
    if input.len() % 2 != 0
        || !matches!(from_hz, 8_000 | 16_000)
        || !matches!(to_hz, 8_000 | 16_000)
    {
        return Err(MediaPlaneError::invalid(
            "PCM must be complete samples at 8 kHz or 16 kHz",
        ));
    }
    if from_hz == to_hz {
        return Ok(input.to_vec());
    }
    let samples = input
        .chunks_exact(2)
        .map(|bytes| i16::from_le_bytes([bytes[0], bytes[1]]))
        .collect::<Vec<_>>();
    let output = if from_hz < to_hz {
        samples
            .into_iter()
            .flat_map(|sample| [sample, sample])
            .collect::<Vec<_>>()
    } else {
        samples
            .chunks(2)
            .map(|pair| {
                if pair.len() == 1 {
                    pair[0]
                } else {
                    ((i32::from(pair[0]) + i32::from(pair[1])) / 2) as i16
                }
            })
            .collect::<Vec<_>>()
    };
    Ok(output.into_iter().flat_map(i16::to_le_bytes).collect())
}
