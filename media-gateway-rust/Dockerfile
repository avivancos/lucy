FROM rust:1.82-bookworm AS builder

WORKDIR /app
COPY Cargo.toml ./
COPY Cargo.lock ./
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim

WORKDIR /app
RUN groupadd --system lucy && useradd --system --gid lucy --no-create-home lucy
COPY --from=builder /app/target/release/lucy-media-gateway /usr/local/bin/lucy-media-gateway

EXPOSE 8081 9092 9093 9094/udp

HEALTHCHECK --interval=2s --timeout=2s --retries=15 \
  CMD ["lucy-media-gateway", "healthcheck"]

USER lucy

CMD ["lucy-media-gateway"]
