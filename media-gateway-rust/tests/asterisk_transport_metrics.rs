use lucy_media_gateway::asterisk::metrics::{
    TransportMetricSample, TransportMetricsSampler, DEFAULT_METRICS_EMIT_INTERVAL_MS,
};

#[test]
fn pcm_jitter_and_media_websocket_rtt_are_measured_and_coalesced() {
    let mut sampler = TransportMetricsSampler::new(DEFAULT_METRICS_EMIT_INTERVAL_MS);

    sampler.observe_pcm(1_000, 20);
    let initial_ping = sampler.begin_probe(1_000).unwrap();
    assert_eq!(
        sampler.complete_probe(&initial_ping, 1_007).unwrap().rtt_ms,
        7.0
    );
    sampler.observe_pcm(1_025, 20);
    assert!(
        sampler.begin_probe(1_025).is_none(),
        "metrics are not per frame"
    );

    let ping = sampler
        .begin_probe(1_000 + DEFAULT_METRICS_EMIT_INTERVAL_MS)
        .unwrap();
    let sample = sampler
        .complete_probe(&ping, 1_000 + DEFAULT_METRICS_EMIT_INTERVAL_MS + 7)
        .expect("the matching pong produces one measured sample");
    assert_eq!(sample.rtt_ms, 7.0);
    assert_eq!(sample.jitter_ms, 5.0);
    assert_eq!(sample.packet_loss, 0.0);
    assert!(sampler
        .complete_probe(&ping, 1_000 + DEFAULT_METRICS_EMIT_INTERVAL_MS + 8)
        .is_none());
    assert!(sampler
        .begin_probe(2_000 + DEFAULT_METRICS_EMIT_INTERVAL_MS)
        .is_some());
}

#[test]
fn audiosocket_reports_jitter_with_explicitly_unavailable_rtt() {
    let mut sampler = TransportMetricsSampler::new(DEFAULT_METRICS_EMIT_INTERVAL_MS);
    sampler.observe_pcm(1_000, 20);
    sampler.observe_pcm(1_025, 20);

    let sample = sampler.sample_without_rtt(1_025).unwrap();
    assert_eq!(sample.rtt_ms, 0.0);
    assert_eq!(sample.jitter_ms, 5.0);
    assert_eq!(sample.packet_loss, 0.0);
    assert!(sampler.sample_without_rtt(1_026).is_none());
}

#[test]
fn an_unmatched_pong_cannot_create_a_zero_only_metric_sample() {
    let mut sampler = TransportMetricsSampler::new(DEFAULT_METRICS_EMIT_INTERVAL_MS);
    sampler.observe_pcm(100, 20);
    let ping = sampler.begin_probe(100).unwrap();

    assert_eq!(
        sampler.complete_probe(b"different-ping", 111),
        None,
        "only a matching ping/pong pair may report RTT"
    );
    assert_eq!(sampler.complete_probe(&ping, 111).unwrap().rtt_ms, 11.0);
}

#[test]
fn metric_sample_is_not_constructible_as_a_non_tcp_loss_estimate() {
    let sample = TransportMetricSample::tcp_websocket(3.0, 2.0);
    assert_eq!(sample.packet_loss, 0.0);
}
