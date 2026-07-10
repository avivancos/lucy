use crate::control::fixture::{AudioFixture, FIXTURE_PROVIDER_LABEL, FRAME_MS};
use crate::control::schema::ControlMessage;
use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::{json, Value};
use std::env;
use std::path::PathBuf;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::net::TcpStream;
use tokio_tungstenite::{
    connect_async,
    tungstenite::{self, protocol::Message},
    MaybeTlsStream, WebSocketStream,
};

pub const DEFAULT_PACING_MS: u64 = 30;
pub const DEFAULT_REPLY_TIMEOUT_MS: u64 = 5_000;
pub const DEFAULT_CONNECT_RETRY_MS: u64 = 500;
pub const DEFAULT_CONNECT_TIMEOUT_MS: u64 = 30_000;
pub const DEFAULT_CALLER_ID: &str = "fixture-caller";

type ClientSocket = WebSocketStream<MaybeTlsStream<TcpStream>>;
type SessionResult<T> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GatewayMode {
    Serve,
    SessionOneshot,
}

#[derive(Debug, Clone)]
pub struct GatewayConfig {
    pub mode: GatewayMode,
    pub session_ws_url: Option<String>,
    pub audio_fixture_wav: Option<PathBuf>,
    pub audio_fixture_timeline: Option<PathBuf>,
    pub pacing_ms: u64,
    pub reply_timeout_ms: u64,
    pub caller_id: String,
}

impl GatewayConfig {
    pub fn from_env() -> SessionResult<Self> {
        let mode = match env::var("LUCY_GATEWAY_MODE")
            .unwrap_or_else(|_| "serve".to_string())
            .as_str()
        {
            "serve" => GatewayMode::Serve,
            "session-oneshot" => GatewayMode::SessionOneshot,
            value => return Err(format!("unsupported LUCY_GATEWAY_MODE: {value}").into()),
        };
        let session_ws_url = env::var("LUCY_SESSION_WS_URL").ok();
        let audio_fixture_wav = env::var_os("LUCY_AUDIO_FIXTURE_WAV").map(PathBuf::from);
        let audio_fixture_timeline = env::var_os("LUCY_AUDIO_FIXTURE_TIMELINE").map(PathBuf::from);
        if mode == GatewayMode::SessionOneshot {
            if session_ws_url.is_none() {
                return Err("LUCY_SESSION_WS_URL is required in session mode".into());
            }
            if audio_fixture_wav.is_none() || audio_fixture_timeline.is_none() {
                return Err("audio fixture paths are required in session mode".into());
            }
        }
        Ok(Self {
            mode,
            session_ws_url,
            audio_fixture_wav,
            audio_fixture_timeline,
            pacing_ms: env_u64("LUCY_GATEWAY_PACING_MS", DEFAULT_PACING_MS)?,
            reply_timeout_ms: env_u64("LUCY_GATEWAY_REPLY_TIMEOUT_MS", DEFAULT_REPLY_TIMEOUT_MS)?,
            caller_id: env::var("LUCY_GATEWAY_CALLER_ID")
                .unwrap_or_else(|_| DEFAULT_CALLER_ID.to_string()),
        })
    }
}

fn env_u64(name: &str, default: u64) -> SessionResult<u64> {
    match env::var(name) {
        Ok(value) => Ok(value.parse()?),
        Err(env::VarError::NotPresent) => Ok(default),
        Err(error) => Err(error.into()),
    }
}

#[derive(Debug, Default, Serialize, PartialEq)]
pub struct SessionReport {
    pub session_id: String,
    pub turns: usize,
    pub tts_speak_received: usize,
    pub playback_finished: usize,
    pub marks_emitted: usize,
    pub rtt_ms_last: f64,
    pub jitter_ms_last: f64,
    pub clean_close: bool,
}

pub struct SessionClient {
    config: GatewayConfig,
    session_id: String,
    seq: u64,
}

impl SessionClient {
    pub fn new(config: GatewayConfig) -> Self {
        let epoch = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis();
        Self {
            config,
            session_id: format!("sess-gateway-{epoch}-{}", std::process::id()),
            seq: 0,
        }
    }

