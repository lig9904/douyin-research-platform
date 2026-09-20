# Extend the same pinned Windmill release; do not alter server/native images.
ARG WINDMILL_BASE_IMAGE=ghcr.io/windmill-labs/windmill@sha256:9f175a520477de0cc24724e32f49104ab7aa2537fc684eb1353bf541c1f1142a
FROM ${WINDMILL_BASE_IMAGE}
# Fail explicitly if the pinned upstream image ceases to be Debian compatible.
RUN command -v apt-get \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && ffmpeg -version \
    && ffprobe -version
