use crate::asterisk::directives::CONTROL_SCHEMA_VERSION;
use crate::asterisk::media_plane::MediaPlaneEvent;
use crate::control::schema::ControlMessage;
use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fmt;
use tokio_tungstenite::tungstenite::Message;

const REDACTED_CALLER: &str = "[redacted]";
const MAX_CONTROL_FIELD_CHARS: usize = 512;
const MAX_REORDERED_MEDIA_CHUNKS: usize = 32;
const MAX_PENDING_MARKS: usize = 1_024;
const TWILIO_REGISTRY_KEY: &str = "twilio";
const TELNYX_REGISTRY_KEY: &str = "telnyx";
const TWILIO_MULAW_CODEC: &str = "audio/x-mulaw";
const TELNYX_PCMU_CODEC: &str = "PCMU";
const TELNYX_L16_CODEC: &str = "L16";

pub const MAX_CPAAAS_MESSAGE_BYTES: usize = 65_536;

pub mod call_control;
pub mod runtime;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CpaasProvider {
    Telnyx,
    Twilio,
}

impl CpaasProvider {
    pub fn registry_key(self) -> &'static str {
        match self {
            Self::Telnyx => TELNYX_REGISTRY_KEY,
            Self::Twilio => TWILIO_REGISTRY_KEY,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpaasMediaFrame {
    pub session_id: String,
    pub sample_rate_hz: u32,
    pub pcm_s16le: Vec<u8>,
    pub provider_timestamp_ms: u64,
}

#[derive(Debug, Clone)]
pub enum CpaasOutput {
    Control(ControlMessage),
    Media(CpaasMediaFrame),
    ReplyPong(Vec<u8>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpaasError(String);

impl CpaasError {
    fn protocol(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for CpaasError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for CpaasError {}

#[derive(Debug, Clone)]
struct StreamState {
    id: String,
    call_id: String,
    codec: &'static str,
    sample_rate_hz: u32,
}

#[derive(Debug, Clone)]
struct PendingMark {
    utterance_id: String,
    mark_chars: u64,
}

#[derive(Debug, Clone)]
struct PendingMedia {
    timestamp_ms: u64,
    payload: Vec<u8>,
}

pub struct CpaasMediaStreamAdapter {
    provider: CpaasProvider,
    connected: bool,
    stream: Option<StreamState>,
    ended: bool,
    seq: u64,
    next_turn: u64,
    active_turn_id: Option<String>,
    latest_turn_id: Option<String>,
    next_mark: u64,
    pending_marks: BTreeMap<String, PendingMark>,
    next_media_chunk: u64,
    pending_media: BTreeMap<u64, PendingMedia>,
}

impl CpaasMediaStreamAdapter {
    pub fn new(provider: CpaasProvider) -> Self {
        Self {
            provider,
            connected: false,
            stream: None,
            ended: false,
            seq: 0,
            next_turn: 0,
            active_turn_id: None,
            latest_turn_id: None,
            next_mark: 0,
            pending_marks: BTreeMap::new(),
            next_media_chunk: 1,
            pending_media: BTreeMap::new(),
        }
    }

    pub fn provider(&self) -> CpaasProvider {
        self.provider
    }

    pub fn session_id(&self) -> Option<&str> {
        self.stream.as_ref().map(|stream| stream.id.as_str())
    }

    pub fn sample_rate_hz(&self) -> Option<u32> {
        self.stream.as_ref().map(|stream| stream.sample_rate_hz)
    }

    pub fn call_id(&self) -> Option<&str> {
        self.stream.as_ref().map(|stream| stream.call_id.as_str())
    }

    pub fn handle_message(
        &mut self,
        message: Message,
        ts_ms: u64,
    ) -> Result<Vec<CpaasOutput>, CpaasError> {
        if self.ended {
            return Err(CpaasError::protocol(
                "CPaaS message received after the stream ended",
            ));
        }
        match message {
            Message::Text(text) => {
                if text.len() > MAX_CPAAAS_MESSAGE_BYTES {
                    return Err(CpaasError::protocol(
                        "CPaaS message exceeds the configured maximum",
                    ));
                }
                self.handle_text(text.as_ref(), ts_ms)
            }
            Message::Ping(payload) => Ok(vec![CpaasOutput::ReplyPong(payload.to_vec())]),
            Message::Pong(_) | Message::Frame(_) => Ok(Vec::new()),
            Message::Close(_) => self
                .end("cpaas_websocket_closed", ts_ms)
                .map(|message| vec![CpaasOutput::Control(message)]),
            Message::Binary(_) => Err(CpaasError::protocol(
                "CPaaS media streams require JSON text frames",
            )),
        }
    }

    pub fn playback_message(&self, pcm_s16le: &[u8]) -> Result<Message, CpaasError> {
        let stream = self.stream()?;
        if pcm_s16le.is_empty() || pcm_s16le.len() % 2 != 0 {
            return Err(CpaasError::protocol(
                "playback PCM16 payload must contain complete samples",
            ));
        }
        let encoded: Vec<u8> = match stream.codec {
            TWILIO_MULAW_CODEC | TELNYX_PCMU_CODEC => pcm_s16le
                .chunks_exact(2)
                .map(|sample| mulaw_encode(i16::from_le_bytes([sample[0], sample[1]])))
                .collect(),
            TELNYX_L16_CODEC => pcm_s16le
                .chunks_exact(2)
                .flat_map(|sample| [sample[1], sample[0]])
                .collect(),
            _ => return Err(CpaasError::protocol("unsupported CPaaS playback codec")),
        };
        let mut value = json!({
            "event": "media",
            "media": {"payload": BASE64.encode(encoded)}
        });
        if self.provider == CpaasProvider::Twilio {
            value["streamSid"] = stream.id.clone().into();
        }
        text_message(value)
    }

    pub fn clear_message(&self) -> Result<Message, CpaasError> {
        let stream = self.stream()?;
        let mut value = json!({"event": "clear"});
        if self.provider == CpaasProvider::Twilio {
            value["streamSid"] = stream.id.clone().into();
        }
        text_message(value)
    }

    pub fn mark_message(
        &mut self,
        utterance_id: &str,
        mark_chars: u64,
    ) -> Result<Message, CpaasError> {
        let stream_id = self.stream()?.id.clone();
        validate_text(utterance_id, "utterance_id")?;
        if self.pending_marks.len() >= MAX_PENDING_MARKS {
            return Err(CpaasError::protocol("CPaaS pending mark limit reached"));
        }
        self.next_mark += 1;
        let name = format!("lucy-mark-{}", self.next_mark);
        self.pending_marks.insert(
            name.clone(),
            PendingMark {
                utterance_id: utterance_id.to_string(),
                mark_chars,
            },
        );
        let mut value = json!({"event":"mark","mark":{"name":name}});
        if self.provider == CpaasProvider::Twilio {
            value["streamSid"] = stream_id.into();
        }
        text_message(value)
    }

    pub fn hangup_message(&self) -> Result<Message, CpaasError> {
        self.stream()?;
        Ok(Message::Close(None))
    }

    pub fn media_plane_event(
        &mut self,
        event: MediaPlaneEvent,
    ) -> Result<ControlMessage, CpaasError> {
        let session_id = self.stream()?.id.clone();
        let value = match event {
            MediaPlaneEvent::SpeechStarted { at_ms } => {
                if self.active_turn_id.is_some() {
                    return Err(CpaasError::protocol(
                        "CPaaS speech started while a turn is active",
                    ));
                }
                self.next_turn += 1;
                let turn_id = format!("turn-{}", self.next_turn);
                self.active_turn_id = Some(turn_id.clone());
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"vad.speech_start","session_id":session_id,"turn_id":turn_id,"seq":self.next_seq(),"ts_ms":at_ms,"at_ms":at_ms})
            }
            MediaPlaneEvent::TranscriptPartial {
                text,
                stability,
                at_ms,
                provider,
            } => {
                let turn_id = self.active_turn()?;
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"stt.partial","session_id":session_id,"turn_id":turn_id,"seq":self.next_seq(),"ts_ms":at_ms,"text":text,"stability":stability,"provider":provider})
            }
            MediaPlaneEvent::SpeechEnded { at_ms, speech_ms } => {
                let turn_id = self.active_turn()?;
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"vad.speech_end","session_id":session_id,"turn_id":turn_id,"seq":self.next_seq(),"ts_ms":at_ms,"at_ms":at_ms,"speech_ms":speech_ms})
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
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"stt.final","session_id":session_id,"turn_id":turn_id,"seq":self.next_seq(),"ts_ms":at_ms.saturating_add(stt_ms),"text":text,"provider":provider,"stt_ms":stt_ms})
            }
            MediaPlaneEvent::PlaybackStarted { utterance_id } => {
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"tts.playback","session_id":session_id,"seq":self.next_seq(),"ts_ms":0,"utterance_id":utterance_id,"state":"started","mark_chars":0})
            }
            MediaPlaneEvent::PlaybackFinished {
                utterance_id,
                mark_chars,
            } => {
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"tts.playback","session_id":session_id,"seq":self.next_seq(),"ts_ms":0,"utterance_id":utterance_id,"state":"finished","mark_chars":mark_chars})
            }
            MediaPlaneEvent::PlaybackFlushed {
                utterance_id,
                mark_chars,
            } => {
                json!({"v":CONTROL_SCHEMA_VERSION,"type":"tts.playback","session_id":session_id,"seq":self.next_seq(),"ts_ms":0,"utterance_id":utterance_id,"state":"flushed","mark_chars":mark_chars})
            }
        };
        control(value)
    }

    fn handle_text(&mut self, text: &str, ts_ms: u64) -> Result<Vec<CpaasOutput>, CpaasError> {
        let value: Value = serde_json::from_str(text)
            .map_err(|_| CpaasError::protocol("CPaaS frame must be valid JSON"))?;
        let object = value
            .as_object()
            .ok_or_else(|| CpaasError::protocol("CPaaS frame must be a JSON object"))?;
        let event = required_text(object.get("event"), "event")?;
        match event {
            "connected" => self.connected(&value),
            "start" => self.start(&value, ts_ms),
            "media" => self.media(&value),
            "dtmf" => self.dtmf(&value, ts_ms),
            "mark" => self.mark(&value, ts_ms),
            "stop" => self
                .end("cpaas_stream_stopped", ts_ms)
                .map(|message| vec![CpaasOutput::Control(message)]),
            "error" => Err(CpaasError::protocol("CPaaS provider reported an error")),
            _ => Err(CpaasError::protocol("unsupported CPaaS stream event")),
        }
    }

    fn connected(&mut self, value: &Value) -> Result<Vec<CpaasOutput>, CpaasError> {
        if self.connected || self.stream.is_some() {
            return Err(CpaasError::protocol("duplicate CPaaS connected event"));
        }
        let version = required_path_text(value, &["version"], "version")?;
        if version != "1.0.0" {
            return Err(CpaasError::protocol(
                "unsupported CPaaS media-stream version",
            ));
        }
        if self.provider == CpaasProvider::Twilio
            && required_path_text(value, &["protocol"], "protocol")? != "Call"
        {
            return Err(CpaasError::protocol("unsupported Twilio stream protocol"));
        }
        self.connected = true;
        Ok(Vec::new())
    }

    fn start(&mut self, value: &Value, ts_ms: u64) -> Result<Vec<CpaasOutput>, CpaasError> {
        if !self.connected {
            return Err(CpaasError::protocol(
                "CPaaS connected event must arrive before start",
            ));
        }
        if self.stream.is_some() {
            return Err(CpaasError::protocol("duplicate CPaaS start event"));
        }
        let (stream_id, call_id, encoding, sample_rate_hz, channels) = match self.provider {
            CpaasProvider::Twilio => (
                required_path_text(value, &["streamSid"], "streamSid")?,
                required_path_text(value, &["start", "callSid"], "callSid")?,
                required_path_text(value, &["start", "mediaFormat", "encoding"], "encoding")?,
                required_path_u64(value, &["start", "mediaFormat", "sampleRate"], "sampleRate")?,
                required_path_u64(value, &["start", "mediaFormat", "channels"], "channels")?,
            ),
            CpaasProvider::Telnyx => (
                required_path_text(value, &["stream_id"], "stream_id")?,
                required_path_text(value, &["start", "call_control_id"], "call_control_id")?,
                required_path_text(value, &["start", "media_format", "encoding"], "encoding")?,
                required_path_u64(
                    value,
                    &["start", "media_format", "sample_rate"],
                    "sample_rate",
                )?,
                required_path_u64(value, &["start", "media_format", "channels"], "channels")?,
            ),
        };
        validate_text(stream_id, "stream id")?;
        if channels != 1 {
            return Err(CpaasError::protocol("CPaaS stream must be mono"));
        }
        let codec = match (self.provider, encoding, sample_rate_hz) {
            (CpaasProvider::Twilio, TWILIO_MULAW_CODEC, 8_000) => TWILIO_MULAW_CODEC,
            (CpaasProvider::Telnyx, TELNYX_PCMU_CODEC, 8_000) => TELNYX_PCMU_CODEC,
            (CpaasProvider::Telnyx, TELNYX_L16_CODEC, 16_000) => TELNYX_L16_CODEC,
            _ => return Err(CpaasError::protocol("unsupported CPaaS media format")),
        };
        self.stream = Some(StreamState {
            id: stream_id.to_string(),
            call_id: call_id.to_string(),
            codec,
            sample_rate_hz: sample_rate_hz as u32,
        });
        let message = control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.started",
            "session_id": stream_id,
            "seq": self.next_seq(),
            "ts_ms": ts_ms,
            "caller": REDACTED_CALLER,
            "transport": format!("cpaas/{}", self.provider.registry_key()),
            "codecs": [format!("{codec}/{sample_rate_hz}")],
            "features": ["bidirectional", "dtmf", "barge_in"]
        }))?;
        Ok(vec![CpaasOutput::Control(message)])
    }

    fn media(&mut self, value: &Value) -> Result<Vec<CpaasOutput>, CpaasError> {
        let stream = self.stream()?.clone();
        self.validate_stream_id(value, &stream.id)?;
        let track = required_path_text(value, &["media", "track"], "media.track")?;
        if matches!(track, "outbound" | "outbound_track") {
            return Ok(Vec::new());
        }
        if !matches!(track, "inbound" | "inbound_track" | "inbound/outbound") {
            return Err(CpaasError::protocol("unsupported CPaaS media track"));
        }
        let chunk = parse_decimal(
            required_path_text(value, &["media", "chunk"], "media.chunk")?,
            "media.chunk",
        )?;
        let timestamp_ms = parse_decimal(
            required_path_text(value, &["media", "timestamp"], "media.timestamp")?,
            "media.timestamp",
        )?;
        if self.pending_media.contains_key(&chunk) {
            return Err(CpaasError::protocol("duplicate CPaaS media chunk"));
        }
        if chunk < self.next_media_chunk {
            return Err(CpaasError::protocol("duplicate CPaaS media chunk"));
        }
        if chunk - self.next_media_chunk >= MAX_REORDERED_MEDIA_CHUNKS as u64
            || self.pending_media.len() >= MAX_REORDERED_MEDIA_CHUNKS
        {
            return Err(CpaasError::protocol("CPaaS media reorder window exceeded"));
        }
        let payload = BASE64
            .decode(required_path_text(
                value,
                &["media", "payload"],
                "media.payload",
            )?)
            .map_err(|_| CpaasError::protocol("CPaaS media payload is not valid base64"))?;
        if payload.is_empty() || payload.len() > MAX_CPAAAS_MESSAGE_BYTES {
            return Err(CpaasError::protocol("CPaaS media payload size is invalid"));
        }
        self.pending_media.insert(
            chunk,
            PendingMedia {
                timestamp_ms,
                payload,
            },
        );
        let mut outputs = Vec::new();
        while let Some(pending) = self.pending_media.remove(&self.next_media_chunk) {
            self.next_media_chunk += 1;
            outputs.push(CpaasOutput::Media(CpaasMediaFrame {
                session_id: stream.id.clone(),
                sample_rate_hz: stream.sample_rate_hz,
                pcm_s16le: decode_provider_audio(stream.codec, &pending.payload)?,
                provider_timestamp_ms: pending.timestamp_ms,
            }));
        }
        Ok(outputs)
    }

    fn dtmf(&mut self, value: &Value, ts_ms: u64) -> Result<Vec<CpaasOutput>, CpaasError> {
        let stream_id = self.stream()?.id.clone();
        self.validate_stream_id(value, &stream_id)?;
        let digit = required_path_text(value, &["dtmf", "digit"], "dtmf.digit")?;
        if digit.len() != 1 || !b"0123456789*#ABCDabcd".contains(&digit.as_bytes()[0]) {
            return Err(CpaasError::protocol("CPaaS DTMF digit is invalid"));
        }
        Ok(vec![CpaasOutput::Control(control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "dtmf",
            "session_id": stream_id,
            "seq": self.next_seq(),
            "ts_ms": ts_ms,
            "digit": digit.to_ascii_uppercase()
        }))?)])
    }

    fn mark(&mut self, value: &Value, ts_ms: u64) -> Result<Vec<CpaasOutput>, CpaasError> {
        let stream_id = self.stream()?.id.clone();
        self.validate_stream_id(value, &stream_id)?;
        let name = required_path_text(value, &["mark", "name"], "mark.name")?;
        let pending = self
            .pending_marks
            .remove(name)
            .ok_or_else(|| CpaasError::protocol("unknown CPaaS playback mark"))?;
        Ok(vec![CpaasOutput::Control(control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "tts.playback",
            "session_id": stream_id,
            "seq": self.next_seq(),
            "ts_ms": ts_ms,
            "utterance_id": pending.utterance_id,
            "state": "mark",
            "mark_chars": pending.mark_chars
        }))?)])
    }

    fn end(&mut self, reason: &str, ts_ms: u64) -> Result<ControlMessage, CpaasError> {
        let stream_id = self.stream()?.id.clone();
        self.ended = true;
        control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.ended",
            "session_id": stream_id,
            "seq": self.next_seq(),
            "ts_ms": ts_ms,
            "reason": reason
        }))
    }

    fn validate_stream_id(&self, value: &Value, expected: &str) -> Result<(), CpaasError> {
        let key = match self.provider {
            CpaasProvider::Twilio => "streamSid",
            CpaasProvider::Telnyx => "stream_id",
        };
        if required_path_text(value, &[key], key)? != expected {
            return Err(CpaasError::protocol(
                "CPaaS stream id does not match the active session",
            ));
        }
        Ok(())
    }

    fn stream(&self) -> Result<&StreamState, CpaasError> {
        self.stream
            .as_ref()
            .ok_or_else(|| CpaasError::protocol("CPaaS start event must arrive first"))
    }

    fn active_turn(&self) -> Result<String, CpaasError> {
        self.active_turn_id
            .clone()
            .ok_or_else(|| CpaasError::protocol("CPaaS media event has no active turn"))
    }

    fn next_seq(&mut self) -> u64 {
        let seq = self.seq;
        self.seq += 1;
        seq
    }
}