    pub async fn run(mut self) -> SessionResult<SessionReport> {
        let fixture = AudioFixture::load(
            self.config
                .audio_fixture_wav
                .as_deref()
                .ok_or("audio wav is required")?,
            self.config
                .audio_fixture_timeline
                .as_deref()
                .ok_or("audio timeline is required")?,
        )?;
        let mut socket = self.connect_with_retry().await?;
        let mut report = SessionReport {
            session_id: self.session_id.clone(),
            ..SessionReport::default()
        };
        self.send(
            &mut socket,
            None,
            json!({
                "type":"session.started",
                "transport":"rust-gateway",
                "caller":self.config.caller_id,
                "codecs":["pcm16/8000"],
                "features":[]
            }),
        )
        .await?;

        let mut audio_cursor_ms = 0;
        for (index, utterance) in fixture.timeline.utterances.iter().enumerate() {
            let turn_id = format!("turn_{index}");
            pace_audio_to(&mut audio_cursor_ms, utterance.start_ms).await;
            self.send(
                &mut socket,
                Some(&turn_id),
                json!({"type":"vad.speech_start","at_ms":utterance.start_ms}),
            )
            .await?;
            let mut accumulated = Vec::new();
            for (position, word) in utterance.words.iter().enumerate() {
                pace_audio_to(&mut audio_cursor_ms, word.at_ms).await;
                accumulated.push(word.text.as_str());
                self.send(
                    &mut socket,
                    Some(&turn_id),
                    json!({
                        "type":"stt.partial",
                        "text":accumulated.join(" "),
                        "stability":(position + 1) as f64 / (utterance.words.len() + 1) as f64,
                        "provider":FIXTURE_PROVIDER_LABEL
                    }),
                )
                .await?;
            }
            pace_audio_to(&mut audio_cursor_ms, utterance.end_ms).await;
            self.send(
                &mut socket,
                Some(&turn_id),
                json!({
                    "type":"vad.speech_end",
                    "at_ms":utterance.end_ms,
                    "speech_ms":utterance.end_ms - utterance.start_ms
                }),
            )
            .await?;
            self.send(
                &mut socket,
                Some(&turn_id),
                json!({
                    "type":"stt.final",
                    "text":utterance.text,
                    "provider":FIXTURE_PROVIDER_LABEL,
                    "stt_ms":0
                }),
            )
            .await?;
            report.turns += 1;
            self.finish_turn(&mut socket, &turn_id, &mut report).await?;
        }

        self.send(
            &mut socket,
            None,
            json!({"type":"session.ended","reason":"fixture_complete"}),
        )
        .await?;
        socket.close(None).await?;
        report.clean_close = true;
        Ok(report)
    }

    async fn connect_with_retry(&self) -> SessionResult<ClientSocket> {
        let url = self
            .config
            .session_ws_url
            .as_deref()
            .ok_or("session WebSocket URL is required")?;
        let started = Instant::now();
        loop {
            match connect_async(url).await {
                Ok((socket, _)) => return Ok(socket),
                Err(error)
                    if started.elapsed() < Duration::from_millis(DEFAULT_CONNECT_TIMEOUT_MS) =>
                {
                    eprintln!("gateway connection pending: {error}");
                    tokio::time::sleep(Duration::from_millis(DEFAULT_CONNECT_RETRY_MS)).await;
                }
                Err(error) => return Err(error.into()),
            }
        }
    }

