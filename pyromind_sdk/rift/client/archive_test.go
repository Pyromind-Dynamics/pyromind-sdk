package main

import (
	"archive/tar"
	"bytes"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type tarEntry struct {
	name     string
	typeflag byte
	linkname string
	body     string
}

func makeTar(t *testing.T, entries ...tarEntry) []byte {
	t.Helper()
	var buf bytes.Buffer
	tw := tar.NewWriter(&buf)
	for _, e := range entries {
		typ := e.typeflag
		if typ == 0 {
			typ = tar.TypeReg
		}
		hdr := &tar.Header{
			Name:     e.name,
			Typeflag: typ,
			Linkname: e.linkname,
			Mode:     0o644,
		}
		if typ == tar.TypeReg {
			hdr.Size = int64(len(e.body))
		}
		if err := tw.WriteHeader(hdr); err != nil {
			t.Fatalf("write header %q: %v", e.name, err)
		}
		if typ == tar.TypeReg && e.body != "" {
			if _, err := tw.Write([]byte(e.body)); err != nil {
				t.Fatalf("write body %q: %v", e.name, err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatalf("close tar: %v", err)
	}
	return buf.Bytes()
}

func repack(t *testing.T, data []byte, dest string) ([]byte, error) {
	t.Helper()
	var out bytes.Buffer
	_, err := copyValidatedTar(bytes.NewReader(data), &out, dest)
	return out.Bytes(), err
}

func TestVerifyExtractedCatchesSilentLoss(t *testing.T) {
	dest := t.TempDir()
	members := []extractedMember{
		{name: "ok.bin", size: 3, typ: tar.TypeReg},
		{name: "missing.bin", size: 3, typ: tar.TypeReg},
	}
	if err := os.WriteFile(filepath.Join(dest, "ok.bin"), []byte("abc"), 0o644); err != nil {
		t.Fatal(err)
	}
	err := verifyExtracted(dest, members)
	if err == nil {
		t.Fatal("a member that never landed must fail the copy")
	}
	if !strings.Contains(err.Error(), "missing.bin") {
		t.Errorf("error = %v, want it to name missing.bin", err)
	}

	// Present but truncated is just as bad: `docker cp` success must mean the
	// bytes are all there.
	if err := os.WriteFile(filepath.Join(dest, "missing.bin"), []byte("ab"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := verifyExtracted(dest, members); err == nil ||
		!strings.Contains(err.Error(), "2 bytes") {
		t.Errorf("truncated member: err = %v, want a size mismatch", err)
	}

	if err := os.WriteFile(filepath.Join(dest, "missing.bin"), []byte("abc"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := verifyExtracted(dest, members); err != nil {
		t.Errorf("complete extraction must pass, got %v", err)
	}
}

func TestEmptyArchiveHasNoMembers(t *testing.T) {
	// `docker cp` never sends an end-of-archive-only payload; PutArchive must
	// reject it instead of letting `tar -xf -` exit 0 and the daemon answer 200.
	var buf bytes.Buffer
	if err := tar.NewWriter(&buf).Close(); err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	members, err := copyValidatedTar(bytes.NewReader(buf.Bytes()), &out, t.TempDir())
	if err != nil {
		t.Fatalf("repack: %v", err)
	}
	if len(members) != 0 {
		t.Fatalf("members = %d, want 0", len(members))
	}
	if buf.Len() == 0 {
		t.Fatal("an empty tar still carries an end-of-archive marker")
	}
}

func TestRepackReturnsMembers(t *testing.T) {
	data := makeTar(t,
		tarEntry{name: "a.tar", typeflag: tar.TypeReg, body: "hello"},
		tarEntry{name: "d", typeflag: tar.TypeDir},
	)
	var out bytes.Buffer
	members, err := copyValidatedTar(bytes.NewReader(data), &out, t.TempDir())
	if err != nil {
		t.Fatalf("repack: %v", err)
	}
	if len(members) != 2 {
		t.Fatalf("members = %d, want 2", len(members))
	}
	if members[0].name != "a.tar" || members[0].size != 5 || members[0].typ != tar.TypeReg {
		t.Errorf("member[0] = %+v", members[0])
	}
}

func readNames(t *testing.T, data []byte) []string {
	t.Helper()
	tr := tar.NewReader(bytes.NewReader(data))
	var names []string
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("read repacked tar: %v", err)
		}
		names = append(names, hdr.Name)
	}
	return names
}

func TestTarMemberPath(t *testing.T) {
	cases := map[string]string{
		"a/b":         "a/b",
		"/a/b":        "a/b",
		"./a":         "a",
		"a/../b":      "b",
		"../etc":      "../etc",
		"/../etc":     "../etc",
		"a/../../etc": "../etc",
		"":            "",
		"a\\b":        "a/b",
	}
	for in, want := range cases {
		if got := tarMemberPath(in); got != want {
			t.Errorf("tarMemberPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestRepackKeepsSafeArchive(t *testing.T) {
	data := makeTar(t,
		tarEntry{name: "dir/", typeflag: tar.TypeDir},
		tarEntry{name: "dir/hello.txt", body: "hi"},
	)
	out, err := repack(t, data, t.TempDir())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	names := readNames(t, out)
	if len(names) != 2 || names[1] != "dir/hello.txt" {
		t.Fatalf("unexpected members: %v", names)
	}
}

func TestRepackRejectsTraversal(t *testing.T) {
	for _, name := range []string{"../etc/passwd", "/../etc/passwd", "a/../../etc/passwd"} {
		data := makeTar(t, tarEntry{name: name, body: "x"})
		if _, err := repack(t, data, t.TempDir()); err == nil ||
			!strings.Contains(err.Error(), "unsafe path") {
			t.Fatalf("%q: want unsafe path error, got %v", name, err)
		}
	}
}

func TestRepackRejectsEscapingSymlink(t *testing.T) {
	for _, target := range []string{"/etc", "../../etc", "../x"} {
		data := makeTar(t, tarEntry{
			name: "link", typeflag: tar.TypeSymlink, linkname: target,
		})
		if _, err := repack(t, data, t.TempDir()); err == nil ||
			!strings.Contains(err.Error(), "symlink") {
			t.Fatalf("target %q: want symlink error, got %v", target, err)
		}
	}
}

func TestRepackRejectsWriteThroughSymlinkInArchive(t *testing.T) {
	data := makeTar(t,
		tarEntry{name: "link", typeflag: tar.TypeSymlink, linkname: "sub"},
		tarEntry{name: "link/passwd", body: "x"},
	)
	if _, err := repack(t, data, t.TempDir()); err == nil ||
		!strings.Contains(err.Error(), "writes through symlink") {
		t.Fatalf("want write-through error, got %v", err)
	}
}

func TestRepackRejectsWriteThroughExistingSymlink(t *testing.T) {
	dest := t.TempDir()
	if err := os.Symlink("/etc", filepath.Join(dest, "link")); err != nil {
		t.Fatalf("symlink: %v", err)
	}
	data := makeTar(t, tarEntry{name: "link/passwd", body: "x"})
	if _, err := repack(t, data, dest); err == nil ||
		!strings.Contains(err.Error(), "existing symlink") {
		t.Fatalf("want existing-symlink error, got %v", err)
	}
}

func TestRepackRejectsDeviceNodes(t *testing.T) {
	data := makeTar(t, tarEntry{name: "dev/sda", typeflag: tar.TypeBlock})
	if _, err := repack(t, data, t.TempDir()); err == nil ||
		!strings.Contains(err.Error(), "device node") {
		t.Fatalf("want device node error, got %v", err)
	}
}

func TestRepackRejectsEscapingHardlink(t *testing.T) {
	data := makeTar(t, tarEntry{
		name: "hard", typeflag: tar.TypeLink, linkname: "../../etc/passwd",
	})
	if _, err := repack(t, data, t.TempDir()); err == nil ||
		!strings.Contains(err.Error(), "hardlink") {
		t.Fatalf("want hardlink error, got %v", err)
	}
}

func TestRepackRejectsInvalidArchive(t *testing.T) {
	if _, err := repack(t, []byte("not a tar"), t.TempDir()); err == nil {
		t.Fatal("want error for invalid archive")
	}
}

func TestRepackEmptyArchive(t *testing.T) {
	out, err := repack(t, nil, t.TempDir())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if names := readNames(t, out); len(names) != 0 {
		t.Fatalf("expected no members, got %v", names)
	}
}
