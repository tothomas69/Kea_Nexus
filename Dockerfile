FROM python:3.12-slim

WORKDIR /app

# curl is used by HEALTHCHECK below — python:3.12-slim doesn't include it by
# default, so without this the healthcheck always fails even when the app
# itself is completely healthy.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
	&& rm -rf /var/lib/apt/lists/*

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

EXPOSE 8000

# The web UI's own health route, used by Docker and NPM's upstream check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# --proxy-headers lets Nginx Proxy Manager's X-Forwarded-Proto set the request
# scheme, so session cookies get the Secure flag over HTTPS.
CMD ["uvicorn", "web.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips=*"]
