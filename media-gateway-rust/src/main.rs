use axum::{routing::get, Json, Router};
use lucy_media_gateway::control::session::{GatewayConfig, GatewayMode, SessionClient};
use serde::Serialize;
use std::net::SocketAddr;

#[derive(Serialize)]
struct Health {
    service: &'static str,
    status: &'static str,
}

async fn health() -> Json<Health> {
    Json(Health {
        service: "lucy-media-gateway",
        status: "ok",
    })
}

#[tokio::main]
async fn main() {
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

    let app = Router::new().route("/health", get(health));
    let addr = SocketAddr::from(([0, 0, 0, 0], 8081));
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
