use super::{
    media_plane::{
        FixtureMediaPlane, FixtureMediaPlaneConfig, MediaPlaneError, MediaPlaneEvent, PlaybackStep,
    },
    playback_queue::{CancelPlayback, PlaybackQueue, SubmitPlayback},
    provider_media::{ProviderMediaConfig, ProviderMediaPlane, DEFAULT_PROVIDER_PLAYBACK_FRAME_MS},
};
use std::time::Duration;

enum MediaBackend {
    Fixture(FixtureMediaPlane),
    Provider(ProviderMediaPlane),
    Unconfigured,
}

/// Shared media backend and clause queue lifecycle for Asterisk transports.
///
/// Adapters own their wire protocol and event mapping. This type owns the
/// common media-plane behavior so a clause has identical queue, cancellation,
/// pacing, and provider-drain semantics across AudioSocket, Media WebSocket,
/// and ARI RTP.
pub(crate) struct MediaSession {
    backend: MediaBackend,
    playback_queue: PlaybackQueue,
    playback_pacing: Duration,
}

pub(crate) struct PlaybackCancellation {
    pub events: Vec<MediaPlaneEvent>,
    pub active_cancelled: bool,
}

pub(crate) enum PlaybackTick {
    Idle,
    Audio(Vec<u8>),
    Finished {
        event: MediaPlaneEvent,
        next_started: Option<MediaPlaneEvent>,
    },
}

impl MediaSession {
    pub fn new(
        fixture: Option<FixtureMediaPlaneConfig>,
        provider: Option<ProviderMediaConfig>,
    ) -> Result<Self, MediaPlaneError> {
        let playback_pacing = fixture
            .as_ref()
            .map(|config| config.playback_pacing_ms)
            .or_else(|| {
                provider
                    .as_ref()
                    .map(|config| config.elevenlabs.playback_frame_ms)
            })
            .unwrap_or(DEFAULT_PROVIDER_PLAYBACK_FRAME_MS);
        let backend = match fixture {
            Some(config) => MediaBackend::Fixture(config.open()?),
            None => match provider {
                Some(config) => MediaBackend::Provider(ProviderMediaPlane::new(config)),
                None => MediaBackend::Unconfigured,
            },
        };
        Ok(Self {
            backend,
            playback_queue: PlaybackQueue::default(),
            playback_pacing: Duration::from_millis(playback_pacing),
        })
    }

    pub fn playback_pacing(&self) -> Duration {
        self.playback_pacing
    }

    pub fn has_backend(&self) -> bool {
        !matches!(self.backend, MediaBackend::Unconfigured)
    }

    pub fn push_audio(
        &mut self,
        pcm_s16le: &[u8],
        sample_rate_hz: u32,
        at_ms: u64,
    ) -> Result<Vec<MediaPlaneEvent>, MediaPlaneError> {
        match &mut self.backend {
            MediaBackend::Fixture(plane) => plane.push_audio(pcm_s16le),
            MediaBackend::Provider(plane) => plane.push_audio(pcm_s16le, sample_rate_hz, at_ms),
            MediaBackend::Unconfigured => {
                Err(MediaPlaneError::invalid("media backend is not configured"))
            }
        }
    }

    pub fn drain_provider_events(&mut self) -> Result<Vec<MediaPlaneEvent>, MediaPlaneError> {
        match &mut self.backend {
            MediaBackend::Provider(plane) => plane.drain_events(),
            MediaBackend::Fixture(_) | MediaBackend::Unconfigured => Ok(Vec::new()),
        }
    }

    pub fn submit_playback(
        &mut self,
        utterance_id: String,
        text: String,
        flush: bool,
    ) -> Result<Vec<MediaPlaneEvent>, MediaPlaneError> {
        match self.playback_queue.submit(utterance_id, text, flush)? {
            SubmitPlayback::Start(request) => self
                .start_playback(&request.utterance_id, &request.text, request.flush)
                .map(|event| vec![event]),
            SubmitPlayback::Queued => Ok(Vec::new()),
        }
    }

