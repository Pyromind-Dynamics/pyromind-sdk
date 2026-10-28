package main

import (
	"archive/tar"
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"os/user"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/creack/pty"
	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"

	pb "rift-client/pb"
)

func serve(lis net.Listener, auth tokenAuth) error {
	s := grpc.NewServer(
		grpc.UnaryInterceptor(auth.unary),
		grpc.StreamInterceptor(auth.stream),
		// rift-gateway pings the upstream every 30s
		// (PermitWithoutStream=true); the default MinTime=5m would return
		// GOAWAY/too_many_pings and break long sessions.
		grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
			MinTime:             10 * time.Second,
			PermitWithoutStream: true,
		}),
	)
	pb.RegisterRiftServer(s, &riftServer{})
	return s.Serve(lis)
}

type riftServer struct {
	pb.UnimplementedRiftServer
}

func (r *riftServer) Ping(context.Context, *pb.PingRequest) (*pb.PingReply, error) {
	return &pb.PingReply{Version: version}, nil
}

func (r *riftServer) Exec(stream pb.Rift_ExecServer) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	open := first.GetOpen()
	if open == nil {
		return fmt.Errorf("first frame must be ExecOpen")
	}
	if len(open.Argv) == 0 {
		return fmt.Errorf("argv required")
	}

	cmd := exec.Command(open.Argv[0], open.Argv[1:]...)
	if open.Cwd != "" {
		cmd.Dir = open.Cwd
	}
	cmd.Env = os.Environ()
	for k, v := range open.Env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	if open.User != "" {
		if err := setCmdUser(cmd, open.User); err != nil {
			return err
		}
	}
	if open.Tty {
		return execTTY(stream, cmd, open)
	}
	return execPipe(stream, cmd, open)
}

func execPipe(stream pb.Rift_ExecServer, cmd *exec.Cmd, open *pb.ExecOpen) error {
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}

	var wg sync.WaitGroup
	wg.Add(2)
	go copyOut(stream, stdout, false, &wg)
	go copyOut(stream, stderr, true, &wg)

	recvDone := make(chan struct{})
	go func() {
		defer close(recvDone)
		defer stdin.Close()
		if !open.Stdin {
			return
		}
		for {
			fr, err := stream.Recv()
			if err != nil {
				return
			}
			if fr.GetStdinClose() {
				return
			}
			if b := fr.GetStdin(); len(b) > 0 {
				if _, err := stdin.Write(b); err != nil {
					return
				}
			}
		}
	}()

	wg.Wait()
	<-recvDone
	return stream.Send(&pb.ExecFrame{Body: &pb.ExecFrame_Exit{Exit: &pb.ExecExit{Code: int32(exitCode(cmd.Wait()))}}})
}

func copyOut(stream pb.Rift_ExecServer, r io.Reader, isErr bool, wg *sync.WaitGroup) {
	defer wg.Done()
	buf := make([]byte, 32*1024)
	for {
		n, err := r.Read(buf)
		if n > 0 {
			chunk := append([]byte(nil), buf[:n]...)
			var fr *pb.ExecFrame
			if isErr {
				fr = &pb.ExecFrame{Body: &pb.ExecFrame_Stderr{Stderr: chunk}}
			} else {
				fr = &pb.ExecFrame{Body: &pb.ExecFrame_Stdout{Stdout: chunk}}
			}
			_ = stream.Send(fr)
		}
		if err != nil {
			return
		}
	}
}

func execTTY(stream pb.Rift_ExecServer, cmd *exec.Cmd, open *pb.ExecOpen) error {
	rows, cols := uint16(open.Rows), uint16(open.Cols)
	if rows == 0 {
		rows = 24
	}
	if cols == 0 {
		cols = 80
	}
	ptmx, err := pty.StartWithSize(cmd, &pty.Winsize{Rows: rows, Cols: cols})
	if err != nil {
		return err
	}
	defer func() { _ = ptmx.Close() }()

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		buf := make([]byte, 32*1024)
		for {
			n, err := ptmx.Read(buf)
			if n > 0 {
				_ = stream.Send(&pb.ExecFrame{Body: &pb.ExecFrame_Stdout{Stdout: append([]byte(nil), buf[:n]...)}})
			}
			if err != nil {
				return
			}
		}
	}()

	go func() {
		for {
			fr, err := stream.Recv()
			if err != nil {
				_ = ptmx.Close()
				return
			}
			if fr.GetStdinClose() {
				_ = ptmx.Close()
				return
			}
			if rz := fr.GetResize(); rz != nil {
				_ = pty.Setsize(ptmx, &pty.Winsize{Rows: uint16(rz.Rows), Cols: uint16(rz.Cols)})
				continue
			}
			if b := fr.GetStdin(); len(b) > 0 {
				if _, err := ptmx.Write(b); err != nil {
					return
				}
			}
		}
	}()

	wg.Wait()
	return stream.Send(&pb.ExecFrame{Body: &pb.ExecFrame_Exit{Exit: &pb.ExecExit{Code: int32(exitCode(cmd.Wait()))}}})
}

