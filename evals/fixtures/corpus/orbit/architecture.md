# Orbit project architecture

## Retry policy

The Orbit gateway uses exponential backoff with a base delay of 200ms.
Maximum retries before circuit-open is five attempts.

## Transport

All service-to-service calls use gRPC with mutual TLS.
