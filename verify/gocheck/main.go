// An MCP client for the groundcheck server, in Go, plus structural validation
// of the case tables.
//
// Everything else in verify/ reimplements a function out of verify.py. None of
// it touches the server, and the server is the only thing a host actually
// talks to. The Python tests import verify.py directly, so a tool that was
// renamed, dropped, given the wrong argument names, or wired to the wrong
// function would pass every test in this repo and still be broken for every
// caller.
//
// So this speaks the protocol: it starts the real server over stdio, does the
// initialize handshake, lists the tools, and then replays every math and repo
// case from the tables through tools/call. The verdict that comes back over
// the wire has to be the verdict Python recorded. It also checks that the tool
// names the README documents are the tool names the server advertises, which
// is the check this repo exists to perform, applied to itself.
//
//	go run . -root <repo-root> [-python <interpreter>]
package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const valueTol = 1e-12

var statuses = map[string]bool{"checked": true, "refuted": true, "unverifiable": true}

// ---- minimal JSON-RPC over stdio ----------------------------------------

type client struct {
	cmd  *exec.Cmd
	in   io.WriteCloser
	out  *bufio.Scanner
	next int
}

type rpcResponse struct {
	ID     int             `json:"id"`
	Result json.RawMessage `json:"result"`
	Error  *struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

func start(root, python string) (*client, error) {
	cmd := exec.Command(python, "-m", "src.groundcheck.server")
	cmd.Dir = root
	cmd.Stderr = os.Stderr
	in, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 0, 64*1024), 8*1024*1024)
	return &client{cmd: cmd, in: in, out: sc}, nil
}

func (c *client) notify(method string, params any) error {
	return c.write(map[string]any{"jsonrpc": "2.0", "method": method, "params": params})
}

func (c *client) call(method string, params any) (json.RawMessage, error) {
	c.next++
	id := c.next
	if err := c.write(map[string]any{
		"jsonrpc": "2.0", "id": id, "method": method, "params": params,
	}); err != nil {
		return nil, err
	}
	for c.out.Scan() {
		line := strings.TrimSpace(c.out.Text())
		if line == "" {
			continue
		}
		var resp rpcResponse
		if err := json.Unmarshal([]byte(line), &resp); err != nil {
			return nil, fmt.Errorf("server sent something that is not JSON-RPC: %s", line)
		}
		if resp.ID != id {
			continue // a notification or an out of order reply
		}
		if resp.Error != nil {
			return nil, fmt.Errorf("%s: server error %d: %s",
				method, resp.Error.Code, resp.Error.Message)
		}
		return resp.Result, nil
	}
	if err := c.out.Err(); err != nil {
		return nil, err
	}
	return nil, fmt.Errorf("%s: the server closed stdout without replying", method)
}

func (c *client) write(msg any) error {
	b, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	_, err = c.in.Write(append(b, '\n'))
	return err
}

func (c *client) stop() {
	_ = c.in.Close()
	done := make(chan struct{})
	go func() { _ = c.cmd.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		_ = c.cmd.Process.Kill()
	}
}

// ---- the tool call shape ------------------------------------------------

type toolResult struct {
	Content []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
	IsError bool `json:"isError"`
}

type verdict struct {
	Status   string `json:"status"`
	Method   string `json:"method"`
	Evidence string `json:"evidence"`
	Detail   string `json:"detail"`
}