func (r *riftServer) GetArchive(req *pb.GetArchiveRequest, stream pb.Rift_GetArchiveServer) error {
	target := req.GetPath()
	if target == "" {
		return fmt.Errorf("path required")
	}
	if !filepath.IsAbs(target) {
		target = "/" + target
	}
	parent := filepath.Dir(target)
	base := filepath.Base(target)

	cmd := exec.Command("tar", "-C", parent, "-cf", "-", base)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}

	first := true
	buf := make([]byte, 256*1024)
	var total int64
	for {
		n, err := stdout.Read(buf)
		if n > 0 {
			total += int64(n)
			chunk := &pb.ArchiveChunk{Data: append([]byte(nil), buf[:n]...)}
			if first {
				chunk.Name = base
				chunk.Mode = 0o644
				first = false
			}
			if err := stream.Send(chunk); err != nil {
				_ = cmd.Process.Kill()
				return err
			}
		}
		if err == io.EOF {
			break
		}
		if err != nil {
			_ = cmd.Process.Kill()
			return err
		}
	}
	errBuf, _ := io.ReadAll(stderr)
	if waitErr := cmd.Wait(); waitErr != nil {
		return fmt.Errorf("tar: %v: %s", waitErr, string(errBuf))
	}
	if total == 0 && len(errBuf) > 0 {
		return fmt.Errorf("tar: %s", string(errBuf))
	}
	return nil
}

func (r *riftServer) PutArchive(stream pb.Rift_PutArchiveServer) error {
	first, err := stream.Recv()
	if err != nil {
		return err
	}
	start := first.GetStart()
	if start == nil {
		return fmt.Errorf("first frame must be PutArchiveStart")
	}
	dest := start.GetDestPath()
	if dest == "" {
		return fmt.Errorf("dest_path required")
	}
	if !filepath.IsAbs(dest) {
		dest = "/" + dest
	}
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return err
	}

	cmd := exec.Command("tar", "-C", dest, "-xf", "-")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}

	// Frames -> io.Pipe -> validation/repack -> tar. The repack is what makes
	// the checks effective: tar itself follows a symlink created earlier in the
	// same archive, so a member list like `link -> /etc` + `link/passwd` would
	// otherwise write outside dest.
	pr, pw := io.Pipe()
	var received int64
	recvErr := make(chan error, 1)
	go func() {
		for {
			fr, err := stream.Recv()
			if err == io.EOF {
				recvErr <- nil
				_ = pw.Close()
				return
			}
			if err != nil {
				recvErr <- err
				_ = pw.CloseWithError(err)
				return
			}
			chunk := fr.GetChunk()
			if len(chunk) == 0 {
				continue
			}
			atomic.AddInt64(&received, int64(len(chunk)))
			if _, werr := pw.Write(chunk); werr != nil {
				recvErr <- werr
				return
			}
		}
	}()

	var errBuf bytes.Buffer
	stderrDone := make(chan struct{})
	go func() {
		defer close(stderrDone)
		_, _ = io.Copy(&errBuf, stderr)
	}()

	validateErr := copyValidatedTar(pr, stdin, dest)
	_ = stdin.Close()
	// No reader is left, so a writer stuck after the end-of-archive marker gets
	// ErrClosedPipe instead of blocking the handler.
	_ = pr.Close()
	if validateErr != nil {
		_ = pw.CloseWithError(validateErr)
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		<-stderrDone
		// The receive goroutine unblocks when this handler returns and gRPC
		// cancels the stream; waiting for it here could hang on a stalled peer.
		return validateErr
	}

	waitErr := cmd.Wait()
	<-stderrDone
	select {
	case err := <-recvErr:
		if err != nil && err != io.ErrClosedPipe {
			return err
		}
	case <-stream.Context().Done():
		return stream.Context().Err()
	}
	if waitErr != nil {
		return fmt.Errorf("tar extract: %v: %s", waitErr, errBuf.String())
	}
	return stream.SendAndClose(&pb.PutArchiveReply{
		BytesWritten: atomic.LoadInt64(&received),
	})
}

