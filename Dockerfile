# Multi-stage production container for LunarVision Planetary Registration Suite
FROM python:3.11-slim

# System dependencies for OpenCV, GIS and image processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY backend/ ./backend/
COPY core/ ./core/
COPY utils/ ./utils/
COPY frontend/ ./frontend/
COPY server.py .
COPY run_server.py .
COPY run_registration.py .
COPY streamlit_app.py .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV LUNARVISION_OUTPUT_DIR=/data/lunarvision_deliverables

RUN mkdir -p /data/lunarvision_deliverables

EXPOSE 8000 8501

# Default command starts FastAPI production service
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
