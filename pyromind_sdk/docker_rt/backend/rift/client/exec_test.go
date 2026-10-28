package main

import (
	"archive/tar"
	"bytes"
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"google.golang.org/grpc"

	pb "rift-client/pb"
)

// ---- fakes ---------------------------------------------------------------

type fakeExecStream struct {
	grpc.ServerStream
	mu  sync.Mutex
	in  []*pb.ExecFrame
	out []*pb.ExecFrame
	idx int
}

func (f *fakeExecStream) Send(fr *pb.ExecFrame) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.out = append(f.out, fr)
	return nil
}

func (f *fakeExecStream) Recv() (*pb.ExecFrame, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.idx >= len(f.in) {
		return nil, io.EOF
	}
	fr := f.in[f.idx]
	f.idx++
	return fr, nil
}

func (f *fakeExecStream) frames() []*pb.ExecFrame {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]*pb.ExecFrame(nil), f.out...)
}

type chanExecStream struct {
	grpc.ServerStream
	mu   sync.Mutex
	recv chan *pb.ExecFrame
	out  []*pb.ExecFrame
}

func (f *chanExecStream) Send(fr *pb.ExecFrame) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.out = append(f.out, fr)
	return nil
}

func (f *chanExecStream) Recv() (*pb.ExecFrame, error) {
	fr, ok := <-f.recv
	if !ok {
		return nil, io.EOF
	}
	return fr, nil
}

type fakeGetArchiveStream struct {
	grpc.ServerStream
	mu     sync.Mutex
	chunks []*pb.ArchiveChunk
}

func (f *fakeGetArchiveStream) Send(c *pb.ArchiveChunk) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.chunks = append(f.chunks, c)
	return nil
}

type fakePutArchiveStream struct {
	grpc.ServerStream
	mu     sync.Mutex
	frames []*pb.PutArchiveFrame
	idx    int
	reply  *pb.PutArchiveReply
}

func (f *fakePutArchiveStream) Recv() (*pb.PutArchiveFrame, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.idx >= len(f.frames) {
		return nil, io.EOF
	}
	fr := f.frames[f.idx]
	f.idx++
	return fr, nil
}

func (f *fakePutArchiveStream) SendAndClose(r *pb.PutArchiveReply) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reply = r
	return nil
}

func (f *fakePutArchiveStream) Context() context.Context {
	return context.Background()
}

// ---- helpers -------------------------------------------------------------

func openFrame(argv []string, mutate func(*pb.ExecOpen)) *pb.ExecFrame {
	open := &pb.ExecOpen{Argv: argv}
	if mutate != nil {
		mutate(open)
	}
	return &pb.ExecFrame{Body: &pb.ExecFrame_Open{Open: open}}
}

func collect(frames []*pb.ExecFrame) (stdout, stderr string, exit int, sawExit bool) {
	var out, errOut bytes.Buffer
	for _, fr := range frames {
		switch body := fr.Body.(type) {
		case *pb.ExecFrame_Stdout:
			out.Write(body.Stdout)
		case *pb.ExecFrame_Stderr:
			errOut.Write(body.Stderr)
		case *pb.ExecFrame_Exit:
			exit = int(body.Exit.Code)
			sawExit = true
		}
	}
	return out.String(), errOut.String(), exit, sawExit
}

// ---- Exec ----------------------------------------------------------------

func TestExecRequiresOpenFrame(t *testing.T) {
	stream := &fakeExecStream{in: []*pb.ExecFrame{{Body: &pb.ExecFrame_Stdin{Stdin: []byte("x")}}}}
	if err := (&riftServer{}).Exec(stream); err == nil ||
		!strings.Contains(err.Error(), "first frame") {
		t.Fatalf("want first-frame error, got %v", err)
	}
}

func TestExecRequiresArgv(t *testing.T) {
	stream := &fakeExecStream{in: []*pb.ExecFrame{openFrame(nil, nil)}}
	if err := (&riftServer{}).Exec(stream); err == nil ||
		!strings.Contains(err.Error(), "argv required") {
		t.Fatalf("want argv error, got %v", err)
	}
}

