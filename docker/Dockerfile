# Build a minimal organizer image. For Mathlib, replace /project with the
# organizer's pinned, fully built Lake project before publishing the image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG LEAN_VERSION
ARG LEAN_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl zstd libgmp10 \
    && rm -rf /var/lib/apt/lists/*
RUN test -n "$LEAN_VERSION" && test "${#LEAN_SHA256}" = 64 \
    && curl --fail --location --retry 3 "https://github.com/leanprover/lean4/releases/download/v${LEAN_VERSION}/lean-${LEAN_VERSION}-linux.tar.zst" -o /tmp/lean.tar.zst \
    && echo "$LEAN_SHA256  /tmp/lean.tar.zst" | sha256sum --check \
    && mkdir /opt/lean \
    && tar --zstd -xf /tmp/lean.tar.zst -C /opt/lean --strip-components=1 \
    && rm /tmp/lean.tar.zst
ENV PATH="/opt/lean/bin:${PATH}"
WORKDIR /project
RUN printf 'name = "ftp_worker"\nversion = "0.1.0"\n' > lakefile.toml \
    && printf 'leanprover/lean4:v%s\n' "$LEAN_VERSION" > lean-toolchain \
    && lake update && lake build \
    && chmod -R a+rX /project /opt/lean
USER 65532:65532
