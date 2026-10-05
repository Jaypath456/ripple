# Guided Replay demo image. Build from the repository root:
#   docker build -f demo/Dockerfile -t ripple-demo .
#   docker run --rm -p 8501:8501 ripple-demo
# Live analysis needs a mounted repository and RIPPLE_LLM_* variables; see demo/README.md.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[demo]"

COPY demo ./demo
COPY .streamlit ./.streamlit
COPY evaluation/final_config.json ./evaluation/final_config.json
COPY evaluation/results/final_summary.json ./evaluation/results/final_summary.json

RUN chmod -R a+rX /app && mkdir -p .ripple && chown nobody .ripple
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false HOME=/tmp
EXPOSE 8501
USER nobody
CMD ["streamlit", "run", "demo/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