func TestExecStreamsStdoutStderrAndExitCode(t *testing.T) {
	stream := &fakeExecStream{in: []*pb.ExecFrame{
		openFrame([]string{"/bin/sh", "-c", "printf hi; printf err >&2; exit 7"}, nil),
	}}
	if err := (&riftServer{}).Exec(stream); err != nil {
		t.Fatalf("exec: %v", err)
	}
	stdout, stderr, code, sawExit := collect(stream.frames())
	if stdout != "hi" {
		t.Errorf("stdout = %q, want %q", stdout, "hi")
	}
	if stderr != "err" {
		t.Errorf("stderr = %q, want %q", stderr, "err")
	}
	if !sawExit || code != 7 {
		t.Errorf("exit = %d (saw=%v), want 7", code, sawExit)
	}
}

func TestExecStdinIsForwarded(t *testing.T) {
	stream := &fakeExecStream{in: []*pb.ExecFrame{
		openFrame([]string{"/bin/sh", "-c", "cat"}, func(o *pb.ExecOpen) { o.Stdin = true }),
		{Body: &pb.ExecFrame_Stdin{Stdin: []byte("ping")}},
		{Body: &pb.ExecFrame_StdinClose{StdinClose: true}},
	}}
	if err := (&riftServer{}).Exec(stream); err != nil {
		t.Fatalf("exec: %v", err)
	}
	stdout, _, code, _ := collect(stream.frames())
	if stdout != "ping" {
		t.Errorf("stdout = %q, want %q", stdout, "ping")
	}
	if code != 0 {
		t.Errorf("exit = %d, want 0", code)
	}
}

func TestExecAppliesCwd(t *testing.T) {
	dir := t.TempDir()
	stream := &fakeExecStream{in: []*pb.ExecFrame{
		openFrame([]string{"/bin/sh", "-c", "pwd"}, func(o *pb.ExecOpen) { o.Cwd = dir }),
	}}
	if err := (&riftServer{}).Exec(stream); err != nil {
		t.Fatalf("exec: %v", err)
	}
	stdout, _, _, _ := collect(stream.frames())
	// pwd may resolve /var -> /private/var on macOS.
	if !strings.Contains(stdout, filepath.Base(dir)) {
		t.Errorf("stdout = %q, want a path ending in %q", stdout, filepath.Base(dir))
	}
}

func TestExecTTYUsesPTY(t *testing.T) {
	recv := make(chan *pb.ExecFrame, 1)
	stream := &chanExecStream{recv: recv}
	recv <- openFrame([]string{"/bin/sh", "-c", "printf tty; exit 3"}, func(o *pb.ExecOpen) {
		o.Tty = true
		o.Rows = 24
		o.Cols = 80
	})
	// No stdin_close: closing the channel would tear the PTY down before the
	// command prints. Recv blocks until the test ends.
	done := make(chan error, 1)
	go func() { done <- (&riftServer{}).Exec(stream) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("exec: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("tty exec timed out")
	}
	close(recv)
	stream.mu.Lock()
	frames := append([]*pb.ExecFrame(nil), stream.out...)
	stream.mu.Unlock()
	stdout, _, code, _ := collect(frames)
	if !strings.Contains(stdout, "tty") {
		t.Errorf("stdout = %q, want it to contain %q", stdout, "tty")
	}
	if code != 3 {
		t.Errorf("exit = %d, want 3", code)
	}
}

// ---- Ping / helpers ------------------------------------------------------

func TestPingReturnsVersion(t *testing.T) {
	reply, err := (&riftServer{}).Ping(context.Background(), &pb.PingRequest{})
	if err != nil {
		t.Fatalf("ping: %v", err)
	}
	if reply.GetVersion() != version {
		t.Errorf("version = %q, want %q", reply.GetVersion(), version)
	}
}

func TestExitCode(t *testing.T) {
	if got := exitCode(nil); got != 0 {
		t.Errorf("nil -> %d, want 0", got)
	}
	cmd := exec.Command("/bin/sh", "-c", "exit 9")
	err := cmd.Run()
	if got := exitCode(err); got != 9 {
		t.Errorf("exit 9 -> %d, want 9", got)
	}
}

func TestSetCmdUser(t *testing.T) {
	if err := setCmdUser(exec.Command("/bin/true"), "12345"); err != nil {
		t.Fatalf("numeric uid: %v", err)
	}
	if err := setCmdUser(exec.Command("/bin/true"), "definitely-not-a-user"); err == nil {
		t.Fatal("want error for unknown user")
	}
}

// ---- GetArchive ----------------------------------------------------------

func TestGetArchiveStreamsTar(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "hello.txt"), []byte("hello"), 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	stream := &fakeGetArchiveStream{}
	if err := (&riftServer{}).GetArchive(&pb.GetArchiveRequest{
		Path: filepath.Join(dir, "hello.txt"),
	}, stream); err != nil {
		t.Fatalf("get archive: %v", err)
	}
	var raw bytes.Buffer
	for _, chunk := range stream.chunks {
		raw.Write(chunk.Data)
	}
	if len(stream.chunks) == 0 || stream.chunks[0].Name != "hello.txt" {
		t.Errorf("first chunk name = %q, want hello.txt", firstChunkName(stream.chunks))
	}
	tr := tar.NewReader(&raw)
	// macOS bsdtar adds AppleDouble members (._name) for xattrs; skip them.
	for {
		hdr, err := tr.Next()
		if err != nil {
			t.Fatalf("read tar: %v", err)
		}
		if hdr.Name != "hello.txt" {
			continue
		}
		body, _ := io.ReadAll(tr)
		if string(body) != "hello" {
			t.Errorf("member body = %q, want hello", body)
		}
		return
	}
}

