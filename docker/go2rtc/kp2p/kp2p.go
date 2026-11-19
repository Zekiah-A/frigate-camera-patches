package kp2p

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/AlexxIT/go2rtc/internal/app"
	"github.com/AlexxIT/go2rtc/internal/streams"
	"github.com/AlexxIT/go2rtc/pkg/core"
	"github.com/pion/rtp"
)

type credentials struct {
	User     string `json:"talk_user"`
	Password string `json:"talk_password"`
}
type endpoint struct {
	address string
	account credentials
	mu      sync.Mutex
	owner   *core.Sender
	active  *session
	timer   *time.Timer
	last    time.Time
}

func Init() {
	var config struct {
		KP2P map[string]string `yaml:"kp2p"`
	}
	app.LoadConfig(&config)
	devices := map[string]*endpoint{}
	valid := regexp.MustCompile("^[a-zA-Z0-9_-]+$")
	for id, address := range config.KP2P {
		if !valid.MatchString(id) {
			continue
		}
		if _, _, err := net.SplitHostPort(address); err != nil {
			continue
		}
		// Stream metadata and logs contain only the alias. Mount the separate
		// camera-local speaker account read-only, outside the go2rtc config.
		path := "/run/secrets/kp2p-" + id + ".json"
		info, err := os.Stat(path)
		if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
			continue
		}
		b, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var account credentials
		if err = json.Unmarshal(b, &account); err != nil || account.User == "" {
			continue
		}
		devices[id] = &endpoint{address: address, account: account}
	}
	streams.HandleFunc("kp2p", func(source string) (core.Producer, error) {
		dev := devices[strings.TrimPrefix(source, "kp2p:")]
		if dev == nil {
			return nil, errors.New("speaker is not configured")
		}
		return newProducer(dev), nil
	})
}

type producer struct {
	core.Connection
	ctx     context.Context
	cancel  context.CancelFunc
	device  *endpoint
	done    chan struct{}
	once    sync.Once
	mu      sync.Mutex
	stopped bool
}

func newProducer(dev *endpoint) *producer {
	media := &core.Media{Kind: core.KindAudio, Direction: core.DirectionSendonly, Codecs: []*core.Codec{{Name: core.CodecPCMA, ClockRate: 8000, PayloadType: 8}}}
	ctx, cancel := context.WithCancel(context.Background())
	return &producer{ctx: ctx, cancel: cancel, Connection: core.Connection{ID: core.NewID(), FormatName: "kp2p", Protocol: "ws", Medias: []*core.Media{media}}, device: dev, done: make(chan struct{})}
}
func (p *producer) AddTrack(media *core.Media, _ *core.Codec, track *core.Receiver) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopped {
		return errors.New("speaker closed")
	}
	sender := core.NewSender(media, track.Codec)
	failed := false
	var last uint16
	var received bool
	sender.Handler = func(packet *rtp.Packet) {
		if failed || len(packet.Payload) == 0 || len(packet.Payload) > 1600 {
			return
		}
		if p.ctx.Err() != nil || (received && int16(packet.SequenceNumber-last) <= 0) {
			return
		}
		received = true
		last = packet.SequenceNumber
		failed = !p.device.write(p.ctx, sender, packet.Payload)

	}
	p.Senders = append(p.Senders, sender)
	sender.WithParent(track).Start()
	return nil
}
func (p *producer) Start() error { <-p.done; return nil }
func (p *producer) Stop() error {
	p.once.Do(func() {
		p.cancel()
		p.mu.Lock()
		p.stopped = true
		for _, sender := range p.Senders {
			sender.Close()
			p.device.release(sender)
		}
		p.mu.Unlock()
		close(p.done)
	})
	return nil
}

const idleTimeout = 1500 * time.Millisecond

func (d *endpoint) write(ctx context.Context, sender *core.Sender, payload []byte) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if ctx.Err() != nil || (d.owner != nil && d.owner != sender) {
		return false
	}
	if d.active == nil {
		s, err := dial(ctx, d.address, d.account)
		if err != nil {
			return false
		}
		d.active = s
		d.owner = sender
	}
	if ctx.Err() != nil {
		d.finish()
		return false
	}
	if err := d.active.audio(payload); err != nil {
		d.finish()
		return false
	}
	d.last = time.Now()
	if d.timer == nil {
		d.timer = time.AfterFunc(idleTimeout, func() { d.expire(sender) })
	} else {
		d.timer.Reset(idleTimeout)
	}
	return true
}
func (d *endpoint) expire(sender *core.Sender) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.owner == sender && time.Since(d.last) >= idleTimeout {
		d.finish()
	}
}
func (d *endpoint) release(sender *core.Sender) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.owner == sender {
		d.finish()
	}
}
func (d *endpoint) finish() {
	if d.timer != nil {
		d.timer.Stop()
		d.timer = nil
	}
	d.active.close()
	d.active = nil
	d.owner = nil
}
