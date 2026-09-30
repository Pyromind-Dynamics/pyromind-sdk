#
# Custom kaniko executor image for docker-rt.
#
# Why build our own: The upstream image is on gcr.io, which is unreachable
# from some clusters (especially Shanghai / cn-east-1). We mirror it to a
# registry the cluster can pull from, then point DOCKER_RT_BUILD_IMAGE there.
#
# Why -debug variant: The executor image's final stage is FROM scratch, which
# only contains the static binary /kaniko/executor and a CA bundle - no shell,
# no coreutils. But k8s CUSTOM sandbox templates hardcode command: ["sleep", "infinity"],
# and images without a shell can't even start, let alone exec commands. The -debug
# variant is the same image + busybox (providing sleep, cat, /bin/sh), which is
# exactly what kaniko officially publishes for this use case.
#
# Don't repackage into an unrelated base image: kaniko docs explicitly state that
# running executor in non-official images is unsupported (it unpacks the target
# rootfs into its own container's root directory, so base image affects behavior).
# This file only adds labels and optionally appends custom CA certificates.

ARG KANIKO_VERSION=v1.24.0
ARG KANIKO_IMAGE=gcr.io/kaniko-project/executor

# Version of this custom build (increment when modifying this Dockerfile)
ARG BUILD_VERSION=0.0.2

# ---- Builder image -----------------------------------------------------------
FROM ${KANIKO_IMAGE}:${KANIKO_VERSION}-debug

# Re-declare ARGs after FROM (required to use them in this stage)
ARG KANIKO_VERSION
ARG BUILD_VERSION

LABEL maintainer="pyromind"
LABEL version="${BUILD_VERSION}"
LABEL description="Custom kaniko executor with Pyromind CA trust store"
LABEL kaniko.version="${KANIKO_VERSION}"

# kaniko needs to write to its own container root (it unpacks FROM images there),
# so run as root inside non-privileged containers. The official image has no USER
# directive; this line makes that explicit and will immediately surface if some
# future tag changes it.
USER 0

# Optional: Add custom CA certificates
# If you have custom CA certs, put them in certs/ directory and uncomment:
# COPY certs/*.crt /kaniko/ssl/certs/
#
# The base image already includes standard CA certificates at:
# /kaniko/ssl/certs/ca-certificates.crt

# Keep upstream entrypoint unchanged: sandbox never uses it (templates override
# command), but docker run directly should match upstream behavior.
ENTRYPOINT ["/kaniko/executor"]
