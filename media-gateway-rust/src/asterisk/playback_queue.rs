use super::media_plane::MediaPlaneError;
use std::collections::VecDeque;

pub(crate) const PLAYBACK_QUEUE_CAPACITY: usize = 32;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PlaybackRequest {
    pub utterance_id: String,
    pub text: String,
    pub flush: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum SubmitPlayback {
    Start(PlaybackRequest),
    Queued,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum CancelPlayback {
    Active {
        utterance_id: String,
        next: Option<PlaybackRequest>,
    },
    Queued {
        utterance_id: String,
    },
}

#[derive(Debug, Default)]
pub(crate) struct PlaybackQueue {
    active: Option<String>,
    pending: VecDeque<PlaybackRequest>,
}

impl PlaybackQueue {
    pub fn submit(
        &mut self,
        utterance_id: String,
        text: String,
        flush: bool,
    ) -> Result<SubmitPlayback, MediaPlaneError> {
        validate_text("utterance_id", &utterance_id)?;
        validate_text("text", &text)?;
        if self.active.as_deref() == Some(utterance_id.as_str())
            || self
                .pending
                .iter()
                .any(|request| request.utterance_id == utterance_id)
        {
            return Err(MediaPlaneError::invalid(
                "playback queue utterance_id must be unique",
            ));
        }
        let request = PlaybackRequest {
            utterance_id,
            text,
            flush,
        };
        if self.active.is_none() {
            self.active = Some(request.utterance_id.clone());
            return Ok(SubmitPlayback::Start(request));
        }
        if self.pending.len() >= PLAYBACK_QUEUE_CAPACITY - 1 {
            return Err(MediaPlaneError::invalid("playback queue is full"));
        }
        self.pending.push_back(request);
        Ok(SubmitPlayback::Queued)
    }

    pub fn complete(
        &mut self,
        utterance_id: &str,
    ) -> Result<Option<PlaybackRequest>, MediaPlaneError> {
        if self.active.as_deref() != Some(utterance_id) {
            return Err(MediaPlaneError::invalid(
                "playback completion does not match active utterance",
            ));
        }
        self.active = None;
        Ok(self.activate_next())
    }

    pub fn cancel(&mut self, utterance_id: &str) -> Result<CancelPlayback, MediaPlaneError> {
        validate_text("utterance_id", utterance_id)?;
        if utterance_id == "all" {
            self.pending.clear();
            let active = self
                .active
                .take()
                .ok_or_else(|| MediaPlaneError::invalid("playback queue is not active"))?;
            return Ok(CancelPlayback::Active {
                utterance_id: active,
                next: None,
            });
        }
        if self.active.as_deref() == Some(utterance_id) {
            let active = self.active.take().expect("active utterance exists");
            return Ok(CancelPlayback::Active {
                utterance_id: active,
                next: self.activate_next(),
            });
        }
        let index = self
            .pending
            .iter()
            .position(|request| request.utterance_id == utterance_id)
            .ok_or_else(|| MediaPlaneError::invalid("playback cancel does not match queue"))?;
        let cancelled = self.pending.remove(index).expect("queued utterance exists");
        Ok(CancelPlayback::Queued {
            utterance_id: cancelled.utterance_id,
        })
    }

    fn activate_next(&mut self) -> Option<PlaybackRequest> {
        let next = self.pending.pop_front()?;
        self.active = Some(next.utterance_id.clone());
        Some(next)
    }
}

fn validate_text(field: &str, value: &str) -> Result<(), MediaPlaneError> {
    if value.trim().is_empty() || value.chars().any(char::is_control) {
        return Err(MediaPlaneError::invalid(format!(
            "playback queue {field} must be non-empty text",
        )));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queue_is_ordered_bounded_and_preserves_flush() {
        let mut queue = PlaybackQueue::default();
        assert!(matches!(
            queue.submit("first".into(), "First".into(), false).unwrap(),
            SubmitPlayback::Start(PlaybackRequest { flush: false, .. })
        ));
        for index in 0..PLAYBACK_QUEUE_CAPACITY - 1 {
            assert_eq!(
                queue
                    .submit(format!("queued-{index}"), format!("Clause {index}"), true)
                    .unwrap(),
                SubmitPlayback::Queued
            );
        }
        assert!(queue
            .submit("overflow".into(), "Overflow".into(), true)
            .is_err());
        let next = queue.complete("first").unwrap().unwrap();
        assert_eq!(next.utterance_id, "queued-0");
        assert!(next.flush);
    }

    #[test]
    fn cancelling_all_drops_every_queued_clause() {
        let mut queue = PlaybackQueue::default();
        queue.submit("first".into(), "First".into(), true).unwrap();
        queue
            .submit("second".into(), "Second".into(), true)
            .unwrap();
        assert_eq!(
            queue.cancel("all").unwrap(),
            CancelPlayback::Active {
                utterance_id: "first".into(),
                next: None
            }
        );
        assert!(queue.complete("first").is_err());
    }
}
