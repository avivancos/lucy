use lucy_media_gateway::asterisk::media_plane::{FixtureMediaPlane, MediaPlaneEvent, PlaybackStep};
use lucy_media_gateway::asterisk::provider_media::{
    resample_pcm_s16le, DeepgramConfig, ElevenLabsConfig, ProviderMediaConfig, ProviderMediaPlane,
};
use std::path::PathBuf;
use std::time::Duration;

fn fixtures() -> (PathBuf, PathBuf) {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/audio");
    (
        std::env::var("LUCY_AUDIO_FIXTURE_WAV")
            .map(PathBuf::from)
            .unwrap_or_else(|_| root.join("booking_caller_8k.wav")),
        std::env::var("LUCY_AUDIO_FIXTURE_TIMELINE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| root.join("booking_caller.timeline.json")),
    )
}

#[test]
fn recorded_media_plane_emits_transcript_only_after_bounded_audio() {
    let (wav, timeline) = fixtures();
    let mut media = FixtureMediaPlane::load(&wav, &timeline, 8, 4).unwrap();

    assert!(media.push_audio(&[0, 0, 1, 0]).unwrap().is_empty());
    let events = media.push_audio(&[2, 0, 3, 0]).unwrap();

    assert_eq!(
        events,
        vec![
            MediaPlaneEvent::SpeechStarted { at_ms: 0 },
            MediaPlaneEvent::TranscriptPartial {
                text: "I want to book a demo.".to_string(),
                stability: 1.0,
                at_ms: 1_800,
                provider: "fixture",
            },
            MediaPlaneEvent::SpeechEnded {
                at_ms: 1_800,
                speech_ms: 1_800,
            },
            MediaPlaneEvent::TranscriptFinal {
                text: "I want to book a demo.".to_string(),
                stt_ms: 0,
                at_ms: 1_800,
                provider: "fixture",
            },
        ]
    );
    assert!(media.push_audio(&[4, 0]).unwrap().is_empty());
}

#[test]
fn playback_streams_recorded_pcm_and_cancel_flushes_active_utterance() {
    let (wav, timeline) = fixtures();
    let mut media = FixtureMediaPlane::load(&wav, &timeline, 2, 640).unwrap();

    assert_eq!(
        media.start_playback("utt-1", "Hello from Lucy.").unwrap(),
        MediaPlaneEvent::PlaybackStarted {
            utterance_id: "utt-1".to_string(),
        }
    );
    let PlaybackStep::Audio(first) = media.next_playback().unwrap() else {
        panic!("expected recorded PCM");
    };
    assert_eq!(first.len(), 640);
    assert_eq!(
        media.cancel_playback("utt-1").unwrap(),
        MediaPlaneEvent::PlaybackFlushed {
            utterance_id: "utt-1".to_string(),
            mark_chars: 0,
        }
    );
    assert_eq!(media.next_playback().unwrap(), PlaybackStep::Idle);
}

#[test]
fn playback_finishes_with_exact_recorded_pcm_and_rejects_invalid_commands() {
    let (wav, timeline) = fixtures();
    let expected = hound::WavReader::open(&wav)
        .unwrap()
        .into_samples::<i16>()
        .flat_map(|sample| sample.unwrap().to_le_bytes())
        .collect::<Vec<_>>();
    let mut media = FixtureMediaPlane::load(&wav, &timeline, 2, 640).unwrap();

    assert!(media.start_playback("", "hello").is_err());
    assert!(media.start_playback("utt-1", " ").is_err());
    media.start_playback("utt-1", "hello").unwrap();
    assert!(media.start_playback("utt-2", "overlap").is_err());

    let mut actual = Vec::new();
    loop {
        match media.next_playback().unwrap() {
            PlaybackStep::Audio(chunk) => actual.extend(chunk),
            PlaybackStep::Finished(event) => {
                assert_eq!(
                    event,
                    MediaPlaneEvent::PlaybackFinished {
                        utterance_id: "utt-1".to_string(),
                        mark_chars: 5,
                    }
                );
                break;
            }
            PlaybackStep::Idle => panic!("playback ended without a finished event"),
        }
    }
    assert_eq!(actual, expected);
    assert!(media.cancel_playback("utt-1").is_err());
}

#[test]
fn media_plane_rejects_unbounded_or_malformed_fixture_configuration() {
    let (wav, timeline) = fixtures();
    assert!(FixtureMediaPlane::load(&wav, &timeline, 0, 640).is_err());
    assert!(FixtureMediaPlane::load(&wav, &timeline, 2, 0).is_err());
    assert!(FixtureMediaPlane::load(&wav, &timeline, 2, u16::MAX as usize + 1).is_err());
}

#[test]
fn provider_configuration_requires_secure_urls_and_redacts_credentials() {
    let secret = "do-not-log-me";
    let config = ProviderMediaConfig::new(
        DeepgramConfig::new(
            "ws://deepgram.example/listen",
            "nova-3",
            secret,
            16_000,
            640,
            300,
            Duration::from_millis(10),
            Duration::from_millis(10),
        ),
        ElevenLabsConfig::new(
            "wss://eleven.example",
            "flash-v2.5",
            "voice-1",
            secret,
            "pcm_16000",
            16_000,
            Duration::from_millis(10),
            Duration::from_millis(10),
        ),
    );
    assert!(config.is_err());

    let config = ProviderMediaConfig::local_protocol_test(
        DeepgramConfig::new(
            "ws://127.0.0.1:9001/listen",
            "nova-3",
            secret,
            16_000,
            640,
            300,
            Duration::from_millis(10),
            Duration::from_millis(10),
        ),
        ElevenLabsConfig::new(
            "ws://127.0.0.1:9002",
            "flash-v2.5",
            "voice-1",
            secret,
            "pcm_16000",
            16_000,
            Duration::from_millis(10),
            Duration::from_millis(10),
        ),
    )
    .unwrap();
    assert!(!format!("{config:?}").contains(secret));
}

#[test]
fn provider_configuration_rejects_an_excessive_stt_startup_buffer() {
    let config = ProviderMediaConfig::local_protocol_test(
        DeepgramConfig::new(
            "ws://127.0.0.1:9001/listen",
            "nova-3",
            "stt-secret",
            16_000,
            2,
            300,
            Duration::from_secs(1),
            Duration::from_secs(1),
        ),
        ElevenLabsConfig::new(
            "ws://127.0.0.1:9002",
            "flash-v2.5",
            "voice-1",
            "tts-secret",
            "pcm_16000",
            16_000,
            Duration::from_secs(1),
            Duration::from_secs(1),
        ),
    );
    assert_eq!(
        config.unwrap_err().to_string(),
        "Deepgram connect timeout and framing require an excessive startup buffer"
    );
}

#[test]
fn provider_resampling_is_deterministic_for_asterisk_slin_rates() {
    let source = [0_i16, 10_000, -10_000, 20_000]
        .into_iter()
        .flat_map(i16::to_le_bytes)
        .collect::<Vec<_>>();
    assert_eq!(
        resample_pcm_s16le(&source, 8_000, 16_000).unwrap().len(),
        source.len() * 2
    );
    assert_eq!(
        resample_pcm_s16le(&source, 16_000, 8_000).unwrap(),
        vec![136, 19, 136, 19]
    );
    assert!(resample_pcm_s16le(&[1], 8_000, 16_000).is_err());
}

#[test]
fn provider_plane_rejects_blank_tts_before_connecting() {
    let config = ProviderMediaConfig::local_protocol_test(
        DeepgramConfig::new(
            "ws://127.0.0.1:9",
            "nova-3",
            "stt-secret",
            16_000,
            640,
            300,
            Duration::from_millis(10),
            Duration::from_millis(10),
        ),
        ElevenLabsConfig::new(
            "ws://127.0.0.1:9",
            "flash-v2.5",
            "voice-1",
            "tts-secret",
            "pcm_16000",
            16_000,
            Duration::from_millis(10),
            Duration::from_millis(10),
        ),
    )
    .unwrap();
    let mut plane = ProviderMediaPlane::new(config);
    let error = plane.start_playback("utt-1", " ", true).unwrap_err();
    assert!(!error.to_string().contains("tts-secret"));
}
