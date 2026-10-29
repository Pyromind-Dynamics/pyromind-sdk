package main

import (
	"context"
	"crypto/subtle"
	"fmt"
	"os"
	"strings"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

// tokenAuth checks the credential forwarded by rift-gateway. The gateway has
// already matched the access key against the sandbox's Pipe; this hop ensures
// that a process inside the cluster cannot call rift-client directly.
type tokenAuth struct {
	token string
}

func loadToken(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	token := strings.TrimSpace(string(raw))
	if token == "" {
		return "", fmt.Errorf("token file is empty")
	}
	return token, nil
}

func (a tokenAuth) unary(
	ctx context.Context,
	req any,
	_ *grpc.UnaryServerInfo,
	handler grpc.UnaryHandler,
) (any, error) {
	if err := a.check(ctx); err != nil {
		return nil, err
	}
	return handler(ctx, req)
}

func (a tokenAuth) stream(
	srv any,
	stream grpc.ServerStream,
	_ *grpc.StreamServerInfo,
	handler grpc.StreamHandler,
) error {
	if err := a.check(stream.Context()); err != nil {
		return err
	}
	return handler(srv, stream)
}

func (a tokenAuth) check(ctx context.Context) error {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return status.Error(codes.Unauthenticated, "missing gRPC metadata")
	}
	values := md.Get("x-rift-token")
	if len(values) == 0 {
		return status.Error(codes.Unauthenticated, "missing x-rift-token")
	}
	got := strings.TrimSpace(values[0])
	if got == "" || a.token == "" {
		return status.Error(codes.Unauthenticated, "invalid x-rift-token")
	}
	if subtle.ConstantTimeCompare([]byte(got), []byte(a.token)) != 1 {
		return status.Error(codes.Unauthenticated, "invalid x-rift-token")
	}
	return nil
}
