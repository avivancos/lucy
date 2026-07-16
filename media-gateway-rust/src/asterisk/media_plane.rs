use crate::control::fixture::{AudioFixture, Utterance};
use hound::WavReader;
use std::fmt;
use std::path::Path;
use std::path::PathBuf;

pub const FIXTURE_PROVIDER: &str = "fixture";

#[derive(Debug, Clone, PartialEq)]
pub enum MediaPlaneEvent {
    SpeechStarted {
        at_ms: u64,
    },
    TranscriptPartial {
        text: String,
        stability: f64,
        at_ms: u64,
        provider: &'static str,
    },
    SpeechEnded {
        at_ms: u64,
        speech_ms: u64,
    },
    TranscriptFinal {
        text: String,
        stt_ms: u64,
        at_ms: u64,
        provider: &'static str,
    },
    PlaybackStarted {
        utterance_id: String,
    },
    PlaybackFinished {
        utterance_id: String,
        mark_chars: u64,
    },
    PlaybackFlushed {
        utterance_id: String,
        mark_chars: u64,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub enum PlaybackStep {
    Idle,
    Audio(Vec<u8>),
    Finished(MediaPlaneEvent),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaPlaneError(String);

impl MediaPlaneError {
    pub(crate) fn invalid(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for MediaPlaneError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for MediaPlaneError {}

#[derive(Debug)]
struct ActivePlayback {
    utterance_id: String,
    mark_chars: u64,
    cursor: usize,
}

/// Deterministic media-plane provider backed by a real recorded call fixture.
///
/// This is the local no-network provider used by the Asterisk lab. It exercises
/// the same bounded PCM and cancellation path as network provider backends while
/// keeping every audio byte inside the Rust process.
pub struct FixtureMediaPlane {
    utterances: Vec<Utterance>,
    playback_pcm: Vec<u8>,
    transcript_trigger_bytes: usize,
    playback_chunk_bytes: usize,
    received_since_transcript: usize,
    next_utterance: usize,
    active_playback: Option<ActivePlayback>,
}

#[derive(Debug, Clone)]
pub struct FixtureMediaPlaneConfig {
    pub playback_wav: PathBuf,
    pub transcript_timeline: PathBuf,
    pub transcript_trigger_bytes: usize,
    pub playback_chunk_bytes: usize,
    pub playback_pacing_ms: u64,
}

impl FixtureMediaPlaneConfig {
    pub fn open(&self) -> Result<FixtureMediaPlane, MediaPlaneError> {
        if self.playback_pacing_ms == 0 {
            return Err(MediaPlaneError::invalid(
                "media-plane playback pacing must be positive",
            ));
        }
        FixtureMediaPlane::load(
            &self.playback_wav,
            &self.transcript_timeline,
            self.transcript_trigger_bytes,
            self.playback_chunk_bytes,
        )
    }
}

impl FixtureMediaPlane {
    pub fn load(
        playback_wav: &Path,
        transcript_timeline: &Path,
        transcript_trigger_bytes: usize,
        playback_chunk_bytes: usize,
    ) -> Result<Self, MediaPlaneError> {
        if transcript_trigger_bytes == 0 || transcript_trigger_bytes % 2 != 0 {
            return Err(MediaPlaneError::invalid(
                "media-plane transcript trigger must be positive whole PCM16 samples",
            ));
        }
        if playback_chunk_bytes == 0
            || playback_chunk_bytes % 2 != 0
            || playback_chunk_bytes > u16::MAX as usize
        {
            return Err(MediaPlaneError::invalid(
                "media-plane playback chunk must fit one AudioSocket PCM16 frame",
            ));
        }
        let fixture = AudioFixture::load(playback_wav, transcript_timeline)
            .map_err(MediaPlaneError::invalid)?;
        if fixture.timeline.utterances.is_empty() {
            return Err(MediaPlaneError::invalid(
                "media-plane transcript fixture has no utterances",
            ));
        }
        let playback_pcm = WavReader::open(playback_wav)
            .map_err(|_| MediaPlaneError::invalid("media-plane playback WAV cannot be opened"))?
            .into_samples::<i16>()
            .map(|sample| {
                sample
                    .map(i16::to_le_bytes)
                    .map_err(|_| MediaPlaneError::invalid("media-plane playback WAV is invalid"))
            })
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();
        if playback_pcm.is_empty() {
            return Err(MediaPlaneError::invalid(
                "media-plane playback WAV contains no PCM",
            ));
        }
        Ok(Self {
            utterances: fixture.timeline.utterances,
            playback_pcm,
            transcript_trigger_bytes,
            playback_chunk_bytes,
            received_since_transcript: 0,
            next_utterance: 0,
            active_playback: None,
        })
    }

    pub fn push_audio(
        &mut self,
        pcm_s16le: &[u8],
    ) -> Result<Vec<MediaPlaneEvent>, MediaPlaneError> {
        if pcm_s16le.is_empty() || pcm_s16le.len() % 2 != 0 {
            return Err(MediaPlaneError::invalid(
                "media-plane input must contain complete PCM16 samples",
            ));
        }
        self.received_since_transcript = self
            .received_since_transcript
            .saturating_add(pcm_s16le.len());
        let mut events = Vec::new();
        while self.received_since_transcript >= self.transcript_trigger_bytes
            && self.next_utterance < self.utterances.len()
        {
            self.received_since_transcript -= self.transcript_trigger_bytes;
            let utterance = &self.utterances[self.next_utterance];
            self.next_utterance += 1;
            events.extend([
                MediaPlaneEvent::SpeechStarted {
                    at_ms: utterance.start_ms,
                },
                MediaPlaneEvent::TranscriptPartial {
                    text: utterance.text.clone(),
                    stability: 1.0,
                    at_ms: utterance.end_ms,
                    provider: FIXTURE_PROVIDER,
                },
                MediaPlaneEvent::SpeechEnded {
                    at_ms: utterance.end_ms,
                    speech_ms: utterance.end_ms - utterance.start_ms,
                },
                MediaPlaneEvent::TranscriptFinal {
                    text: utterance.text.clone(),
                    stt_ms: 0,
                    at_ms: utterance.end_ms,
                    provider: FIXTURE_PROVIDER,
                },
            ]);
        }
        Ok(events)
    }

    pub fn start_playback(
        &mut self,
        utterance_id: &str,
        text: &str,
    ) -> Result<MediaPlaneEvent, MediaPlaneError> {
        validate_text("utterance_id", utterance_id)?;
        validate_text("text", text)?;
        if self.active_playback.is_some() {
            return Err(MediaPlaneError::invalid(
                "media-plane playback is already active",
            ));
        }
        self.active_playback = Some(ActivePlayback {
            utterance_id: utterance_id.to_string(),
            mark_chars: text.chars().count() as u64,
            cursor: 0,
        });
        Ok(MediaPlaneEvent::PlaybackStarted {
            utterance_id: utterance_id.to_string(),
        })
    }

    pub fn next_playback(&mut self) -> Result<PlaybackStep, MediaPlaneError> {
        let Some(playback) = self.active_playback.as_mut() else {
            return Ok(PlaybackStep::Idle);
        };
        if playback.cursor < self.playback_pcm.len() {
            let end = playback
                .cursor
                .saturating_add(self.playback_chunk_bytes)
                .min(self.playback_pcm.len());
            let chunk = self.playback_pcm[playback.cursor..end].to_vec();
            playback.cursor = end;
            return Ok(PlaybackStep::Audio(chunk));
        }
        let playback = self.active_playback.take().expect("active playback exists");
        Ok(PlaybackStep::Finished(MediaPlaneEvent::PlaybackFinished {
            utterance_id: playback.utterance_id,
            mark_chars: playback.mark_chars,
        }))
    }

    pub fn cancel_playback(
        &mut self,
        utterance_id: &str,
    ) -> Result<MediaPlaneEvent, MediaPlaneError> {
        validate_text("utterance_id", utterance_id)?;
        let active = self
            .active_playback
            .take()
            .ok_or_else(|| MediaPlaneError::invalid("media-plane playback is not active"))?;
        if utterance_id != "all" && active.utterance_id != utterance_id {
            self.active_playback = Some(active);
            return Err(MediaPlaneError::invalid(
                "media-plane cancel does not match active utterance",
            ));
        }
        let mark_chars = (active.cursor as u128 * u128::from(active.mark_chars)
            / self.playback_pcm.len() as u128) as u64;
        Ok(MediaPlaneEvent::PlaybackFlushed {
            utterance_id: active.utterance_id,
            mark_chars,
        })
    }
}

fn validate_text(field: &str, value: &str) -> Result<(), MediaPlaneError> {
    if value.trim().is_empty() || value.chars().any(char::is_control) {
        return Err(MediaPlaneError::invalid(format!(
            "media-plane {field} must be non-empty text",
        )));
    }
    Ok(())
}
