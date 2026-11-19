package kp2p

import (
	"context"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/core"
	"github.com/gorilla/websocket"
)

func TestSyntheticCall(t *testing.T) {
	type event struct {
		cmd  uint32
		body []byte
	}
	seen := make(chan event, 20)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upgrader := websocket.Upgrader{}
		c, e := upgrader.Upgrade(w, r, nil)
		if e != nil {
			return
		}
		defer c.Close()
		_, _, e = c.ReadMessage()
		if e != nil {
			return
		}
		ack, _ := hex.DecodeString("96d5390d12fcbe8f4790d932ccd849f3")
		_ = c.WriteMessage(websocket.BinaryMessage, ack)
		for {
			_, _, e = c.ReadMessage()
			if e != nil {
				return
			}
			_, b, e := c.ReadMessage()
			if e != nil {
				return
			}
			cmd := le.Uint32(b[4:])
			body := b[32:]
			var reply []byte
			if cmd == 20 {
				le.PutUint32(b[4:], 21)
				reply = b[:32]
			} else if le.Uint32(body) == 0x4b503250 {
				op := le.Uint32(body[12:])
				seen <- event{op, body[24:]}
				le.PutUint32(body[12:], op+1)
				reply = b[:56]
			} else {
				seen <- event{19, body}
				continue
			}
			le.PutUint32(reply[28:], uint32(len(reply)-32))
			if c.WriteMessage(websocket.BinaryMessage, reply) != nil {
				return
			}
		}
	}))
	defer server.Close()
	d := &endpoint{address: strings.TrimPrefix(server.URL, "http://"), account: credentials{"test", "test"}}
	p := newProducer(d)
	defer p.Stop()
	m := p.GetMedias()[0]
	track := core.NewReceiver(m, m.Codecs[0])
	if err := p.AddTrack(m, m.Codecs[0], track); err != nil {
		t.Fatal(err)
	}
	if len(seen) != 0 || d.active != nil {
		t.Fatal("capability probe opened call")
	}
	owner := p.Senders[0]
	if !d.write(context.Background(), owner, []byte{0xd5, 0xd5}) {
		t.Fatal("could not send synthetic audio")
	}
	if d.write(context.Background(), new(core.Sender), []byte{0xd5}) {
		t.Fatal("second sender acquired active call")
	}
	for _, want := range []uint32{10, 60, 19, 60} {
		select {
		case got := <-seen:
			if got.cmd != want {
				t.Fatalf("command %d, want %d", got.cmd, want)
			}
			if want == 19 && (len(got.body) != 66 || string(got.body[32:37]) != "g711a" || got.body[64] != 0xd5) {
				t.Fatal("invalid A-law frame")
			}
		case <-time.After(3 * time.Second):
			t.Fatalf("timeout waiting for %d", want)
		}
	}
}
