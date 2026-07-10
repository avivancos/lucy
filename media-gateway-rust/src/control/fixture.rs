use hound::{SampleFormat, WavReader};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{fs, path::Path};

pub const FRAME_MS: u64 = 20;
pub const FIXTURE_PROVIDER_LABEL: &str = "fixture";

#[derive(Debug, Deserialize)]
pub struct Timeline {
    pub wav: String,
    pub utterances: Vec<Utterance>,
}

#[derive(Debug, Deserialize)]
pub struct Utterance {
    pub text: String,
    pub start_ms: u64,
    pub end_ms: u64,
    pub words: Vec<Word>,
}

#[derive(Debug, Deserialize)]
pub struct Word {
    pub text: String,
    pub at_ms: u64,
}

pub struct AudioFixture {
    pub timeline: Timeline,
    pub duration_ms: u64,
}

impl AudioFixture {
    pub fn load(wav: &Path, timeline: &Path) -> Result<Self, String> {
        let reader = WavReader::open(wav).map_err(|error| error.to_string())?;
        let spec = reader.spec();
        if spec.sample_rate != 8_000
            || spec.channels != 1
            || spec.bits_per_sample != 16
            || spec.sample_format != SampleFormat::Int
        {
            return Err("audio fixture must be 8kHz mono PCM16".to_string());
        }
        let duration_ms = reader.duration() as u64 * 1_000 / spec.sample_rate as u64;
        let parsed: Timeline =
            serde_json::from_str(&fs::read_to_string(timeline).map_err(|error| error.to_string())?)
                .map_err(|error| error.to_string())?;
        if parsed.wav != wav.file_name().and_then(|name| name.to_str()).unwrap_or("") {
            return Err("timeline wav name does not match fixture".to_string());
        }
        let mut previous = 0;
        for utterance in &parsed.utterances {
            if utterance.start_ms >= utterance.end_ms || utterance.end_ms > duration_ms {
                return Err("timeline utterance is outside wav duration".to_string());
            }
            for word in &utterance.words {
                if word.at_ms <= previous || word.at_ms >= utterance.end_ms {
                    return Err("timeline word offsets must increase".to_string());
                }
                previous = word.at_ms;
            }
        }
        Ok(Self {
            timeline: parsed,
            duration_ms,
        })
    }

    pub async fn stream_control_events(&self) -> Vec<Value> {
        let mut events = Vec::new();
        let frame_count = self.duration_ms.div_ceil(FRAME_MS);
        for _ in 0..frame_count {
            tokio::time::sleep(std::time::Duration::from_millis(FRAME_MS)).await;
        }
        for utterance in &self.timeline.utterances {
            events.push(json!({"type":"vad.speech_start","at_ms":utterance.start_ms}));
            let mut accumulated = Vec::new();
            for (index, word) in utterance.words.iter().enumerate() {
                accumulated.push(word.text.as_str());
                events.push(json!({
                    "type":"stt.partial",
                    "text":accumulated.join(" "),
                    "stability":(index + 1) as f64 / (utterance.words.len() + 1) as f64,
                    "provider":FIXTURE_PROVIDER_LABEL
                }));
            }
            events.push(json!({
                "type":"vad.speech_end",
                "at_ms":utterance.end_ms,
                "speech_ms":utterance.end_ms - utterance.start_ms
            }));
            events.push(json!({
                "type":"stt.final",
                "text":utterance.text,
                "provider":FIXTURE_PROVIDER_LABEL,
                "stt_ms":0
            }));
        }
        events
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn fixtures() -> (PathBuf, PathBuf) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/audio");
        let wav = std::env::var_os("LUCY_AUDIO_FIXTURE_WAV")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("booking_caller_8k.wav"));
        let timeline = std::env::var_os("LUCY_AUDIO_FIXTURE_TIMELINE")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("booking_caller.timeline.json"));
        (wav, timeline)
    }

    #[tokio::test(start_paused = true)]
    async fn timeline_emits_rising_stability_partials_then_final() {
        let (wav, timeline) = fixtures();
        let events = AudioFixture::load(&wav, &timeline)
            .unwrap()
            .stream_control_events()
            .await;
        let partials: Vec<_> = events
            .iter()
            .filter(|event| event["type"] == "stt.partial")
            .collect();
        assert!(partials.windows(2).all(|pair| {
            pair[0]["stability"].as_f64().unwrap() < pair[1]["stability"].as_f64().unwrap()
                || pair[1]["text"] == "Tuesday"
        }));
        assert_eq!(
            events
                .iter()
                .filter(|event| event["type"] == "stt.final")
                .count(),
            2
        );
    }

    #[test]
    fn loader_rejects_non_8k_mono_wav() {
        let path = std::env::temp_dir().join("lucy-invalid-audio.wav");
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: 16_000,
            bits_per_sample: 16,
            sample_format: SampleFormat::Int,
        };
        hound::WavWriter::create(&path, spec)
            .unwrap()
            .finalize()
            .unwrap();
        let (_, timeline) = fixtures();
        assert!(AudioFixture::load(&path, &timeline).is_err());
        let _ = std::fs::remove_file(path);
    }

    #[tokio::test(start_paused = true)]
    async fn frames_pace_internally_and_never_become_control_messages() {
        let (wav, timeline) = fixtures();
        let fixture = AudioFixture::load(&wav, &timeline).unwrap();
        let started = tokio::time::Instant::now();
        let events = fixture.stream_control_events().await;
        assert!(started.elapsed().as_millis() >= fixture.duration_ms as u128);
        assert!(events.iter().all(|event| event.get("audio").is_none()));
    }
}
