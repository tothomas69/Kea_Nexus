"""
version.py — Single source of truth for the KeaNexus application version.
Shown in the web UI (web/templating.py) and read by any tooling that needs it.
"""

# Bump this on every release. Shown under the logo and in the Help header.
# 2.0.0 is the move from Streamlit to the FastAPI + HTMX web UI.
APP_VERSION = "2.0.0"
