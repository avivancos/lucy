FROM rust:1.82-slim AS builder

WORKDIR /app
COPY Cargo.toml ./
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim

WORKDIR /app
COPY --from=builder /app/target/release/lucy-media-gateway /usr/local/bin/lucy-media-gateway

EXPOSE 8081

CMD ["lucy-media-gateway"]

