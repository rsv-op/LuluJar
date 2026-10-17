# LuluJar in a container - handy for a throw-away server on a machine you
# do not want to install anything on.
#
#   docker build -t lulujar .
#   docker run --rm -p 8000:8000 -v "$PWD/output:/data" lulujar
#
# Then open http://localhost:8000 in your browser.

FROM python:3.12-slim

# p7zip-full gives a native 7-Zip engine (RAR v4/v5, 7z, zip, tar, ...).
RUN apt-get update \
    && apt-get install -y --no-install-recommends p7zip-full \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

ENV PYTHONPATH=/app \
    LULUJAR_HOME=/data/.lulujar \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data/extracted /data/uploads

EXPOSE 8000
VOLUME ["/data"]

CMD ["python", "-m", "lulujar", "serve", "--host", "0.0.0.0", "--port", "8000", "--root", "/data"]
