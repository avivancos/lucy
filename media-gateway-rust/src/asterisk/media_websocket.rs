use crate::control::schema::ControlMessage;
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::fmt;
use tokio_tungstenite::tungstenite::Message;

use super::media_plane::MediaPlaneEvent;
use super::metrics::TransportMetricSample;
use super::{CONTROL_SCHEMA_VERSION, UNKNOWN_CALLER, VALID_DTMF};

const TRANSPORT_NAME: &str = "asterisk/media_websocket";
const EVENT_MEDIA_START: &str = "MEDIA_START";
const EVENT_DTMF_END: &str = "DTMF_END";
const EVENT_MEDIA_XOFF: &str = "MEDIA_XOFF";
const EVENT_MEDIA_XON: &str = "MEDIA_XON";
const EVENT_MEDIA_MARK_PROCESSED: &str = "MEDIA_MARK_PROCESSED";
const COMMAND_FLUSH_MEDIA: &str = "FLUSH_MEDIA";
const COMMAND_MARK_MEDIA: &str = "MARK_MEDIA";
const COMMAND_HANGUP: &str = "HANGUP";
const MEDIA_WEBSOCKET_CLOSED_REASON: &str = "media_websocket_closed";
const FEATURE_DTMF: &str = "dtmf";
const FEATURE_FLOW_CONTROL: &str = "flow_control";
const FEATURE_MEDIA_MARKS: &str = "media_marks";
const FIXTURE_PCM16_CODEC: &str = "slin";
pub(crate) const SLIN_SAMPLE_RATE_HZ: u32 = 8_000;
const MAX_CONTROL_FIELD_CHARS: usize = 512;
const MAX_PENDING_MEDIA_MARKS: usize = 1_024;