func (c *client) callTool(name string, args map[string]any) (verdict, error) {
	var v verdict
	raw, err := c.call("tools/call", map[string]any{"name": name, "arguments": args})
	if err != nil {
		return v, err
	}
	var res toolResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return v, err
	}
	if res.IsError {
		return v, fmt.Errorf("%s reported isError", name)
	}
	if len(res.Content) != 1 || res.Content[0].Type != "text" {
		return v, fmt.Errorf("%s returned %d content blocks, expected one text block",
			name, len(res.Content))
	}
	if err := json.Unmarshal([]byte(res.Content[0].Text), &v); err != nil {
		return v, fmt.Errorf("%s returned a payload that is not a verdict: %v", name, err)
	}
	// The contract in the README is that every tool returns the same shape and
	// one of exactly three statuses. A fourth status would be a silent change
	// of meaning for every caller.
	if !statuses[v.Status] {
		return v, fmt.Errorf("%s returned status %q, which is not one of the three verdicts",
			name, v.Status)
	}
	if v.Method == "" {
		return v, fmt.Errorf("%s returned an empty method, so the verdict is not auditable", name)
	}
	return v, nil
}

// ---- tsv ----------------------------------------------------------------

func unescape(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] != '\\' || i+1 >= len(s) {
			b.WriteByte(s[i])
			continue
		}
		i++
		switch s[i] {
		case 'n':
			b.WriteByte('\n')
		case 't':
			b.WriteByte('\t')
		case '\\':
			b.WriteByte('\\')
		default:
			b.WriteByte('\\')
			b.WriteByte(s[i])
		}
	}
	return b.String()
}

// readTable returns the data rows of a tsv, having checked that it is a table:
// a header, a fixed field count, no empty or duplicated ids.
func readTable(path string, want int, header string) ([][]string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	if len(lines) < 2 {
		return nil, fmt.Errorf("%s has %d lines, so it holds no cases",
			filepath.Base(path), len(lines))
	}
	if lines[0] != header {
		return nil, fmt.Errorf("%s header is %q, expected %q",
			filepath.Base(path), lines[0], header)
	}
	seen := map[string]bool{}
	var rows [][]string
	for i, line := range lines[1:] {
		f := strings.Split(line, "\t")
		if len(f) != want {
			return nil, fmt.Errorf("%s row %d has %d fields, expected %d",
				filepath.Base(path), i+1, len(f), want)
		}
		if f[0] == "" {
			return nil, fmt.Errorf("%s row %d has an empty id", filepath.Base(path), i+1)
		}
		if seen[f[0]] {
			return nil, fmt.Errorf("%s has two rows with id %q", filepath.Base(path), f[0])
		}
		seen[f[0]] = true
		rows = append(rows, f)
	}
	return rows, nil
}

func checkStatusColumn(name string, rows [][]string, col int) error {
	for _, r := range rows {
		if !statuses[r[col]] {
			return fmt.Errorf("%s row %s records status %q, which is not one of the three verdicts",
				name, r[0], r[col])
		}
	}
	return nil
}

// ---- the run ------------------------------------------------------------