    async fn finish_turn(
        &mut self,
        socket: &mut ClientSocket,
        turn_id: &str,
        report: &mut SessionReport,
    ) -> SessionResult<()> {
        let ping_started = Instant::now();
        socket.send(Message::Ping(Vec::new().into())).await?;
        let mut got_barrier = false;
        let mut got_playback = false;
        let deadline = tokio::time::sleep(Duration::from_millis(self.config.reply_timeout_ms));
        tokio::pin!(deadline);
        loop {
            tokio::select! {
                _ = &mut deadline => break,
                next = socket.next() => {
                    match next {
                        Some(Ok(Message::Pong(_))) => {
                            report.rtt_ms_last = ping_started.elapsed().as_secs_f64() * 1_000.0;
                            self.send(socket, Some(turn_id), json!({
                                "type":"transport.metrics",
                                "jitter_ms":report.jitter_ms_last,
                                "rtt_ms":report.rtt_ms_last.max(f64::EPSILON),
                                "packet_loss":0.0
                            })).await?;
                        }
                        Some(Ok(Message::Ping(payload))) => socket.send(Message::Pong(payload)).await?,
                        Some(Ok(Message::Text(text))) => {
                            let message: ControlMessage = serde_json::from_str(text.as_ref())?;
                            match message.message_type() {
                                "session.configure" => eprintln!("session configured"),
                                "tts.speak" => {
                                    report.tts_speak_received += 1;
                                    let value = message.as_value();
                                    let utterance_id = value["utterance_id"].as_str().ok_or("missing utterance_id")?;
                                    let text = value["text"].as_str().ok_or("missing tts text")?;
                                    got_barrier |= self.play_speech(
                                        socket, turn_id, utterance_id, text, report, ping_started
                                    ).await?;
                                    got_playback = true;
                                }
                                "tts.cancel" => {
                                    let utterance_id = message.as_value()["utterance_id"].as_str().unwrap_or("cancelled");
                                    self.send(socket, Some(turn_id), json!({
                                        "type":"tts.playback", "utterance_id":utterance_id,
                                        "state":"flushed", "mark_chars":0
                                    })).await?;
                                }
                                "tts.stream_end" => got_barrier = true,
                                "session.end" => return Ok(()),
                                _ => {}
                            }
                            if got_barrier && got_playback {
                                return Ok(());
                            }
                        }
                        Some(Ok(Message::Close(_))) | None => return Ok(()),
                        Some(Ok(_)) => {}
                        Some(Err(error)) => return Err(error.into()),
                    }
                }
            }
        }
        Ok(())
    }

    async fn play_speech(
        &mut self,
        socket: &mut ClientSocket,
        turn_id: &str,
        utterance_id: &str,
        text: &str,
        report: &mut SessionReport,
        ping_started: Instant,
    ) -> SessionResult<bool> {
        self.send(
            socket,
            Some(turn_id),
            json!({
                "type":"tts.playback", "utterance_id":utterance_id,
                "state":"started", "mark_chars":0
            }),
        )
        .await?;
        let marks: Vec<Value> = playback_messages(utterance_id, text, None)
            .into_iter()
            .filter(|message| message["state"] == "mark")
            .collect();
        let mut heard_chars = 0;
        let mut intervals = Vec::new();
        let mut previous = Instant::now();
        let mut barrier_seen = false;

        for mark in marks {
            let tick = tokio::time::sleep(Duration::from_millis(self.config.pacing_ms));
            tokio::pin!(tick);
            loop {
                tokio::select! {
                    _ = &mut tick => {
                        heard_chars = mark["mark_chars"].as_u64().unwrap_or(0);
                        intervals.push(previous.elapsed().as_secs_f64() * 1_000.0);
                        previous = Instant::now();
                        report.marks_emitted += 1;
                        self.send(socket, Some(turn_id), mark).await?;
                        break;
                    }
                    next = socket.next() => match next {
                        Some(Ok(Message::Text(inbound))) => {
                            let message: ControlMessage = serde_json::from_str(inbound.as_ref())?;
                            if message.message_type() == "tts.stream_end" {
                                barrier_seen = true;
                            } else if message.message_type() == "tts.cancel" {
                                let target = message.as_value()["utterance_id"].as_str();
                                if target.is_none() || target == Some(utterance_id) {
                                    self.send(socket, Some(turn_id), json!({
                                        "type":"tts.playback", "utterance_id":utterance_id,
                                        "state":"flushed", "mark_chars":heard_chars
                                    })).await?;
                                    report.jitter_ms_last = mean_absolute_deviation(
                                        &intervals, self.config.pacing_ms as f64
                                    );
                                    return Ok(barrier_seen);
                                }
                            }
                        }
                        Some(Ok(Message::Pong(_))) => {
                            report.rtt_ms_last = ping_started.elapsed().as_secs_f64() * 1_000.0;
                            self.send(socket, Some(turn_id), json!({
                                "type":"transport.metrics",
                                "jitter_ms":report.jitter_ms_last,
                                "rtt_ms":report.rtt_ms_last.max(f64::EPSILON),
                                "packet_loss":0.0
                            })).await?;
                        }
                        Some(Ok(Message::Ping(payload))) => socket.send(Message::Pong(payload)).await?,
                        Some(Ok(Message::Close(_))) | None => return Ok(barrier_seen),
                        Some(Ok(_)) => {}
                        Some(Err(error)) => return Err(error.into()),
                    }
                }
            }
        }
        self.send(
            socket,
            Some(turn_id),
            json!({
                "type":"tts.playback", "utterance_id":utterance_id,
                "state":"finished", "mark_chars":text.chars().count()
            }),
        )
        .await?;
        report.playback_finished += 1;
        report.jitter_ms_last = mean_absolute_deviation(&intervals, self.config.pacing_ms as f64);
        Ok(barrier_seen)
    }