// tarMemberPath normalises a member name the way GNU tar does: leading slashes
// are dropped, then the path is cleaned. Cleaning before dropping "/" would
// turn "/../etc" into "etc" and hide the traversal.
func tarMemberPath(name string) string {
	raw := strings.TrimLeft(strings.ReplaceAll(name, "\\", "/"), "/")
	if raw == "" {
		return ""
	}
	return path.Clean(raw)
}

func tarParents(rel string) []string {
	parts := strings.Split(rel, "/")
	parents := make([]string, 0, len(parts))
	for i := 1; i < len(parts); i++ {
		parents = append(parents, strings.Join(parts[:i], "/"))
	}
	return parents
}

// validateTarHeader rejects members that could write outside dest:
// traversal paths, absolute or escaping links, device nodes, and members that
// go through a symlink (created in this archive or already in dest).
func validateTarHeader(hdr *tar.Header, dest string, symlinks map[string]struct{}) error {
	name := hdr.Name
	rel := tarMemberPath(name)
	if rel == "" || rel == "." {
		return nil
	}
	if rel == ".." || strings.HasPrefix(rel, "../") || path.IsAbs(rel) {
		return fmt.Errorf("unsafe path in tar archive: %q", name)
	}
	if hdr.Typeflag == tar.TypeChar || hdr.Typeflag == tar.TypeBlock {
		return fmt.Errorf("device node in tar archive: %q", name)
	}
	for _, parent := range tarParents(rel) {
		if _, ok := symlinks[parent]; ok {
			return fmt.Errorf("tar member %q writes through symlink %q", name, parent)
		}
		info, err := os.Lstat(filepath.Join(dest, filepath.FromSlash(parent)))
		if err == nil && info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("tar member %q writes through existing symlink %q", name, parent)
		}
	}
	switch hdr.Typeflag {
	case tar.TypeSymlink:
		target := hdr.Linkname
		if path.IsAbs(target) {
			return fmt.Errorf("tar symlink %q has absolute target %q", name, target)
		}
		resolved := path.Clean(path.Join(path.Dir(rel), target))
		if resolved == ".." || strings.HasPrefix(resolved, "../") {
			return fmt.Errorf("tar symlink %q escapes the destination: %q", name, target)
		}
	case tar.TypeLink:
		target := hdr.Linkname
		if path.IsAbs(target) {
			return fmt.Errorf("tar hardlink %q has absolute target %q", name, target)
		}
		resolved := tarMemberPath(target)
		if resolved == ".." || strings.HasPrefix(resolved, "../") {
			return fmt.Errorf("tar hardlink %q escapes the destination: %q", name, target)
		}
	}
	return nil
}

// copyValidatedTar streams members from src to dst, dropping the ones that
// fail validation instead of handing them to tar.
func copyValidatedTar(src io.Reader, dst io.Writer, dest string) error {
	tr := tar.NewReader(src)
	tw := tar.NewWriter(dst)
	symlinks := make(map[string]struct{})
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return fmt.Errorf("tar: %v", err)
		}
		if hdr.Typeflag == tar.TypeXGlobalHeader {
			continue
		}
		if err := validateTarHeader(hdr, dest, symlinks); err != nil {
			return err
		}
		if hdr.Typeflag == tar.TypeSymlink {
			symlinks[tarMemberPath(hdr.Name)] = struct{}{}
		}
		if err := tw.WriteHeader(hdr); err != nil {
			return fmt.Errorf("tar: %v", err)
		}
		if _, err := io.Copy(tw, tr); err != nil {
			return fmt.Errorf("tar: %v", err)
		}
	}
	if err := tw.Close(); err != nil {
		return fmt.Errorf("tar: %v", err)
	}
	return nil
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	if ee, ok := err.(*exec.ExitError); ok {
		return ee.ExitCode()
	}
	return 1
}

func setCmdUser(cmd *exec.Cmd, spec string) error {
	var uid, gid uint32
	if u, err := user.Lookup(spec); err == nil {
		u64, _ := strconv.ParseUint(u.Uid, 10, 32)
		g64, _ := strconv.ParseUint(u.Gid, 10, 32)
		uid, gid = uint32(u64), uint32(g64)
	} else if u64, err2 := strconv.ParseUint(spec, 10, 32); err2 == nil {
		uid = uint32(u64)
		gid = uid
	} else {
		return fmt.Errorf("user %q: %v", spec, err)
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{
		Credential: &syscall.Credential{Uid: uid, Gid: gid},
	}
	return nil
}