func firstChunkName(chunks []*pb.ArchiveChunk) string {
	if len(chunks) == 0 {
		return "<none>"
	}
	return chunks[0].Name
}

// ---- PutArchive ----------------------------------------------------------

func putFrames(dest string, data []byte) []*pb.PutArchiveFrame {
	frames := []*pb.PutArchiveFrame{{Body: &pb.PutArchiveFrame_Start{
		Start: &pb.PutArchiveStart{DestPath: dest},
	}}}
	for offset := 0; offset < len(data); offset += 32 {
		end := offset + 32
		if end > len(data) {
			end = len(data)
		}
		frames = append(frames, &pb.PutArchiveFrame{
			Body: &pb.PutArchiveFrame_Chunk{Chunk: data[offset:end]},
		})
	}
	return frames
}

func TestPutArchiveExtractsFile(t *testing.T) {
	dest := t.TempDir()
	data := makeTar(t, tarEntry{name: "hello.txt", body: "hi"})
	stream := &fakePutArchiveStream{frames: putFrames(dest, data)}
	if err := (&riftServer{}).PutArchive(stream); err != nil {
		t.Fatalf("put archive: %v", err)
	}
	if stream.reply == nil || stream.reply.BytesWritten != int64(len(data)) {
		t.Errorf("reply = %+v, want %d bytes", stream.reply, len(data))
	}
	body, err := os.ReadFile(filepath.Join(dest, "hello.txt"))
	if err != nil {
		t.Fatalf("read extracted file: %v", err)
	}
	if string(body) != "hi" {
		t.Errorf("file = %q, want %q", body, "hi")
	}
}

func TestPutArchiveRejectsTraversalOverRPC(t *testing.T) {
	root := t.TempDir()
	dest := filepath.Join(root, "dest")
	if err := os.MkdirAll(dest, 0o755); err != nil {
		t.Fatalf("mkdir: %v", err)
	}
	data := makeTar(t, tarEntry{name: "../escape.txt", body: "x"})
	stream := &fakePutArchiveStream{frames: putFrames(dest, data)}
	err := (&riftServer{}).PutArchive(stream)
	if err == nil || !strings.Contains(err.Error(), "unsafe path") {
		t.Fatalf("want unsafe path error, got %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(root, "escape.txt")); statErr == nil {
		t.Fatal("traversal member was written outside dest")
	}
}

func TestPutArchiveRequiresStartFrame(t *testing.T) {
	stream := &fakePutArchiveStream{frames: []*pb.PutArchiveFrame{
		{Body: &pb.PutArchiveFrame_Chunk{Chunk: []byte("x")}},
	}}
	if err := (&riftServer{}).PutArchive(stream); err == nil ||
		!strings.Contains(err.Error(), "first frame") {
		t.Fatalf("want first-frame error, got %v", err)
	}
}