func run(root, python string) error {
	mathRows, err := readTable(filepath.Join(root, "verify/cases_math.tsv"), 5,
		"id\texpression\tclaimed\tstatus\tvalue")
	if err != nil {
		return err
	}
	repoRows, err := readTable(filepath.Join(root, "verify/cases_repo.tsv"), 6,
		"id\tpattern\tpath\tregex\tstatus\thits")
	if err != nil {
		return err
	}
	textRows, err := readTable(filepath.Join(root, "verify/cases_text.tsv"), 4,
		"id\tfixture\tneedle\tstatus")
	if err != nil {
		return err
	}
	for _, c := range []struct {
		name string
		rows [][]string
		col  int
	}{{"cases_math.tsv", mathRows, 3}, {"cases_repo.tsv", repoRows, 4},
		{"cases_text.tsv", textRows, 3}} {
		if err := checkStatusColumn(c.name, c.rows, c.col); err != nil {
			return err
		}
	}
	fmt.Printf("  tables well formed: %d math, %d repo, %d text cases\n",
		len(mathRows), len(repoRows), len(textRows))

	c, err := start(root, python)
	if err != nil {
		return err
	}
	defer c.stop()

	initRaw, err := c.call("initialize", map[string]any{
		"protocolVersion": "2025-06-18",
		"capabilities":    map[string]any{},
		"clientInfo":      map[string]any{"name": "gocheck", "version": "0"},
	})
	if err != nil {
		return err
	}
	var initRes struct {
		ProtocolVersion string `json:"protocolVersion"`
		ServerInfo      struct {
			Name string `json:"name"`
		} `json:"serverInfo"`
	}
	if err := json.Unmarshal(initRaw, &initRes); err != nil {
		return err
	}
	if initRes.ServerInfo.Name != "groundcheck" {
		return fmt.Errorf("server introduced itself as %q", initRes.ServerInfo.Name)
	}
	if initRes.ProtocolVersion == "" {
		return fmt.Errorf("server did not state a protocol version")
	}
	if err := c.notify("notifications/initialized", map[string]any{}); err != nil {
		return err
	}
	fmt.Printf("  handshake: %s speaks MCP %s\n",
		initRes.ServerInfo.Name, initRes.ProtocolVersion)

	// tools/list, and the arguments each tool actually accepts.
	listRaw, err := c.call("tools/list", map[string]any{})
	if err != nil {
		return err
	}
	var list struct {
		Tools []struct {
			Name        string `json:"name"`
			Description string `json:"description"`
			InputSchema struct {
				Type       string                    `json:"type"`
				Required   []string                  `json:"required"`
				Properties map[string]map[string]any `json:"properties"`
			} `json:"inputSchema"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(listRaw, &list); err != nil {
		return err
	}
	expect := map[string]map[string]string{
		"check_quote":    {"quote": "string", "url": "string"},
		"check_citation": {"identifier": "string"},
		"check_code":     {"snippet": "string", "expected_output": "string"},
		"check_repo":     {"pattern": "string", "path": "string", "regex": "boolean"},
		"check_math":     {"expression": "string", "claimed_result": "number"},
	}
	advertised := map[string]bool{}
	for _, t := range list.Tools {
		advertised[t.Name] = true
		want, ok := expect[t.Name]
		if !ok {
			return fmt.Errorf("server advertises an undocumented tool %q", t.Name)
		}
		if t.Description == "" {
			return fmt.Errorf("tool %q has no description, so a host cannot tell when to call it", t.Name)
		}
		if t.InputSchema.Type != "object" {
			return fmt.Errorf("tool %q has input schema type %q", t.Name, t.InputSchema.Type)
		}
		for arg, typ := range want {
			prop, ok := t.InputSchema.Properties[arg]
			if !ok {
				return fmt.Errorf("tool %q does not accept %q", t.Name, arg)
			}
			if got, _ := prop["type"].(string); got != typ {
				return fmt.Errorf("tool %q argument %q has type %v, expected %s",
					t.Name, arg, prop["type"], typ)
			}
		}
		if len(t.InputSchema.Properties) != len(want) {
			return fmt.Errorf("tool %q takes %d arguments, expected %d",
				t.Name, len(t.InputSchema.Properties), len(want))
		}
	}
	for name := range expect {
		if !advertised[name] {
			return fmt.Errorf("the server no longer advertises %q", name)
		}
	}
	fmt.Printf("  tools/list: %d tools, argument names and types as documented\n",
		len(list.Tools))

	// The README's own tool table, checked against the server. This is exactly
	// what check_repo does for other people's claims.
	readme, err := os.ReadFile(filepath.Join(root, "README.md"))
	if err != nil {
		return err
	}
	named := map[string]bool{}
	for _, m := range regexp.MustCompile(`check_[a-z_]+`).FindAll(readme, -1) {
		named[string(m)] = true
	}
	var missing []string
	for name := range expect {
		if !named[name] {
			missing = append(missing, name)
		}
	}
	for name := range named {
		if !advertised[name] {
			missing = append(missing, name+" (in the README, not on the server)")
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		return fmt.Errorf("README and server disagree about the tools: %s",
			strings.Join(missing, ", "))
	}
	fmt.Printf("  README documents exactly the %d tools the server advertises\n", len(expect))

	// The README also states how many tests the suite has. That is a claim
	// about a file in this repository, which is precisely the kind of claim
	// check_repo exists to settle, so it gets settled rather than trusted.
	claim := regexp.MustCompile(`([0-9]+) tests`).FindSubmatch(readme)
	if claim == nil {
		return fmt.Errorf("the README no longer states how many tests there are")
	}
	claimed, err := strconv.Atoi(string(claim[1]))
	if err != nil {
		return err
	}
	suite, err := os.ReadFile(filepath.Join(root, "tests/test_verify.py"))
	if err != nil {
		return err
	}
	defined := len(regexp.MustCompile(`(?m)^def test`).FindAll(suite, -1))
	if claimed != defined {
		return fmt.Errorf("the README says %d tests, tests/test_verify.py defines %d",
			claimed, defined)
	}
	fmt.Printf("  README's claim of %d tests matches tests/test_verify.py\n", defined)

	// Replay the recorded cases through the protocol.
	bad := 0
	worst := 0.0
	for _, r := range mathRows {
		claimed, err := strconv.ParseFloat(r[2], 64)
		if err != nil {
			return fmt.Errorf("case %s: unreadable claimed value %q", r[0], r[2])
		}
		v, err := c.callTool("check_math", map[string]any{
			"expression": unescape(r[1]), "claimed_result": claimed,
		})
		if err != nil {
			return err
		}
		if v.Status != r[3] {
			fmt.Printf("  %s (%s): table says %s, the server says %s\n",
				r[0], r[1], r[3], v.Status)
			bad++
			continue
		}
		if r[4] != "" {
			want, err := strconv.ParseFloat(r[4], 64)
			if err != nil {
				return fmt.Errorf("case %s: unreadable value %q", r[0], r[4])
			}
			got, err := strconv.ParseFloat(v.Evidence, 64)
			if err != nil {
				fmt.Printf("  %s: server evidence %q is not a number\n", r[0], v.Evidence)
				bad++
				continue
			}
			d := math.Abs(got-want) / math.Max(1.0, math.Abs(want))
			if d > worst {
				worst = d
			}
			if d > valueTol {
				fmt.Printf("  %s (%s): table says %.17g, the server says %.17g\n",
					r[0], r[1], want, got)
				bad++
			}
		}
	}
	for _, r := range repoRows {
		v, err := c.callTool("check_repo", map[string]any{
			"pattern": unescape(r[1]),
			"path":    filepath.Join(root, r[2]),
			"regex":   r[3] == "1",
		})
		if err != nil {
			return err
		}
		hits := 0
		if v.Evidence != "" {
			hits = len(strings.Split(v.Evidence, "\n"))
		}
		wantHits, err := strconv.Atoi(r[5])
		if err != nil {
			return fmt.Errorf("case %s: unreadable hit count %q", r[0], r[5])
		}
		if v.Status != r[4] || hits != wantHits {
			fmt.Printf("  %s (%s in %s): table says %s/%d, the server says %s/%d\n",
				r[0], r[1], r[2], r[4], wantHits, v.Status, hits)
			bad++
		}
	}
	if bad > 0 {
		return fmt.Errorf("the server disagrees with the recorded verdicts on %d of %d cases",
			bad, len(mathRows)+len(repoRows))
	}
	fmt.Printf("  tools/call: %d math and %d repo verdicts came back over the wire unchanged, "+
		"worst value disagreement %.1e relative\n", len(mathRows), len(repoRows), worst)
	return nil
}

func main() {
	root := flag.String("root", ".", "repository root")
	python := flag.String("python", "python3", "interpreter that can import src.groundcheck")
	flag.Parse()

	abs, err := filepath.Abs(*root)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := run(abs, *python); err != nil {
		fmt.Printf("Go: %v\n", err)
		os.Exit(1)
	}
	fmt.Println("Go drove the real MCP server and everything it returned matched")
}