pub const MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES: usize = 65_500;
pub const MAX_MEDIA_WEBSOCKET_CONTROL_BYTES: usize = 65_500;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaWebSocketFrame {
    pub session_id: String,
    pub codec: String,
    pub sample_rate_hz: u32,
    pub bytes: Vec<u8>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MediaWebSocketSignal {
    Pause,
    Resume,
}

#[derive(Debug, Clone)]
pub enum MediaWebSocketOutput {
    Control(ControlMessage),
    Media(MediaWebSocketFrame),
    FlowControl(MediaWebSocketSignal),
    ReplyPong(Vec<u8>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaWebSocketError(String);

impl MediaWebSocketError {
    fn protocol(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for MediaWebSocketError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for MediaWebSocketError {}

#[derive(Debug, Clone)]
struct SessionInfo {
    channel_id: String,
    codec: String,
    sample_rate_hz: u32,
}

#[derive(Debug, Clone)]
struct PlaybackMark {
    utterance_id: String,
    mark_chars: u64,
}

#[derive(Debug, Default)]
pub struct MediaWebSocketAdapter {
    session: Option<SessionInfo>,
    seq: u64,
    next_mark: u64,
    pending_marks: BTreeMap<String, PlaybackMark>,
    media_paused: bool,
    ended: bool,
    next_turn: u64,
    active_turn_id: Option<String>,
    latest_turn_id: Option<String>,
}

impl MediaWebSocketAdapter {
    pub fn handle_message(
        &mut self,
        message: Message,
        ts_ms: u64,
    ) -> Result<Option<MediaWebSocketOutput>, MediaWebSocketError> {
        if self.ended {
            return Err(MediaWebSocketError::protocol(
                "media WebSocket message received after close",
            ));
        }
        match message {
            Message::Binary(bytes) => self.media(bytes.to_vec()).map(Some),
            Message::Text(text) => {
                if text.len() > MAX_MEDIA_WEBSOCKET_CONTROL_BYTES {
                    return Err(MediaWebSocketError::protocol(
                        "media WebSocket control exceeds Asterisk maximum",
                    ));
                }
                self.text(text.as_ref(), ts_ms).map(Some)
            }
            Message::Close(_) => self.close(ts_ms).map(Some),
            Message::Ping(payload) => Ok(Some(MediaWebSocketOutput::ReplyPong(payload.to_vec()))),
            Message::Pong(_) | Message::Frame(_) => Ok(None),
        }
    }

    pub fn cancel_command() -> Message {
        Message::Text(json!({ "command": COMMAND_FLUSH_MEDIA }).to_string().into())
    }

    pub fn hangup_command() -> Message {
        Message::Text(json!({ "command": COMMAND_HANGUP }).to_string().into())
    }

    pub fn mark_command(
        &mut self,
        utterance_id: &str,
        mark_chars: u64,
    ) -> Result<Message, MediaWebSocketError> {
        self.session()?;
        if utterance_id.trim().is_empty() {
            return Err(MediaWebSocketError::protocol(
                "media mark utterance_id cannot be blank",
            ));
        }
        if self.pending_marks.len() >= MAX_PENDING_MEDIA_MARKS {
            return Err(MediaWebSocketError::protocol(
                "media WebSocket pending media-mark limit reached",
            ));
        }
        self.next_mark += 1;
        let correlation_id = format!("lucy-mark-{}", self.next_mark);
        self.pending_marks.insert(
            correlation_id.clone(),
            PlaybackMark {
                utterance_id: utterance_id.to_string(),
                mark_chars,
            },
        );
        Ok(Message::Text(
            json!({
                "command": COMMAND_MARK_MEDIA,
                "correlation_id": correlation_id
            })
            .to_string()
            .into(),
        ))
    }

    pub fn media_command(&self, bytes: Vec<u8>) -> Result<Message, MediaWebSocketError> {
        self.session()?;
        validate_media_length(bytes.len())?;
        if self.media_paused {
            return Err(MediaWebSocketError::protocol(
                "media WebSocket output is paused by MEDIA_XOFF",
            ));
        }
        Ok(Message::Binary(bytes.into()))
    }

    pub fn media_plane_event(
        &mut self,
        event: MediaPlaneEvent,
    ) -> Result<ControlMessage, MediaWebSocketError> {
        let session_id = self.session()?.channel_id.clone();
        let value = match event {
            MediaPlaneEvent::SpeechStarted { at_ms } => {
                if self.active_turn_id.is_some() {
                    return Err(MediaWebSocketError::protocol(
                        "media-plane speech started while a turn is active",
                    ));
                }
                self.next_turn += 1;
                let turn_id = format!("turn-{}", self.next_turn);
                self.active_turn_id = Some(turn_id.clone());
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"vad.speech_start", "session_id":session_id, "turn_id":turn_id, "seq":self.next_seq(), "ts_ms":at_ms, "at_ms":at_ms})
            }
            MediaPlaneEvent::TranscriptPartial {
                text,
                stability,
                at_ms,
                provider,
            } => {
                let turn_id = self.active_turn()?;
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"stt.partial", "session_id":session_id, "turn_id":turn_id, "seq":self.next_seq(), "ts_ms":at_ms, "text":text, "stability":stability, "provider":provider})
            }
            MediaPlaneEvent::SpeechEnded { at_ms, speech_ms } => {
                let turn_id = self.active_turn()?;
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"vad.speech_end", "session_id":session_id, "turn_id":turn_id, "seq":self.next_seq(), "ts_ms":at_ms, "at_ms":at_ms, "speech_ms":speech_ms})
            }
            MediaPlaneEvent::TranscriptFinal {
                text,
                stt_ms,
                at_ms,
                provider,
            } => {
                let turn_id = self.active_turn()?;
                self.latest_turn_id = Some(turn_id.clone());
                self.active_turn_id = None;
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"stt.final", "session_id":session_id, "turn_id":turn_id, "seq":self.next_seq(), "ts_ms":at_ms.saturating_add(stt_ms), "text":text, "provider":provider, "stt_ms":stt_ms})
            }
            MediaPlaneEvent::PlaybackStarted { utterance_id } => {
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"tts.playback", "session_id":session_id, "seq":self.next_seq(), "ts_ms":0, "utterance_id":utterance_id, "state":"started", "mark_chars":0})
            }
            MediaPlaneEvent::PlaybackFinished {
                utterance_id,
                mark_chars,
            } => {
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"tts.playback", "session_id":session_id, "seq":self.next_seq(), "ts_ms":0, "utterance_id":utterance_id, "state":"finished", "mark_chars":mark_chars})
            }
            MediaPlaneEvent::PlaybackFlushed {
                utterance_id,
                mark_chars,
            } => {
                json!({"v": CONTROL_SCHEMA_VERSION, "type":"tts.playback", "session_id":session_id, "seq":self.next_seq(), "ts_ms":0, "utterance_id":utterance_id, "state":"flushed", "mark_chars":mark_chars})
            }
        };
        serde_json::from_value(value).map_err(|error| {
            MediaWebSocketError::protocol(format!(
                "media-plane control-schema conversion failed: {error}"
            ))
        })
    }

    pub fn transport_metrics(
        &mut self,
        sample: TransportMetricSample,
        ts_ms: u64,
    ) -> Result<Option<ControlMessage>, MediaWebSocketError> {
        let Some(turn_id) = self.latest_turn_id.clone() else {
            return Ok(None);
        };
        let session_id = self.session()?.channel_id.clone();
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "transport.metrics",
            "session_id": session_id,
            "turn_id": turn_id,
            "seq": self.next_seq(),
            "ts_ms": ts_ms,
            "jitter_ms": sample.jitter_ms,
            "rtt_ms": sample.rtt_ms,
            "packet_loss": sample.packet_loss
        }))
        .map(|output| match output {
            MediaWebSocketOutput::Control(message) => Some(message),
            _ => unreachable!("transport metrics are control messages"),
        })
    }

    fn active_turn(&self) -> Result<String, MediaWebSocketError> {
        self.active_turn_id
            .clone()
            .ok_or_else(|| MediaWebSocketError::protocol("media-plane event has no active turn"))
    }

    fn text(
        &mut self,
        text: &str,
        ts_ms: u64,
    ) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        let value: Value = serde_json::from_str(text).map_err(|_| {
            MediaWebSocketError::protocol("media WebSocket control must be a valid JSON object")
        })?;
        let object = value.as_object().ok_or_else(|| {
            MediaWebSocketError::protocol("media WebSocket control must be a valid JSON object")
        })?;
        let event = string_field(object, "event")?;
        match event {
            EVENT_MEDIA_START => self.start(object, ts_ms),
            EVENT_DTMF_END => self.dtmf(object, ts_ms),
            EVENT_MEDIA_XOFF => self.flow(object, MediaWebSocketSignal::Pause),
            EVENT_MEDIA_XON => self.flow(object, MediaWebSocketSignal::Resume),
            EVENT_MEDIA_MARK_PROCESSED => self.mark_processed(object, ts_ms),
            other => Err(MediaWebSocketError::protocol(format!(
                "unsupported media WebSocket event {other}"
            ))),
        }
    }

    fn start(
        &mut self,
        object: &Map<String, Value>,
        ts_ms: u64,
    ) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        if self.session.is_some() {
            return Err(MediaWebSocketError::protocol(
                "MEDIA_START may only arrive once",
            ));
        }
        string_field(object, "connection_id")?;
        string_field(object, "channel")?;
        let channel_id = string_field(object, "channel_id")?.to_string();
        let codec = string_field(object, "format")?.to_string();
        let sample_rate_hz = codec_sample_rate(&codec)?;
        let optimal_frame_size = unsigned_field(object, "optimal_frame_size")?;
        if optimal_frame_size == 0 || optimal_frame_size > MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES as u64
        {
            return Err(MediaWebSocketError::protocol(
                "MEDIA_START optimal_frame_size is invalid",
            ));
        }
        if unsigned_field(object, "ptime")? == 0 {
            return Err(MediaWebSocketError::protocol(
                "MEDIA_START ptime is invalid",
            ));
        }
        self.session = Some(SessionInfo {
            channel_id: channel_id.clone(),
            codec: codec.clone(),
            sample_rate_hz,
        });
        let seq = self.next_seq();
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.started",
            "session_id": channel_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "transport": TRANSPORT_NAME,
            "caller": UNKNOWN_CALLER,
            "codecs": [codec],
            "features": [FEATURE_DTMF, FEATURE_FLOW_CONTROL, FEATURE_MEDIA_MARKS]
        }))
    }

    fn media(&self, bytes: Vec<u8>) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        let session = self.session()?;
        validate_media_length(bytes.len())?;
        Ok(MediaWebSocketOutput::Media(MediaWebSocketFrame {
            session_id: session.channel_id.clone(),
            codec: session.codec.clone(),
            sample_rate_hz: session.sample_rate_hz,
            bytes,
        }))
    }

    fn dtmf(
        &mut self,
        object: &Map<String, Value>,
        ts_ms: u64,
    ) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        let channel_id = self.checked_channel(object)?.to_string();
        let digit = string_field(object, "digit")?;
        let bytes = digit.as_bytes();
        if bytes.len() != 1 || !VALID_DTMF.contains(&bytes[0]) {
            return Err(MediaWebSocketError::protocol(
                "DTMF_END digit must be one valid digit",
            ));
        }
        let digit = char::from(bytes[0]).to_ascii_uppercase().to_string();
        let seq = self.next_seq();
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "dtmf",
            "session_id": channel_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "digit": digit
        }))
    }

    fn flow(
        &mut self,
        object: &Map<String, Value>,
        signal: MediaWebSocketSignal,
    ) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        self.checked_channel(object)?;
        self.media_paused = signal == MediaWebSocketSignal::Pause;
        Ok(MediaWebSocketOutput::FlowControl(signal))
    }

    fn mark_processed(
        &mut self,
        object: &Map<String, Value>,
        ts_ms: u64,
    ) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        let channel_id = self.checked_channel(object)?.to_string();
        let correlation_id = string_field(object, "correlation_id")?;
        let mark = self.pending_marks.remove(correlation_id).ok_or_else(|| {
            MediaWebSocketError::protocol("MEDIA_MARK_PROCESSED references unknown media mark")
        })?;
        let seq = self.next_seq();
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "tts.playback",
            "session_id": channel_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "utterance_id": mark.utterance_id,
            "state": "mark",
            "mark_chars": mark.mark_chars
        }))
    }

    fn close(&mut self, ts_ms: u64) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
        let channel_id = self.session()?.channel_id.clone();
        let seq = self.next_seq();
        let output = control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.ended",
            "session_id": channel_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "reason": MEDIA_WEBSOCKET_CLOSED_REASON
        }))?;
        self.ended = true;
        Ok(output)
    }

    fn session(&self) -> Result<&SessionInfo, MediaWebSocketError> {
        self.session
            .as_ref()
            .ok_or_else(|| MediaWebSocketError::protocol("MEDIA_START must arrive first"))
    }

    fn checked_channel<'a>(
        &self,
        object: &'a Map<String, Value>,
    ) -> Result<&'a str, MediaWebSocketError> {
        let channel_id = string_field(object, "channel_id")?;
        if channel_id != self.session()?.channel_id {
            return Err(MediaWebSocketError::protocol(
                "media WebSocket channel_id does not match active session",
            ));
        }
        Ok(channel_id)
    }

    fn next_seq(&mut self) -> u64 {
        self.seq += 1;
        self.seq
    }
}

