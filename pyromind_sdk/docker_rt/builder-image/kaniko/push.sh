#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Push kaniko executor image to Docker Hub (multi-platform)
# After successful push, sync version to build_sandbox.py default
# ============================================================

# ---------- Configuration ----------
BUILD_VERSION="${BUILD_VERSION:-0.0.3}"
IMAGE_NAME="${IMAGE_NAME:-kaniko-executor-pyromind}"

# Docker Hub registry
DOCKER_HUB_NS="${DOCKER_HUB_NS:-pyrominddynamics}"
REGISTRY="docker.io/${DOCKER_HUB_NS}"

# Target platforms (default: both amd64 and arm64)
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

# Proxy settings
PROXY="${PROXY:-http://127.0.0.1:7897}"

# ---------- Pre-checks ----------
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon not running"
  exit 1
fi

# ---------- Determine tag ----------
IMAGE_TAG="${BUILD_VERSION}"
REMOTE_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

# ---------- Push logic ----------
echo "📤 Pushing kaniko executor image to Docker Hub"
echo "   Remote image: $REMOTE_IMAGE"
echo "   Platform(s):  $PLATFORMS"
echo "   Proxy:        $PROXY"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Create or use builder
BUILDER_NAME="pyromind-builder"
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
  echo "🔧 Creating builder: $BUILDER_NAME"
  docker buildx create --name "$BUILDER_NAME" --driver docker-container --use
else
  docker buildx use "$BUILDER_NAME"
fi

echo "🔨 Building and pushing multi-platform image..."
HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" \
docker buildx build \
  --platform "$PLATFORMS" \
  --build-arg BUILD_VERSION="$BUILD_VERSION" \
  -t "$REMOTE_IMAGE" \
  --push \
  .

echo ""
echo "✅ Push successful: $REMOTE_IMAGE"
echo ""
echo "🔍 Manifest list:"
HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" \
docker buildx imagetools inspect "$REMOTE_IMAGE" 2>/dev/null || true

echo ""
echo "🔗 Docker Hub: https://hub.docker.com/r/${DOCKER_HUB_NS}/${IMAGE_NAME}/tags"

# ---------- Sync version to code ----------
echo ""
echo "📝 Syncing version to build_sandbox.py..."

BUILD_SANDBOX_PY="${SCRIPT_DIR}/../../backend/build_sandbox.py"
if [ -f "$BUILD_SANDBOX_PY" ]; then
  # Find the current default image line and update it
  OLD_DEFAULT=$(grep -o 'docker.io/pyrominddynamics/kaniko-executor-pyromind:[0-9.]*' "$BUILD_SANDBOX_PY" | head -1)
  NEW_DEFAULT="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

  if [ -n "$OLD_DEFAULT" ]; then
    if [ "$OLD_DEFAULT" != "$NEW_DEFAULT" ]; then
      sed -i.bak "s|${OLD_DEFAULT}|${NEW_DEFAULT}|g" "$BUILD_SANDBOX_PY"
      rm -f "${BUILD_SANDBOX_PY}.bak"
      echo "   Updated: $OLD_DEFAULT → $NEW_DEFAULT"
    else
      echo "   Already up to date: $NEW_DEFAULT"
    fi
  else
    echo "   ⚠️  Could not find existing default to update"
  fi
else
  echo "   ⚠️  build_sandbox.py not found at: $BUILD_SANDBOX_PY"
fi

echo ""
echo "✅ Done!"
