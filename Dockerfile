
# 🔨 CodeForge Agents — Dockerfile
# ---------------------------------------------------------------
# Build:   docker build -t codeforge-agents .
# Run:     docker run -p 8501:8501 --env-file .env codeforge-agents
#    or:   docker run -p 8501:8501 -e GROQ_API_KEY=$GROQ_API_KEY codeforge-agents
#    or:   leave the key out entirely and paste it in the app UI sidebar.
#
# SECURITY: The API key is injected at RUNTIME only.
# The image never contains keys (.env is excluded via .dockerignore).
#
# RAG: The ONNX model and knowledge index are baked into the image.
# The model is copied from the verified local download, avoiding
# another slow network download during the Docker build.
# ---------------------------------------------------------------

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ANONYMIZED_TELEMETRY=False \
    HOME=/home/forge

# Create the non-root runtime user.
RUN useradd -m -u 10001 forge

# Install Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and the extracted ONNX model.
COPY . .

# Place the verified ONNX model in ChromaDB's expected cache.
RUN mkdir -p /home/forge/.cache/chroma/onnx_models/all-MiniLM-L6-v2 && \
    cp -a /app/model-download/cache/all-MiniLM-L6-v2/. \
    /home/forge/.cache/chroma/onnx_models/all-MiniLM-L6-v2/

# Give the runtime user ownership of the application and model.
RUN chown -R forge:forge /app /home/forge

# Build the RAG knowledge index as the same user that runs the app.
USER 10001
RUN python -m rag.ingest

EXPOSE 8501

# Healthcheck using Python (curl is not required).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