    async fn send(
        &mut self,
        socket: &mut ClientSocket,
        turn_id: Option<&str>,
        payload: Value,
    ) -> Result<(), tungstenite::Error> {
        let object = payload.as_object().expect("control payload object");
        let mut wire = object.clone();
        wire.insert("v".to_string(), json!(1));
        wire.insert("session_id".to_string(), json!(self.session_id));
        wire.insert("turn_id".to_string(), json!(turn_id));
        wire.insert("seq".to_string(), json!(self.seq));
        wire.insert("ts_ms".to_string(), json!(now_ms()));
        self.seq += 1;
        socket
            .send(Message::Text(serde_json::to_string(&wire).unwrap().into()))
            .await
    }
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

async fn pace_audio_to(cursor_ms: &mut u64, target_ms: u64) {
    while *cursor_ms < target_ms {
        let step_ms = FRAME_MS.min(target_ms - *cursor_ms);
        tokio::time::sleep(Duration::from_millis(step_ms)).await;
        *cursor_ms += step_ms;
    }
}

fn playback_messages(utterance_id: &str, text: &str, cancel_after: Option<usize>) -> Vec<Value> {
    let mut output = vec![json!({
        "type":"tts.playback", "utterance_id":utterance_id,
        "state":"started", "mark_chars":0
    })];
    let mut search_byte = 0;
    for (index, word) in text.split_whitespace().enumerate() {
        if let Some(limit) = cancel_after {
            if index >= limit {
                output.push(json!({
                    "type":"tts.playback", "utterance_id":utterance_id,
                    "state":"flushed", "mark_chars":text[..search_byte].chars().count()
                }));
                return output;
            }
        }
        let relative = text[search_byte..].find(word).unwrap_or(0);
        search_byte += relative + word.len();
        output.push(json!({
            "type":"tts.playback", "utterance_id":utterance_id,
            "state":"mark", "mark_chars":text[..search_byte].chars().count()
        }));
    }
    output.push(json!({
        "type":"tts.playback", "utterance_id":utterance_id,
        "state":"finished", "mark_chars":text.chars().count()
    }));
    output
}

fn mean_absolute_deviation(samples: &[f64], expected: f64) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    samples
        .iter()
        .map(|sample| (sample - expected).abs())
        .sum::<f64>()
        / samples.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

