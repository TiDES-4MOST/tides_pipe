FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code - copy entire current directory as tides_pipe
COPY . /app/tides_pipe

# Set Python path so imports work correctly
ENV PYTHONPATH=/app

# Expose config via env
#TODO configure these to whatever mounted disks we have
ENV TIDES_CONFIG=/app/tides_pipe/config/config.yml
ENV DELIVERIES_DIR=/data/deliveries
ENV SPECTRA_DIR=/data/spectra
ENV STATIC_PLOTS_DIR=/data/static/plots

# FastAPI port
EXPOSE 8001

# Default CMD: start API
CMD ["uvicorn", "tides_pipe.pipeline_service:app", "--host", "0.0.0.0", "--port", "8001"] #TODO fix this
