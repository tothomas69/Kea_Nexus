FROM python:3.12-slim

WORKDIR /app

# nmap for OS fingerprint refresh — no meaningful pure-Python equivalent for
# real OS detection, so this has to be the actual nmap binary.
RUN apt-get update && apt-get install -y --no-install-recommends nmap \
	&& rm -rf /var/lib/apt/lists/*

# Install dependencies first for layer caching
COPY quarantine_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared modules — same SQLite schema and Kea/Pi-hole clients KeaNexus itself
# uses. Copied explicitly rather than via a package install since this repo
# isn't structured as an installable package.
COPY db.py .
COPY kea.py .
COPY pihole.py .

# Service code
COPY quarantine_service/ ./quarantine_service/

EXPOSE 8600

CMD ["uvicorn", "quarantine_service.main:app", "--host", "0.0.0.0", "--port", "8600"]
