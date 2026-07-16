use crate::control::schema::ControlMessage;
use serde_json::json;
use std::fmt;
use tokio::io::{AsyncRead, AsyncReadExt};

use super::media_plane::MediaPlaneEvent;
use super::metrics::TransportMetricSample;
use super::{CONTROL_SCHEMA_VERSION, UNKNOWN_CALLER, VALID_DTMF};

const FRAME_HANGUP: u8 = 0x00;
const FRAME_UUID: u8 = 0x01;
const FRAME_DTMF: u8 = 0x03;
const FRAME_SLIN8: u8 = 0x10;
const FRAME_ERROR: u8 = 0xff;
const UUID_BYTES: usize = 16;
const TRANSPORT_NAME: &str = "asterisk/audiosocket";
const SLIN8_CODEC: &str = "slin/8000";
const DTMF_FEATURE: &str = "dtmf";
const REMOTE_HANGUP_REASON: &str = "remote_hangup";

pub const SLIN8_SAMPLE_RATE_HZ: u32 = 8_000;

pub fn encode_slin8_frame(payload: &[u8]) -> Result<Vec<u8>, AudioSocketError> {
    if payload.is_empty() || payload.len() % 2 != 0 {
        return Err(AudioSocketError::protocol(
            "AudioSocket output must contain complete 16-bit samples",
        ));
    }
    encode_frame(FRAME_SLIN8, payload)
}

pub fn encode_dtmf_frames(digits: &str) -> Result<Vec<u8>, AudioSocketError> {
    if digits.is_empty()
        || !digits
            .as_bytes()
            .iter()
            .all(|digit| VALID_DTMF.contains(digit))
    {
        return Err(AudioSocketError::protocol(
            "AudioSocket output DTMF contains an invalid digit",
        ));
    }
    let mut frames = Vec::with_capacity(digits.len() * 4);
    for digit in digits.bytes() {
        frames.extend(encode_frame(FRAME_DTMF, &[digit.to_ascii_uppercase()])?);
    }
    Ok(frames)
}

