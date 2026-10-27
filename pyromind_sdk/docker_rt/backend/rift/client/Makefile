GO ?= go
OUT ?= ../bin
MODULE ?= rift-client

.PHONY: build build-linux test fmt vet clean

build:
	@mkdir -p $(OUT)
	CGO_ENABLED=0 $(GO) build -trimpath -ldflags="-s -w" -o $(OUT)/rift-client .

build-linux:
	@mkdir -p $(OUT)
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 $(GO) build -trimpath -ldflags="-s -w" -o $(OUT)/rift-client-linux-amd64 .
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 $(GO) build -trimpath -ldflags="-s -w" -o $(OUT)/rift-client-linux-arm64 .
	@if command -v sha256sum >/dev/null 2>&1; then \
		sha256sum $(OUT)/rift-client-linux-* > $(OUT)/rift-client.sha256; \
	else \
		shasum -a 256 $(OUT)/rift-client-linux-* > $(OUT)/rift-client.sha256; \
	fi

test:
	$(GO) test ./...

fmt:
	$(GO) fmt ./...

vet:
	$(GO) vet ./...

clean:
	rm -f $(OUT)/rift-client $(OUT)/rift-client-linux-* $(OUT)/rift-client.sha256