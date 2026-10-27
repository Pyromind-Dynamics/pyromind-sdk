package main

import (
	"flag"
	"log"
	"net"
	"os"
	"strconv"
)

const version = "0.6.0"

func main() {
	port := flag.Int("port", 7500, "gRPC listen port")
	bind := flag.String("bind", envOr("RIFT_BIND", "0.0.0.0"), "gRPC listen address")
	sandboxID := flag.String("sandbox-id", envOr("RIFT_SANDBOX_ID", ""), "sandbox id this client belongs to")
	tokenFile := flag.String("token-file", envOr("RIFT_TOKEN_FILE", ""), "access key forwarded by rift-gateway")
	flag.Parse()

	if v := os.Getenv("RIFT_PORT"); v != "" {
		if p, err := strconv.Atoi(v); err == nil && p > 0 {
			*port = p
		}
	}
	if *sandboxID == "" {
		log.Fatal("--sandbox-id (or RIFT_SANDBOX_ID) is required")
	}
	if *tokenFile == "" {
		log.Fatal("--token-file (or RIFT_TOKEN_FILE) is required")
	}
	token, err := loadToken(*tokenFile)
	if err != nil {
		log.Fatalf("load token: %v", err)
	}

	addr := net.JoinHostPort(*bind, strconv.Itoa(*port))
	lis, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen %s: %v", addr, err)
	}
	log.Printf(
		"rift-client %s listening on %s pid=%d sandbox=%s",
		version, addr, os.Getpid(), *sandboxID,
	)
	if err := serve(lis, tokenAuth{token: token}); err != nil {
		log.Fatalf("serve: %v", err)
	}
}

func envOr(key, def string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return def
}
