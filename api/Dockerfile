FROM python:3.12-slim

WORKDIR /app
COPY clientbridge_api.py /app/clientbridge_api.py

ENV CLIENTBRIDGE_HOST=0.0.0.0 \
    CLIENTBRIDGE_PORT=8787

EXPOSE 8787

# The server refuses to bind 0.0.0.0 without CLIENTBRIDGE_AUTH_TOKEN or
# CLIENTBRIDGE_SIGNING_KEY. Always pass at least one of them.
#
# There is no volume: events live in RAM only, by design. Restarting the
# container starts with an empty replay buffer.
CMD ["python", "clientbridge_api.py"]