pub fn encode_hangup_frame() -> Vec<u8> {
    vec![FRAME_HANGUP, 0, 0]
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaFrame {
    pub session_id: String,
    pub sample_rate_hz: u32,
    pub pcm_s16le: Vec<u8>,
}

#[derive(Debug, Clone)]
pub enum AdapterOutput {
    Control(ControlMessage),
    Media(MediaFrame),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioSocketError(String);

impl AudioSocketError {
    fn protocol(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for AudioSocketError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for AudioSocketError {}

#[derive(Debug, Default)]
pub struct AudioSocketAdapter {
    session_id: Option<String>,
    seq: u64,
    ended: bool,
    wire_buffer: Vec<u8>,
    next_turn: u64,
    active_turn_id: Option<String>,
    latest_turn_id: Option<String>,
}

impl AudioSocketAdapter {
    pub fn active_session_id(&self) -> Option<&str> {
        self.session_id.as_deref()
    }

    pub fn transport_metrics(
        &mut self,
        sample: TransportMetricSample,
        ts_ms: u64,
    ) -> Result<Option<AdapterOutput>, AudioSocketError> {
        let Some(turn_id) = self.latest_turn_id.clone() else {
            return Ok(None);
        };
        let session_id = self.session_id()?.to_string();
        let seq = self.next_seq();
        Self::control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "transport.metrics",
            "session_id": session_id,
            "turn_id": turn_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "jitter_ms": sample.jitter_ms,
            "rtt_ms": sample.rtt_ms,
            "packet_loss": sample.packet_loss
        }))
        .map(Some)
    }

    pub fn media_plane_event(
        &mut self,
        event: MediaPlaneEvent,
    ) -> Result<AdapterOutput, AudioSocketError> {
        let session_id = self.session_id()?.to_string();
        match event {
            MediaPlaneEvent::SpeechStarted { at_ms } => {
                if self.active_turn_id.is_some() {
                    return Err(AudioSocketError::protocol(
                        "media-plane speech started while a turn is active",
                    ));
                }
                self.next_turn += 1;
                let turn_id = format!("turn-{}", self.next_turn);
                self.active_turn_id = Some(turn_id.clone());
                let seq = self.next_seq();
                Self::control(json!({
                    "v": CONTROL_SCHEMA_VERSION,
                    "type": "vad.speech_start",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": seq,
                    "ts_ms": at_ms,
                    "at_ms": at_ms
                }))
            }
            MediaPlaneEvent::TranscriptPartial {
                text,
                stability,
                at_ms,
                provider,
            } => {
                let turn_id = self.active_turn()?.to_string();
                let seq = self.next_seq();
                Self::control(json!({
                    "v": CONTROL_SCHEMA_VERSION,
                    "type": "stt.partial",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": seq,
                    "ts_ms": at_ms,
                    "text": text,
                    "stability": stability,
                    "provider": provider
                }))
            }
            MediaPlaneEvent::SpeechEnded { at_ms, speech_ms } => {
                let turn_id = self.active_turn()?.to_string();
                let seq = self.next_seq();
                Self::control(json!({
                    "v": CONTROL_SCHEMA_VERSION,
                    "type": "vad.speech_end",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": seq,
                    "ts_ms": at_ms,
                    "at_ms": at_ms,
                    "speech_ms": speech_ms
                }))
            }
            MediaPlaneEvent::TranscriptFinal {
                text,
                stt_ms,
                at_ms,
                provider,
            } => {
                let turn_id = self.active_turn()?.to_string();
                let seq = self.next_seq();
                let output = Self::control(json!({
                    "v": CONTROL_SCHEMA_VERSION,
                    "type": "stt.final",
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "seq": seq,
                    "ts_ms": at_ms.saturating_add(stt_ms),
                    "text": text,
                    "provider": provider,
                    "stt_ms": stt_ms
                }))?;
                self.latest_turn_id = Some(turn_id);
                self.active_turn_id = None;
                Ok(output)
            }
            MediaPlaneEvent::PlaybackStarted { utterance_id } => {
                self.playback_control(session_id, utterance_id, "started", 0)
            }
            MediaPlaneEvent::PlaybackFinished {
                utterance_id,
                mark_chars,
            } => self.playback_control(session_id, utterance_id, "finished", mark_chars),
            MediaPlaneEvent::PlaybackFlushed {
                utterance_id,
                mark_chars,
            } => self.playback_control(session_id, utterance_id, "flushed", mark_chars),
        }
    }

    fn playback_control(
        &mut self,
        session_id: String,
        utterance_id: String,
        state: &str,
        mark_chars: u64,
    ) -> Result<AdapterOutput, AudioSocketError> {
        let seq = self.next_seq();
        Self::control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "tts.playback",
            "session_id": session_id,
            "seq": seq,
            "ts_ms": 0,
            "utterance_id": utterance_id,
            "state": state,
            "mark_chars": mark_chars
        }))
    }

    fn active_turn(&self) -> Result<&str, AudioSocketError> {
        self.active_turn_id
            .as_deref()
            .ok_or_else(|| AudioSocketError::protocol("media-plane event has no active turn"))
    }

    pub async fn read_next<R>(
        &mut self,
        reader: &mut R,
        ts_ms: u64,
    ) -> Result<Option<AdapterOutput>, AudioSocketError>
    where
        R: AsyncRead + Unpin,
    {
        if self.ended {
            return Err(AudioSocketError::protocol(
                "AudioSocket frame received after hangup",
            ));
        }
        loop {
            if let Some((kind, payload)) = self.take_frame()? {
                return self.handle_frame(kind, payload, ts_ms).map(Some);
            }
            let received = reader
                .read_buf(&mut self.wire_buffer)
                .await
                .map_err(|error| {
                    AudioSocketError::protocol(format!("AudioSocket read failed: {error}"))
                })?;
            if received == 0 {
                if self.wire_buffer.is_empty() {
                    return Ok(None);
                }
                let message = if self.wire_buffer.len() < 3 {
                    "truncated AudioSocket header"
                } else {
                    "truncated AudioSocket payload"
                };
                return Err(AudioSocketError::protocol(message));
            }
        }
    }

    fn take_frame(&mut self) -> Result<Option<(u8, Vec<u8>)>, AudioSocketError> {
        if self.wire_buffer.len() < 3 {
            return Ok(None);
        }
        let length = usize::from(u16::from_be_bytes([
            self.wire_buffer[1],
            self.wire_buffer[2],
        ]));
        let frame_length = 3 + length;
        if self.wire_buffer.len() < frame_length {
            return Ok(None);
        }
        let kind = self.wire_buffer[0];
        let payload = self.wire_buffer[3..frame_length].to_vec();
        self.wire_buffer.drain(..frame_length);
        Ok(Some((kind, payload)))
    }

    pub fn stream_closed(&mut self, ts_ms: u64) -> Result<AdapterOutput, AudioSocketError> {
        self.finish(ts_ms, REMOTE_HANGUP_REASON)
    }

    pub fn end(&mut self, ts_ms: u64, reason: &str) -> Result<AdapterOutput, AudioSocketError> {
        if reason.trim().is_empty() || reason.chars().any(char::is_control) {
            return Err(AudioSocketError::protocol(
                "AudioSocket end reason must be non-empty text",
            ));
        }
        self.finish(ts_ms, reason)
    }

    fn handle_frame(
        &mut self,
        kind: u8,
        payload: Vec<u8>,
        ts_ms: u64,
    ) -> Result<AdapterOutput, AudioSocketError> {
        if self.session_id.is_none() && kind != FRAME_UUID {
            return Err(AudioSocketError::protocol(
                "AudioSocket first frame must be UUID",
            ));
        }
        match kind {
            FRAME_UUID => self.start_session(payload, ts_ms),
            FRAME_DTMF => self.dtmf(payload, ts_ms),
            FRAME_SLIN8 => self.media(payload),
            FRAME_HANGUP => self.hangup(payload, ts_ms),
            FRAME_ERROR => Err(AudioSocketError::protocol(
                "Asterisk reported an AudioSocket protocol error",
            )),
            other => Err(AudioSocketError::protocol(format!(
                "unsupported AudioSocket frame type 0x{other:02x}"
            ))),
        }
    }

    fn start_session(
        &mut self,
        payload: Vec<u8>,
        ts_ms: u64,
    ) -> Result<AdapterOutput, AudioSocketError> {
        if self.session_id.is_some() {
            return Err(AudioSocketError::protocol(
                "AudioSocket UUID frame may only appear once",
            ));
        }
        if payload.len() != UUID_BYTES {
            return Err(AudioSocketError::protocol(
                "AudioSocket UUID payload must be 16 bytes",
            ));
        }
        let session_id = format_uuid(&payload);
        self.session_id = Some(session_id.clone());
        let seq = self.next_seq();
        Self::control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.started",
            "session_id": session_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "transport": TRANSPORT_NAME,
            "caller": UNKNOWN_CALLER,
            "codecs": [SLIN8_CODEC],
            "features": [DTMF_FEATURE]
        }))
    }

    fn dtmf(&mut self, payload: Vec<u8>, ts_ms: u64) -> Result<AdapterOutput, AudioSocketError> {
        if payload.len() != 1 || !VALID_DTMF.contains(&payload[0]) {
            return Err(AudioSocketError::protocol(
                "AudioSocket DTMF payload must be one valid digit",
            ));
        }
        let digit = char::from(payload[0]).to_ascii_uppercase().to_string();
        let session_id = self.session_id()?.to_string();
        let seq = self.next_seq();
        Self::control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "dtmf",
            "session_id": session_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "digit": digit
        }))
    }

    fn media(&self, payload: Vec<u8>) -> Result<AdapterOutput, AudioSocketError> {
        if payload.is_empty() || payload.len() % 2 != 0 {
            return Err(AudioSocketError::protocol(
                "AudioSocket audio payload must contain complete 16-bit samples",
            ));
        }
        Ok(AdapterOutput::Media(MediaFrame {
            session_id: self.session_id()?.to_string(),
            sample_rate_hz: SLIN8_SAMPLE_RATE_HZ,
            pcm_s16le: payload,
        }))
    }

    fn hangup(&mut self, payload: Vec<u8>, ts_ms: u64) -> Result<AdapterOutput, AudioSocketError> {
        if !payload.is_empty() {
            return Err(AudioSocketError::protocol(
                "AudioSocket hangup payload must be empty",
            ));
        }
        self.finish(ts_ms, REMOTE_HANGUP_REASON)
    }

    fn finish(&mut self, ts_ms: u64, reason: &str) -> Result<AdapterOutput, AudioSocketError> {
        if self.ended {
            return Err(AudioSocketError::protocol(
                "AudioSocket session has already ended",
            ));
        }
        let session_id = self.session_id()?.to_string();
        let seq = self.next_seq();
        let output = Self::control(json!({
            "v": CONTROL_SCHEMA_VERSION,
            "type": "session.ended",
            "session_id": session_id,
            "seq": seq,
            "ts_ms": ts_ms,
            "reason": reason
        }))?;
        self.ended = true;
        Ok(output)
    }

    fn session_id(&self) -> Result<&str, AudioSocketError> {
        self.session_id
            .as_deref()
            .ok_or_else(|| AudioSocketError::protocol("AudioSocket session has no UUID"))
    }

    fn next_seq(&mut self) -> u64 {
        self.seq += 1;
        self.seq
    }

    fn control(value: serde_json::Value) -> Result<AdapterOutput, AudioSocketError> {
        serde_json::from_value(value)
            .map(AdapterOutput::Control)
            .map_err(|error| {
                AudioSocketError::protocol(format!(
                    "AudioSocket control-schema conversion failed: {error}"
                ))
            })
    }
}

fn encode_frame(kind: u8, payload: &[u8]) -> Result<Vec<u8>, AudioSocketError> {
    let length = u16::try_from(payload.len())
        .map_err(|_| AudioSocketError::protocol("AudioSocket output frame is too large"))?;
    let mut frame = Vec::with_capacity(payload.len() + 3);
    frame.push(kind);
    frame.extend(length.to_be_bytes());
    frame.extend(payload);
    Ok(frame)
}

fn format_uuid(bytes: &[u8]) -> String {
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        bytes[0],
        bytes[1],
        bytes[2],
        bytes[3],
        bytes[4],
        bytes[5],
        bytes[6],
        bytes[7],
        bytes[8],
        bytes[9],
        bytes[10],
        bytes[11],
        bytes[12],
        bytes[13],
        bytes[14],
        bytes[15]
    )
}
