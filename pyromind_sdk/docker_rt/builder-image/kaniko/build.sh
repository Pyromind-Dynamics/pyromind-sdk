#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Build kaniko executor image with custom CA trust store
# Purpose: Mirror to a registry the cluster can pull from,
#          for docker-rt build sandbox usage
#
# Note: kaniko executor runs on cluster nodes (typically amd64).
#       When kaniko builds target images, docker-rt configures
#       the platform separately (defaults to linux/amd64).
# ============================================================

# ---------- Configuration ----------
BUILD_VERSION="${BUILD_VERSION:-0.0.3}"
IMAGE_NAME="${IMAGE_NAME:-kaniko-executor-pyromind}"

# Target platforms (default: both amd64 and arm64)
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

# Target registry (default: Docker Hub pyrominddynamics)
REGISTRY="${REGISTRY:-docker.io/pyrominddynamics}"

# Build mode: "push" (default) or "local" (native only, no push)
MODE="${MODE:-push}"

# Proxy settings (for buildx and push)
PROXY="${PROXY:-http://127.0.0.1:7897}"

# ---------- Pre-checks ----------
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon not running, please start Docker Desktop first"
  exit 1
fi

# Ensure using local Docker daemon, not docker-rt context
CURRENT_CONTEXT=$(docker context show 2>/dev/null || echo "default")
if [ "$CURRENT_CONTEXT" != "default" ] && [ "$CURRENT_CONTEXT" != "desktop-linux" ]; then
  echo "⚠️  Current Docker context is '$CURRENT_CONTEXT', switching to default"
  docker context use default >/dev/null 2>&1 || true
fi

# ---------- Setup ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Determine current architecture
CURRENT_ARCH="linux/$(docker info --format '{{.Architecture}}' | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')"

# Check if multi-platform
IS_MULTI_PLATFORM=false
if [[ "$PLATFORMS" == *","* ]]; then
  IS_MULTI_PLATFORM=true
fi

# Check if cross-platform build
IS_CROSS_PLATFORM=false
if [ "$PLATFORMS" != "$CURRENT_ARCH" ]; then
  IS_CROSS_PLATFORM=true
fi

# Set tag (simplified: just build version)
IMAGE_TAG="${BUILD_VERSION}"

FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

# ---------- Build ----------
echo "🔨 Building kaniko executor image..."
echo "   Build version:  $BUILD_VERSION"
echo "   Platform(s):    $PLATFORMS"
echo "   Current arch:   $CURRENT_ARCH"
echo "   Target image:   $FULL_IMAGE"
echo "   Mode:           $MODE"
if [ -n "$PROXY" ]; then
  echo "   Proxy:          $PROXY"
fi
echo "   Build dir:      $SCRIPT_DIR"
echo ""

if [ "$IS_MULTI_PLATFORM" = true ] || [ "$IS_CROSS_PLATFORM" = true ]; then
  # Cross-platform or multi-platform build with buildx
  if [ "$IS_MULTI_PLATFORM" = true ]; then
    echo "📤 Multi-platform build: building and pushing to registry"
  else
    echo "📤 Cross-platform build: $CURRENT_ARCH → $PLATFORMS"
  fi

  BUILDER_NAME="pyromind-builder"
  if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
    echo "🔧 Creating builder: $BUILDER_NAME"
    docker buildx create --name "$BUILDER_NAME" --driver docker-container --use
  else
    docker buildx use "$BUILDER_NAME"
  fi

  if [ "$MODE" = "push" ]; then
    HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" \
    docker buildx build \
      --platform "$PLATFORMS" \
      --build-arg BUILD_VERSION="$BUILD_VERSION" \
      -t "$FULL_IMAGE" \
      --push \
      .
  else
    docker buildx build \
      --platform "$PLATFORMS" \
      --build-arg BUILD_VERSION="$BUILD_VERSION" \
      -t "$FULL_IMAGE" \
      .
  fi

  echo ""
  echo "✅ Build successful: $FULL_IMAGE"

  if [ "$IS_MULTI_PLATFORM" = true ] && [ "$MODE" = "push" ]; then
    echo ""
    echo "🔍 Manifest list:"
    HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" \
    docker buildx imagetools inspect "$FULL_IMAGE" 2>/dev/null || true
  fi

else
  # Native build (current arch matches target)
  echo "📦 Native build for: $PLATFORMS"

  docker build \
    --build-arg BUILD_VERSION="$BUILD_VERSION" \
    -t "$FULL_IMAGE" \
    .

  if [ "$MODE" = "push" ]; then
    echo ""
    echo "📤 Pushing to registry..."
    HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" \
    docker push "$FULL_IMAGE"
  fi

  echo ""
  echo "✅ Build successful: $FULL_IMAGE"

  # Verify
  echo ""
  echo "🔍 Image info:"
  docker images "${IMAGE_NAME}:${IMAGE_TAG}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}" 2>/dev/null || true

  echo ""
  echo "🧪 Quick verify:"
  docker run --rm --entrypoint /busybox/sh "$FULL_IMAGE" -c 'echo "shell OK: $(which sh), arch: $(uname -m)"' 2>/dev/null || true

  echo ""
  echo "📋 Image labels:"
  docker inspect "$FULL_IMAGE" --format '{{json .Config.Labels}}' 2>/dev/null | python3 -m json.tool 2>/dev/null || true
fi

echo ""
echo "📋 Final image: $FULL_IMAGE"
echo "🔗 Docker Hub: https://hub.docker.com/r/pyrominddynamics/${IMAGE_NAME}/tags"

# ---------- Sync version to code (only after push) ----------
if [ "$MODE" = "push" ]; then
  echo ""
  echo "📝 Syncing version to build_sandbox.py..."

  BUILD_SANDBOX_PY="${SCRIPT_DIR}/../../backend/build_sandbox.py"
  if [ -f "$BUILD_SANDBOX_PY" ]; then
    # Find the current default image line and update it
    OLD_DEFAULT=$(grep -o 'docker.io/pyrominddynamics/kaniko-executor-pyromind:[0-9.]*' "$BUILD_SANDBOX_PY" | head -1)
    NEW_DEFAULT="${FULL_IMAGE}"

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
fi