    fn fixture_paths() -> (PathBuf, PathBuf) {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/audio");
        let wav = std::env::var_os("LUCY_AUDIO_FIXTURE_WAV")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("booking_caller_8k.wav"));
        let timeline = std::env::var_os("LUCY_AUDIO_FIXTURE_TIMELINE")
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join("booking_caller.timeline.json"));
        (wav, timeline)
    }

    fn server_message(kind: &str, turn_id: Option<&str>, payload: Value) -> Message {
        let mut wire = payload.as_object().unwrap().clone();
        wire.insert("v".to_string(), json!(1));
        wire.insert("type".to_string(), json!(kind));
        wire.insert("session_id".to_string(), json!("sess-server"));
        wire.insert("turn_id".to_string(), json!(turn_id));
        wire.insert("seq".to_string(), json!(1));
        wire.insert("ts_ms".to_string(), json!(1_700_000_000_000_u64));
        Message::Text(serde_json::to_string(&wire).unwrap().into())
    }

    #[tokio::test]
    async fn session_client_sends_session_started_first_then_streams_fixture() {
        let listener = TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let first = socket.next().await.unwrap().unwrap().into_text().unwrap();
            let first: Value = serde_json::from_str(first.as_ref()).unwrap();
            assert_eq!(first["type"], "session.started");
            socket
                .send(server_message(
                    "session.configure",
                    None,
                    json!({"stt":"fixture","tts":"fixture","vad":"server"}),
                ))
                .await
                .unwrap();

            let mut finals = 0;
            while let Some(message) = socket.next().await {
                match message.unwrap() {
                    Message::Ping(payload) => socket.send(Message::Pong(payload)).await.unwrap(),
                    Message::Text(text) => {
                        let value: Value = serde_json::from_str(text.as_ref()).unwrap();
                        if value["type"] == "stt.final" {
                            finals += 1;
                            let turn_id = value["turn_id"].as_str().unwrap();
                            socket
                                .send(server_message(
                                    "tts.speak",
                                    Some(turn_id),
                                    json!({
                                        "utterance_id":format!("utt-{finals}"),
                                        "text":format!("Reply {finals}"),
                                        "flush":true
                                    }),
                                ))
                                .await
                                .unwrap();
                            socket
                                .send(server_message("tts.stream_end", Some(turn_id), json!({})))
                                .await
                                .unwrap();
                        } else if value["type"] == "session.ended" {
                            break;
                        }
                    }
                    Message::Close(_) => break,
                    _ => {}
                }
            }
            finals
        });

        let (wav, timeline) = fixture_paths();
        let config = GatewayConfig {
            mode: GatewayMode::SessionOneshot,
            session_ws_url: Some(format!("{}://{address}", "ws")),
            audio_fixture_wav: Some(wav),
            audio_fixture_timeline: Some(timeline),
            pacing_ms: 1,
            reply_timeout_ms: 500,
            caller_id: DEFAULT_CALLER_ID.to_string(),
        };
        let report = SessionClient::new(config).run().await.unwrap();
        assert_eq!(server.await.unwrap(), 2);
        assert_eq!(report.turns, 2);
        assert_eq!(report.tts_speak_received, 2);
        assert_eq!(report.playback_finished, 2);
        assert!(report.rtt_ms_last > 0.0);
        assert!(report.clean_close);
    }

    #[test]
    fn tts_speak_yields_started_marks_finished_with_advancing_mark_chars() {
        let messages = playback_messages("utt-1", "Hello brave world", None);
        assert_eq!(messages.first().unwrap()["state"], "started");
        assert_eq!(messages.last().unwrap()["state"], "finished");
        let marks: Vec<_> = messages
            .iter()
            .filter(|message| message["state"] == "mark")
            .map(|message| message["mark_chars"].as_u64().unwrap())
            .collect();
        assert!(marks.windows(2).all(|pair| pair[0] < pair[1]));
        assert_eq!(messages.last().unwrap()["mark_chars"], 17);
    }

    #[test]
    fn tts_cancel_mid_playback_emits_flushed_with_partial_mark_chars() {
        let messages = playback_messages("utt-1", "Hello brave world", Some(1));
        assert_eq!(messages.last().unwrap()["state"], "flushed");
        assert_eq!(messages.last().unwrap()["mark_chars"], 5);
    }

    #[test]
    fn tts_marks_count_unicode_scalars_without_panicking() {
        let messages = playback_messages("utt-1", "Héllo 世界", None);
        let marks: Vec<_> = messages
            .iter()
            .filter(|message| message["state"] == "mark")
            .map(|message| message["mark_chars"].as_u64().unwrap())
            .collect();
        assert_eq!(marks, vec![5, 8]);
        assert_eq!(messages.last().unwrap()["mark_chars"], 8);
    }

    #[test]
    fn transport_metrics_reports_measured_jitter() {
        assert_eq!(
            mean_absolute_deviation(&[28.0, 30.0, 35.0], 30.0),
            7.0 / 3.0
        );
    }
}
