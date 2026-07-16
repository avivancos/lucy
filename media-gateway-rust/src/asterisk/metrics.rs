//! Coalesced measurements from the Asterisk-facing media transport.

/// Transport measurements are deliberately sampled rather than emitted for
/// every PCM frame, so the control channel remains a low-frequency channel.
pub const DEFAULT_METRICS_EMIT_INTERVAL_MS: u64 = 1_000;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TransportMetricSample {
    pub jitter_ms: f64,
    pub rtt_ms: f64,
    /// TCP and WebSocket do not expose transport packet loss to this adapter.
    pub packet_loss: f64,
}

impl TransportMetricSample {
    pub fn tcp_websocket(rtt_ms: f64, jitter_ms: f64) -> Self {
        Self {
            jitter_ms,
            rtt_ms,
            packet_loss: 0.0,
        }
    }
}

#[derive(Debug)]
pub struct TransportMetricsSampler {
    emit_interval_ms: u64,
    last_pcm: Option<(u64, u64)>,
    jitter_total_ms: u64,
    jitter_samples: u64,
    last_probe_at_ms: Option<u64>,
    outstanding_probe: Option<Vec<u8>>,
    next_probe: u64,
}

impl TransportMetricsSampler {
    pub fn new(emit_interval_ms: u64) -> Self {
        assert!(
            emit_interval_ms > 0,
            "transport metric interval must be positive"
        );
        Self {
            emit_interval_ms,
            last_pcm: None,
            jitter_total_ms: 0,
            jitter_samples: 0,
            last_probe_at_ms: None,
            outstanding_probe: None,
            next_probe: 0,
        }
    }

    pub fn observe_pcm(&mut self, arrived_at_ms: u64, duration_ms: u64) {
        if let Some((previous_at_ms, previous_duration_ms)) = self.last_pcm {
            let observed_interval_ms = arrived_at_ms.saturating_sub(previous_at_ms);
            self.jitter_total_ms = self
                .jitter_total_ms
                .saturating_add(observed_interval_ms.abs_diff(previous_duration_ms));
            self.jitter_samples = self.jitter_samples.saturating_add(1);
        }
        self.last_pcm = Some((arrived_at_ms, duration_ms));
    }

    /// Returns a coalesced sample for transports that expose no media RTT.
    pub fn sample_without_rtt(&mut self, now_ms: u64) -> Option<TransportMetricSample> {
        if self.outstanding_probe.is_some()
            || self
                .last_probe_at_ms
                .is_some_and(|last| now_ms.saturating_sub(last) < self.emit_interval_ms)
        {
            return None;
        }
        self.last_probe_at_ms = Some(now_ms);
        Some(TransportMetricSample::tcp_websocket(0.0, self.jitter_ms()))
    }

    /// Starts one ping on the Asterisk-facing media WebSocket.
    pub fn begin_probe(&mut self, now_ms: u64) -> Option<Vec<u8>> {
        if self.outstanding_probe.is_some()
            || self
                .last_probe_at_ms
                .is_some_and(|last| now_ms.saturating_sub(last) < self.emit_interval_ms)
        {
            return None;
        }
        self.next_probe = self.next_probe.saturating_add(1);
        let payload = format!("lucy-transport-{}", self.next_probe).into_bytes();
        self.last_probe_at_ms = Some(now_ms);
        self.outstanding_probe = Some(payload.clone());
        Some(payload)
    }

    /// Completes a matching media ping/pong exchange and returns one sample.
    pub fn complete_probe(&mut self, payload: &[u8], now_ms: u64) -> Option<TransportMetricSample> {
        let outstanding = self.outstanding_probe.as_ref()?;
        if outstanding.as_slice() != payload {
            return None;
        }
        let started_at_ms = self.last_probe_at_ms?;
        self.outstanding_probe = None;
        let jitter_ms = self.jitter_ms();
        Some(TransportMetricSample::tcp_websocket(
            now_ms.saturating_sub(started_at_ms) as f64,
            jitter_ms,
        ))
    }

    fn jitter_ms(&self) -> f64 {
        if self.jitter_samples == 0 {
            0.0
        } else {
            self.jitter_total_ms as f64 / self.jitter_samples as f64
        }
    }
}
