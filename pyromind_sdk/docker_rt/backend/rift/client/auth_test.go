package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

func ctxWithToken(token string) context.Context {
	if token == "" {
		return context.Background()
	}
	return metadata.NewIncomingContext(
		context.Background(), metadata.Pairs("x-rift-token", token),
	)
}

func TestTokenAuth(t *testing.T) {
	auth := tokenAuth{token: "ACCESSKEY123"}

	if err := auth.check(ctxWithToken("ACCESSKEY123")); err != nil {
		t.Fatalf("valid token rejected: %v", err)
	}
	if err := auth.check(ctxWithToken("")); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("missing token: got %v", err)
	}
	if err := auth.check(ctxWithToken("OTHER")); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("wrong token: got %v", err)
	}
	if err := auth.check(context.Background()); status.Code(err) != codes.Unauthenticated {
		t.Fatalf("missing metadata: got %v", err)
	}
}

func TestLoadToken(t *testing.T) {
	path := filepath.Join(t.TempDir(), "token")
	if err := os.WriteFile(path, []byte("  ACCESSKEY123\n"), 0o400); err != nil {
		t.Fatal(err)
	}
	token, err := loadToken(path)
	if err != nil {
		t.Fatalf("load token: %v", err)
	}
	if token != "ACCESSKEY123" {
		t.Fatalf("token = %q", token)
	}

	empty := filepath.Join(t.TempDir(), "empty")
	if err := os.WriteFile(empty, []byte(" \n"), 0o400); err != nil {
		t.Fatal(err)
	}
	if _, err := loadToken(empty); err == nil {
		t.Fatal("empty token file must be rejected")
	}
}
