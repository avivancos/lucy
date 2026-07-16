use axum::{extract::State, routing::get, Json, Router};
use lucy_media_gateway::asterisk::runtime::{
    run_ari_external_media_session, serve_transport, AriRuntimeConfig, AsteriskRuntimeConfig,
};
use lucy_media_gateway::asterisk::server::{GatewayMetrics, GatewayMetricsSnapshot};
use lucy_media_gateway::control::session::{GatewayConfig, GatewayMode, SessionClient};
use lucy_media_gateway::cpaas::runtime::{serve_cpaas, CpaasRuntimeConfig};
use serde::Serialize;
use std::env;
use std::io::{Read, Write};
use std::net::SocketAddr;
use std::time::Duration;

const HEALTH_BIND_ENV: &str = "LUCY_GATEWAY_HEALTH_BIND";
const CPAAS_PROVIDER_ENV: &str = "LUCY_CPAAAS_PROVIDER";
const DEFAULT_HEALTH_BIND: &str = "0.0.0.0:8081";
const HEALTHCHECK_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Serialize)]
struct Health {
    service: &'static str,
    status: &'static str,
    #[serde(flatten)]
    metrics: GatewayMetricsSnapshot,
}

async fn health(State(metrics): State<GatewayMetrics>) -> Json<Health> {
    Json(Health {
        service: "lucy-media-gateway",
        status: "ok",
        metrics: metrics.snapshot(),
    })
}

fn health_address() -> Result<SocketAddr, String> {
    env::var(HEALTH_BIND_ENV)
        .unwrap_or_else(|_| DEFAULT_HEALTH_BIND.to_string())
        .parse()
        .map_err(|_| format!("{HEALTH_BIND_ENV} must be a valid socket address"))
}

fn run_healthcheck() -> Result<(), String> {
    let mut address = health_address()?;
    if address.ip().is_unspecified() {
        address.set_ip(std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST));
    }
    let mut stream = std::net::TcpStream::connect_timeout(&address, HEALTHCHECK_TIMEOUT)
        .map_err(|error| format!("healthcheck connect failed: {error}"))?;
    stream
        .set_read_timeout(Some(HEALTHCHECK_TIMEOUT))
        .map_err(|error| format!("healthcheck timeout setup failed: {error}"))?;
    stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .map_err(|error| format!("healthcheck request failed: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("healthcheck response failed: {error}"))?;
    if !response.starts_with("HTTP/1.1 200") || !response.contains("\"status\":\"ok\"") {
        return Err("healthcheck returned an unhealthy response".to_string());
    }
    Ok(())
}

async fn serve_gateway() -> Result<(), String> {
    let health_address = health_address()?;
    let health_listener = tokio::net::TcpListener::bind(health_address)
        .await
        .map_err(|error| format!("bind health listener failed: {error}"))?;
    let metrics = GatewayMetrics::default();
    let app = Router::new()
        .route("/health", get(health))
        .with_state(metrics.clone());

    if nonblank_env(CPAAS_PROVIDER_ENV) {
        let transport_config = CpaasRuntimeConfig::from_env().map_err(|error| error.to_string())?;
        tokio::select! {
            result = serve_cpaas(transport_config) => {
                result.map_err(|error| error.to_string())
            }
            result = axum::serve(health_listener, app) => {
                result.map_err(|error| format!("serve health endpoint failed: {error}"))
            }
        }
    } else {
        let transport_config =
            AsteriskRuntimeConfig::from_env().map_err(|error| error.to_string())?;
        tokio::select! {
            result = serve_transport(transport_config, metrics.clone()) => {
                result.map_err(|error| error.to_string())
            }
            result = axum::serve(health_listener, app) => {
                result.map_err(|error| format!("serve health endpoint failed: {error}"))
            }
        }
    }
}

fn nonblank_env(name: &str) -> bool {
    env::var_os(name).is_some_and(|value| !value.to_string_lossy().trim().is_empty())
}

#[tokio::main]
async fn main() {
    if env::args().nth(1).as_deref() == Some("healthcheck") {
        if let Err(error) = run_healthcheck() {
            eprintln!("{error}");
            std::process::exit(1);
        }
        return;
    }
    let command = env::args().nth(1);
    if command.as_deref() == Some("ari-session") {
        let Some(caller_channel_id) = env::args().nth(2) else {
            eprintln!("ari-session requires a caller channel id");
            std::process::exit(1);
        };
        let config = match AriRuntimeConfig::from_env() {
            Ok(config) => config,
            Err(error) => {
                eprintln!("ARI runtime configuration failed: {error}");
                std::process::exit(1);
            }
        };
        match run_ari_external_media_session(&config, &caller_channel_id).await {
            Ok(report) => {
                println!(
                    "{}",
                    serde_json::to_string(&report).expect("serializable ARI report")
                );
                return;
            }
            Err(error) => {
                eprintln!("ARI externalMedia session failed: {error}");
                std::process::exit(1);
            }
        }
    }
    let config = match GatewayConfig::from_env() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("gateway configuration failed: {error}");
            std::process::exit(1);
        }
    };
    if config.mode == GatewayMode::SessionOneshot {
        match SessionClient::new(config).run().await {
            Ok(report) => {
                let value = serde_json::to_value(report).expect("serializable session report");
                println!(
                    "{}",
                    serde_json::to_string(&value).expect("canonical report")
                );
                return;
            }
            Err(error) => {
                eprintln!("gateway session failed: {error}");
                std::process::exit(1);
            }
        }
    }

    if let Err(error) = serve_gateway().await {
        eprintln!("gateway serve mode failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::nonblank_env;

    #[test]
    fn blank_compose_value_does_not_select_cpaas_runtime() {
        const NAME: &str = "LUCY_TEST_CPAAAS_PROVIDER_SELECTION";
        std::env::set_var(NAME, "   ");
        assert!(!nonblank_env(NAME));
        std::env::set_var(NAME, "telnyx");
        assert!(nonblank_env(NAME));
        std::env::remove_var(NAME);
        assert!(!nonblank_env(NAME));
    }
}