    pub fn cancel_playback(
        &mut self,
        utterance_id: &str,
    ) -> Result<PlaybackCancellation, MediaPlaneError> {
        match self.playback_queue.cancel(utterance_id)? {
            CancelPlayback::Active { utterance_id, next } => {
                let mut events = vec![self.cancel_active_playback(&utterance_id)?];
                if let Some(next) = next {
                    events.push(self.start_playback(&next.utterance_id, &next.text, next.flush)?);
                }
                Ok(PlaybackCancellation {
                    events,
                    active_cancelled: true,
                })
            }
            CancelPlayback::Queued { utterance_id } => Ok(PlaybackCancellation {
                events: vec![MediaPlaneEvent::PlaybackFlushed {
                    utterance_id,
                    mark_chars: 0,
                }],
                active_cancelled: false,
            }),
        }
    }

    pub fn next_playback(&mut self, sample_rate_hz: u32) -> Result<PlaybackTick, MediaPlaneError> {
        let step = match &mut self.backend {
            MediaBackend::Fixture(plane) => plane.next_playback(),
            MediaBackend::Provider(plane) => plane.next_playback(sample_rate_hz),
            MediaBackend::Unconfigured => return Ok(PlaybackTick::Idle),
        }?;
        match step {
            PlaybackStep::Idle => Ok(PlaybackTick::Idle),
            PlaybackStep::Audio(audio) => Ok(PlaybackTick::Audio(audio)),
            PlaybackStep::Finished(event) => {
                let MediaPlaneEvent::PlaybackFinished { utterance_id, .. } = &event else {
                    return Err(MediaPlaneError::invalid(
                        "media playback finished with an invalid event",
                    ));
                };
                let next_started = self
                    .playback_queue
                    .complete(utterance_id)?
                    .map(|next| self.start_playback(&next.utterance_id, &next.text, next.flush))
                    .transpose()?;
                Ok(PlaybackTick::Finished {
                    event,
                    next_started,
                })
            }
        }
    }

    fn start_playback(
        &mut self,
        utterance_id: &str,
        text: &str,
        flush: bool,
    ) -> Result<MediaPlaneEvent, MediaPlaneError> {
        match &mut self.backend {
            MediaBackend::Fixture(plane) => plane.start_playback(utterance_id, text),
            MediaBackend::Provider(plane) => plane.start_playback(utterance_id, text, flush),
            MediaBackend::Unconfigured => {
                Err(MediaPlaneError::invalid("media backend is not configured"))
            }
        }
    }

    fn cancel_active_playback(
        &mut self,
        utterance_id: &str,
    ) -> Result<MediaPlaneEvent, MediaPlaneError> {
        match &mut self.backend {
            MediaBackend::Fixture(plane) => plane.cancel_playback(utterance_id),
            MediaBackend::Provider(plane) => plane.cancel_playback(utterance_id),
            MediaBackend::Unconfigured => {
                Err(MediaPlaneError::invalid("media backend is not configured"))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn queued_playback_starts_automatically_after_completion() {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/audio");
        let fixture = FixtureMediaPlaneConfig {
            playback_wav: std::env::var("LUCY_AUDIO_FIXTURE_WAV")
                .map(PathBuf::from)
                .unwrap_or_else(|_| root.join("booking_caller_8k.wav")),
            transcript_timeline: std::env::var("LUCY_AUDIO_FIXTURE_TIMELINE")
                .map(PathBuf::from)
                .unwrap_or_else(|_| root.join("booking_caller.timeline.json")),
            transcript_trigger_bytes: 320,
            playback_chunk_bytes: 320,
            playback_pacing_ms: 20,
        };
        let mut session = MediaSession::new(Some(fixture), None).unwrap();

        assert!(matches!(
            session
                .submit_playback("first".into(), "First clause".into(), false)
                .unwrap()[..],
            [MediaPlaneEvent::PlaybackStarted { .. }]
        ));
        assert!(session
            .submit_playback("second".into(), "Second clause".into(), true)
            .unwrap()
            .is_empty());

        loop {
            match session.next_playback(8_000).unwrap() {
                PlaybackTick::Audio(_) | PlaybackTick::Idle => {}
                PlaybackTick::Finished {
                    event,
                    next_started,
                } => {
                    assert!(
                        matches!(event, MediaPlaneEvent::PlaybackFinished { utterance_id, .. } if utterance_id == "first")
                    );
                    assert!(
                        matches!(next_started, Some(MediaPlaneEvent::PlaybackStarted { utterance_id }) if utterance_id == "second")
                    );
                    break;
                }
            }
        }
    }
}