fn string_field<'a>(
    object: &'a Map<String, Value>,
    field: &str,
) -> Result<&'a str, MediaWebSocketError> {
    object
        .get(field)
        .and_then(Value::as_str)
        .filter(|value| {
            !value.trim().is_empty()
                && value.chars().count() <= MAX_CONTROL_FIELD_CHARS
                && !value.chars().any(char::is_control)
        })
        .ok_or_else(|| {
            MediaWebSocketError::protocol(format!(
                "media WebSocket field {field} must be a non-empty string"
            ))
        })
}

fn unsigned_field(object: &Map<String, Value>, field: &str) -> Result<u64, MediaWebSocketError> {
    object.get(field).and_then(Value::as_u64).ok_or_else(|| {
        MediaWebSocketError::protocol(format!(
            "media WebSocket field {field} must be an unsigned integer"
        ))
    })
}

fn codec_sample_rate(codec: &str) -> Result<u32, MediaWebSocketError> {
    (codec == FIXTURE_PCM16_CODEC)
        .then_some(SLIN_SAMPLE_RATE_HZ)
        .ok_or_else(|| {
            MediaWebSocketError::protocol(format!("unsupported media WebSocket codec {codec}"))
        })
}

fn validate_media_length(length: usize) -> Result<(), MediaWebSocketError> {
    if length == 0 {
        return Err(MediaWebSocketError::protocol(
            "media WebSocket frame cannot be empty",
        ));
    }
    if length > MAX_MEDIA_WEBSOCKET_MESSAGE_BYTES {
        return Err(MediaWebSocketError::protocol(
            "media WebSocket frame exceeds Asterisk maximum",
        ));
    }
    if length % std::mem::size_of::<i16>() != 0 {
        return Err(MediaWebSocketError::protocol(
            "media WebSocket frame must contain complete PCM16 samples",
        ));
    }
    Ok(())
}

fn control(value: Value) -> Result<MediaWebSocketOutput, MediaWebSocketError> {
    serde_json::from_value(value)
        .map(MediaWebSocketOutput::Control)
        .map_err(|error| {
            MediaWebSocketError::protocol(format!(
                "media WebSocket control-schema conversion failed: {error}"
            ))
        })
}
