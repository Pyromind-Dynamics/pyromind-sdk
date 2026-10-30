GO ?= go
PYTHON ?= python3
OUT ?= ../bin
MODULE ?= rift-client

# Per-build identity: `git describe` (falling back to a short hash) so a stale
# binary inside a sandbox can be told apart from the one this SDK ships.
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
# Build identity: the same commit can be rebuilt from different sources, so the
# git string alone cannot answer "is the binary in the Pod the one I just
# built?". `Ping` returns this string and bootstrap refuses to keep a build
# whose version differs from the one shipped next to the binary.
SOURCE_SHA := $(shell $(PYTHON) ../source_fingerprint.py --print 2>/dev/null | cut -c1-12)
BUILD_VERSION := $(VERSION)+$(if $(SOURCE_SHA),$(SOURCE_SHA),dev)
LDFLAGS := -s -w -X main.version=$(BUILD_VERSION)

.PHONY: build build-linux test fmt vet check-proto clean

build:
	@mkdir -p $(OUT)
	CGO_ENABLED=0 $(GO) build -trimpath -ldflags="$(LDFLAGS)" -o $(OUT)/rift-client .

build-linux:
	@mkdir -p $(OUT)
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 $(GO) build -trimpath -ldflags="$(LDFLAGS)" -o $(OUT)/rift-client-linux-amd64 .
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 $(GO) build -trimpath -ldflags="$(LDFLAGS)" -o $(OUT)/rift-client-linux-arm64 .
	@if command -v sha256sum >/dev/null 2>&1; then \
		sha256sum $(OUT)/rift-client-linux-* > $(OUT)/rift-client.sha256; \
	else \
		shasum -a 256 $(OUT)/rift-client-linux-* > $(OUT)/rift-client.sha256; \
	fi
	@printf '%s\n' "$(BUILD_VERSION)" > $(OUT)/rift-client.version
	@$(PYTHON) ../source_fingerprint.py --write

test:
	$(GO) test ./...

fmt:
	$(GO) fmt ./...

vet:
	$(GO) vet ./...

# Schema drift between the SDK copy, the gateway copy and both generated
# bindings. Needs grpcio-tools (python3 -m pip install grpcio-tools).
check-proto:
	$(PYTHON) ../check_proto.py

clean:
	rm -f $(OUT)/rift-client $(OUT)/rift-client-linux-* $(OUT)/rift-client.sha256 $(OUT)/rift-client.version
