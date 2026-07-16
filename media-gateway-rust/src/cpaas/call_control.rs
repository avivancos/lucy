use super::CpaasProvider;
use futures_util::StreamExt;
use reqwest::{Client, Response};
use std::fmt;
use std::net::IpAddr;
use std::time::Duration;
use url::Url;

const MAX_SECRET_CHARS: usize = 4_000;
const MAX_CALL_ID_CHARS: usize = 512;
const MAX_RESPONSE_BYTES: usize = 65_536;
const TWILIO_STATUS_COMPLETED_BODY: &str = "Status=completed";

#[derive(Debug, Clone)]
pub struct CpaasCallControlConfig {
    provider: CpaasProvider,
    base_url: Url,
    account_id: Option<String>,
    api_key: String,
    timeout: Duration,
}

impl CpaasCallControlConfig {
    pub fn new(
        provider: CpaasProvider,
        base_url: &str,
        account_id: Option<&str>,
        api_key: &str,
        allow_insecure_loopback: bool,
        timeout: Duration,
    ) -> Result<Self, CpaasCallControlError> {
        let mut base_url = Url::parse(base_url)
            .map_err(|_| CpaasCallControlError::config("call-control URL is invalid"))?;
        if !matches!(base_url.scheme(), "http" | "https") {
            return Err(CpaasCallControlError::config(
                "call-control URL must use http or https",
            ));
        }
        if !base_url.username().is_empty()
            || base_url.password().is_some()
            || base_url.query().is_some()
            || base_url.fragment().is_some()
        {
            return Err(CpaasCallControlError::config(
                "call-control URL cannot contain credentials, query, or fragment",
            ));
        }
        let loopback = base_url.host_str().is_some_and(|host| {
            host.eq_ignore_ascii_case("localhost")
                || host
                    .parse::<IpAddr>()
                    .is_ok_and(|address| address.is_loopback())
        });
        if base_url.scheme() == "http" && !(allow_insecure_loopback && loopback) {
            return Err(CpaasCallControlError::config(
                "call-control URL must use https outside local protocol tests",
            ));
        }
        if timeout.is_zero() {
            return Err(CpaasCallControlError::config(
                "call-control timeout must be positive",
            ));
        }
        validate_secret(api_key, "API key")?;
        let account_id = account_id
            .map(|value| {
                validate_identifier(value, "account id")?;
                Ok(value.to_string())
            })
            .transpose()?;
        if provider == CpaasProvider::Twilio && account_id.is_none() {
            return Err(CpaasCallControlError::config(
                "Twilio account id is required",
            ));
        }
        let normalized_path = base_url.path().trim_end_matches('/').to_string();
        base_url.set_path(&normalized_path);
        Ok(Self {
            provider,
            base_url,
            account_id,
            api_key: api_key.to_string(),
            timeout,
        })
    }
}

#[derive(Clone)]
pub struct CpaasCallControlClient {
    config: CpaasCallControlConfig,
    client: Client,
}

impl CpaasCallControlClient {
    pub fn new(config: CpaasCallControlConfig) -> Result<Self, CpaasCallControlError> {
        let client = Client::builder()
            .timeout(config.timeout)
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| CpaasCallControlError::config("build call-control HTTP client failed"))?;
        Ok(Self { config, client })
    }

    pub async fn hangup(&self, call_id: &str) -> Result<(), CpaasCallControlError> {
        validate_identifier(call_id, "call id")?;
        let response = match self.config.provider {
            CpaasProvider::Telnyx => {
                let url = self.url_with_segments(&["v2", "calls", call_id, "actions", "hangup"])?;
                self.client
                    .post(url)
                    .bearer_auth(&self.config.api_key)
                    .json(&serde_json::json!({}))
                    .send()
                    .await
            }
            CpaasProvider::Twilio => {
                let account_id = self
                    .config
                    .account_id
                    .as_deref()
                    .expect("validated Twilio account id");
                let call_resource = format!("{call_id}.json");
                let url = self.url_with_segments(&[
                    "2010-04-01",
                    "Accounts",
                    account_id,
                    "Calls",
                    &call_resource,
                ])?;
                self.client
                    .post(url)
                    .basic_auth(account_id, Some(&self.config.api_key))
                    .header(
                        reqwest::header::CONTENT_TYPE,
                        "application/x-www-form-urlencoded",
                    )
                    .body(TWILIO_STATUS_COMPLETED_BODY)
                    .send()
                    .await
            }
        }
        .map_err(|_| CpaasCallControlError::operation("call-control request failed"))?;
        validate_response(response).await
    }

    fn url_with_segments(&self, segments: &[&str]) -> Result<Url, CpaasCallControlError> {
        let mut url = self.config.base_url.clone();
        {
            let mut path = url.path_segments_mut().map_err(|_| {
                CpaasCallControlError::config("call-control URL cannot be a base URL")
            })?;
            path.pop_if_empty();
            for segment in segments {
                path.push(segment);
            }
        }
        Ok(url)
    }
}

async fn validate_response(response: Response) -> Result<(), CpaasCallControlError> {
    if !response.status().is_success() {
        return Err(CpaasCallControlError::operation(format!(
            "call-control request returned HTTP {}",
            response.status().as_u16()
        )));
    }
    let mut size = 0usize;
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk
            .map_err(|_| CpaasCallControlError::operation("read call-control response failed"))?;
        size = size.checked_add(chunk.len()).ok_or_else(|| {
            CpaasCallControlError::operation("call-control response is too large")
        })?;
        if size > MAX_RESPONSE_BYTES {
            return Err(CpaasCallControlError::operation(
                "call-control response is too large",
            ));
        }
    }
    Ok(())
}

fn validate_secret(value: &str, label: &str) -> Result<(), CpaasCallControlError> {
    if value.trim().is_empty()
        || value.len() > MAX_SECRET_CHARS
        || value
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
    {
        return Err(CpaasCallControlError::config(
            format!("{label} is invalid",),
        ));
    }
    Ok(())
}

fn validate_identifier(value: &str, label: &str) -> Result<(), CpaasCallControlError> {
    if value.trim().is_empty()
        || value.len() > MAX_CALL_ID_CHARS
        || value.chars().any(char::is_control)
    {
        return Err(CpaasCallControlError::config(
            format!("{label} is invalid",),
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CpaasCallControlError(String);

impl CpaasCallControlError {
    fn config(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    fn operation(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for CpaasCallControlError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for CpaasCallControlError {}