fn decode_provider_audio(codec: &str, payload: &[u8]) -> Result<Vec<u8>, CpaasError> {
    match codec {
        TWILIO_MULAW_CODEC | TELNYX_PCMU_CODEC => Ok(payload
            .iter()
            .flat_map(|sample| mulaw_decode(*sample).to_le_bytes())
            .collect()),
        TELNYX_L16_CODEC if payload.len() % 2 == 0 => Ok(payload
            .chunks_exact(2)
            .flat_map(|sample| [sample[1], sample[0]])
            .collect()),
        TELNYX_L16_CODEC => Err(CpaasError::protocol(
            "L16 media payload contains an incomplete sample",
        )),
        _ => Err(CpaasError::protocol("unsupported CPaaS media codec")),
    }
}

fn mulaw_decode(value: u8) -> i16 {
    let value = !value;
    let sign = value & 0x80;
    let exponent = (value >> 4) & 0x07;
    let mantissa = value & 0x0f;
    let magnitude = (((mantissa as i32) << 3) + 0x84) << exponent;
    let sample = magnitude - 0x84;
    if sign != 0 {
        (-sample) as i16
    } else {
        sample as i16
    }
}

fn mulaw_encode(sample: i16) -> u8 {
    const BIAS: i32 = 0x84;
    const CLIP: i32 = 32_635;
    let sample = sample as i32;
    let sign = if sample < 0 { 0x80 } else { 0 };
    let magnitude = sample.abs().min(CLIP) + BIAS;
    let exponent = (24 - magnitude.leading_zeros() as i32).clamp(0, 7) as u8;
    let mantissa = ((magnitude >> (exponent + 3)) & 0x0f) as u8;
    !(sign | (exponent << 4) | mantissa)
}

