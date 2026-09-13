#!/usr/bin/env bash
# Build the attendance app image for linux/amd64 + linux/arm64 and push it to the
# GitHub container registry. Requires: docker, and a prior
# `docker login ghcr.io` with push rights on martinbaumg/emargement.
#
# Builds each arch separately with --load + classic `docker push` instead of
# `buildx --push`: buildx's chunked PATCH+PUT blob upload gets a 400 from the Varnish
# proxy in front of git.gnous.eu on layers over a few MB, while the classic
# docker-push code path (monolithic PUT) goes through fine. The two per-arch images
# are then stitched into one multi-arch manifest list with `docker manifest`.
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/martinbaumg/emargement}"
TAG="${1:-latest}"

cd "$(dirname "${BASH_SOURCE[0]}")"

for arch in amd64 arm64; do
    echo "==> building linux/$arch"
    # --provenance=false --sbom=false: without them buildx wraps even a --load'd
    # single-arch image in an attestation manifest *list*, which `docker manifest
    # create` below then refuses to nest into the multi-arch list.
    docker build --platform "linux/$arch" -t "$IMAGE:$TAG-$arch" \
        --provenance=false --sbom=false --load .
    echo "==> pushing $IMAGE:$TAG-$arch"
    docker push "$IMAGE:$TAG-$arch"
done

echo "==> creating manifest list $IMAGE:$TAG"
docker manifest rm "$IMAGE:$TAG" 2>/dev/null || true
docker manifest create "$IMAGE:$TAG" \
    "$IMAGE:$TAG-amd64" \
    "$IMAGE:$TAG-arm64"
docker manifest push "$IMAGE:$TAG"

echo "Pushed $IMAGE:$TAG (linux/amd64, linux/arm64)"