fn required_path_text<'a>(
    value: &'a Value,
    path: &[&str],
    label: &str,
) -> Result<&'a str, CpaasError> {
    let mut current = value;
    for field in path {
        current = current
            .get(*field)
            .ok_or_else(|| CpaasError::protocol(format!("CPaaS {label} is required")))?;
    }
    required_text(Some(current), label)
}

fn required_path_u64(value: &Value, path: &[&str], label: &str) -> Result<u64, CpaasError> {
    let mut current = value;
    for field in path {
        current = current
            .get(*field)
            .ok_or_else(|| CpaasError::protocol(format!("CPaaS {label} is required")))?;
    }
    current
        .as_u64()
        .ok_or_else(|| CpaasError::protocol(format!("CPaaS {label} must be an integer")))
}

fn required_text<'a>(value: Option<&'a Value>, label: &str) -> Result<&'a str, CpaasError> {
    let text = value
        .and_then(Value::as_str)
        .ok_or_else(|| CpaasError::protocol(format!("CPaaS {label} must be text")))?;
    validate_text(text, label)?;
    Ok(text)
}

fn validate_text(value: &str, label: &str) -> Result<(), CpaasError> {
    if value.trim().is_empty()
        || value.len() > MAX_CONTROL_FIELD_CHARS
        || value.chars().any(char::is_control)
    {
        return Err(CpaasError::protocol(format!(
            "CPaaS {label} must be bounded non-empty text",
        )));
    }
    Ok(())
}

fn parse_decimal(value: &str, label: &str) -> Result<u64, CpaasError> {
    value
        .parse()
        .map_err(|_| CpaasError::protocol(format!("CPaaS {label} must be an integer")))
}

fn control(value: Value) -> Result<ControlMessage, CpaasError> {
    serde_json::from_value(value).map_err(|error| {
        CpaasError::protocol(format!("CPaaS control-schema conversion failed: {error}"))
    })
}

fn text_message(value: Value) -> Result<Message, CpaasError> {
    serde_json::to_string(&value)
        .map(|text| Message::Text(text.into()))
        .map_err(|error| CpaasError::protocol(format!("encode CPaaS message failed: {error}")))
}
