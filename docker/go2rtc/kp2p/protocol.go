// Package kp2p implements the local WebSocket speaker backchannel.
package kp2p

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"math"
	"net/url"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

var le = binary.LittleEndian

const ioTimeout = 3 * time.Second

type session struct {
	conn        *websocket.Conn
	sid, ticket uint32
	timestamp   uint64
	writeMu     sync.Mutex
}

func words(values ...uint32) []byte {
	b := make([]byte, len(values)*4)
	for i, value := range values {
		le.PutUint32(b[i*4:], value)
	}
	return b
}

func dial(ctx context.Context, address string, account credentials) (*session, error) {
	u := url.URL{Scheme: "ws", Host: address, Path: "/"}
	dialer := websocket.Dialer{HandshakeTimeout: ioTimeout}
	conn, _, err := dialer.DialContext(ctx, u.String(), nil)
	if err != nil {
		return nil, errors.New("speaker connection failed")
	}
	var nonce [4]byte
	if _, err = rand.Read(nonce[:]); err != nil {
		conn.Close()
		return nil, err
	}
	s := &session{conn: conn, sid: 1 + le.Uint32(nonce[:])%10000}
	conn.SetReadLimit(1 << 20)
	cancelHandshake := context.AfterFunc(ctx, func() { conn.Close() })
	defer cancelHandshake()
	if err = s.open(account); err != nil {
		conn.Close()
		return nil, err
	}
	return s, nil
}

func (s *session) open(account credentials) error {
	hello, _ := hex.DecodeString("d9ffcc028c38eed2d199ac6026947fae")
	ack, _ := hex.DecodeString("96d5390d12fcbe8f4790d932ccd849f3")
	s.conn.SetWriteDeadline(time.Now().Add(ioTimeout))
	if err := s.conn.WriteMessage(websocket.BinaryMessage, append(hello, words(s.sid)...)); err != nil {
		return err
	}
	s.conn.SetReadDeadline(time.Now().Add(ioTimeout))
	_, response, err := s.conn.ReadMessage()
	if err != nil || !bytes.Equal(response, ack) {
		return errors.New("speaker handshake rejected")
	}
	if err = s.send(20, words(s.sid, 0)); err != nil {
		return err
	}
	if _, err = s.receive(21); err != nil {
		return err
	}
	command, body, err := login(account)
	if err != nil {
		return err
	}
	if err = s.command(command, body); err != nil {
		return err
	}
	if err = s.command(60, words(1, 0)); err != nil {
		return err
	}
	s.conn.SetReadDeadline(time.Time{})
	s.timestamp = uint64(time.Now().UnixMilli())
	// Drain keepalives for the duration of the call. Closing the socket ends the reader.
	go func() { _, _ = s.receive(0) }()
	return nil
}

func login(account credentials) (uint32, []byte, error) {
	if len(account.User) >= 1024 || len(account.Password) >= 1024 {
		return 0, nil, errors.New("speaker credential too long")
	}
	if len(account.User) >= 32 || len(account.Password) >= 32 {
		b := make([]byte, 2048)
		copy(b, account.User)
		copy(b[1024:], account.Password)
		return 140, b, nil
	}
	b := make([]byte, 64)
	copy(b, account.User)
	copy(b[32:], account.Password)
	cipher, err := aes.NewCipher([]byte("~!JUAN*&Vision-="))
	if err != nil {
		return 0, nil, err
	}
	for i := 0; i < len(b); i += aes.BlockSize {
		cipher.Encrypt(b[i:i+aes.BlockSize], b[i:i+aes.BlockSize])
	}
	return 10, b, nil
}

func (s *session) send(op uint32, data []byte) error {
	packet := make([]byte, 32, len(data)+32)
	le.PutUint32(packet, 0xdecdbcab)
	le.PutUint32(packet[4:], op)
	packet[11] = 1
	le.PutUint32(packet[16:], s.sid)
	le.PutUint32(packet[28:], uint32(len(data)))
	packet = append(packet, data...)
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	s.conn.SetWriteDeadline(time.Now().Add(ioTimeout))
	if err := s.conn.WriteMessage(websocket.BinaryMessage, words(0xfeefface, uint32(len(packet)))); err != nil {
		return err
	}
	return s.conn.WriteMessage(websocket.BinaryMessage, packet)
}

func (s *session) receive(op uint32) ([]byte, error) {
	for {
		_, b, err := s.conn.ReadMessage()
		if err != nil {
			return nil, err
		}
		if len(b) < 32 || le.Uint32(b) != 0xdecdbcab {
			continue
		}
		if int(le.Uint32(b[28:])) != len(b)-32 {
			return nil, errors.New("invalid speaker packet")
		}
		cmd := le.Uint32(b[4:])
		if cmd == 17 {
			if err = s.send(18, b[32:]); err != nil {
				return nil, err
			}
			continue
		}
		if cmd != op {
			continue
		}
		if le.Uint32(b[24:]) != 0 {
			return nil, errors.New("speaker rejected transport request")
		}
		return b[32:], nil
	}
}

func (s *session) command(cmd uint32, payload []byte) error {
	s.ticket++
	body := append(words(0x4b503250, 1, s.ticket, cmd, 0, uint32(len(payload))), payload...)
	if err := s.send(19, body); err != nil {
		return err
	}
	s.conn.SetReadDeadline(time.Now().Add(ioTimeout))
	for {
		response, err := s.receive(19)
		if err != nil {
			return err
		}
		if len(response) < 24 || le.Uint32(response) != 0x4b503250 || le.Uint32(response[8:]) != s.ticket || le.Uint32(response[12:]) != cmd+1 {
			continue
		}
		if le.Uint32(response[16:]) != 0 {
			return errors.New("speaker rejected command")
		}
		return nil
	}
}

func audioFrame(payload []byte, timestamp uint64) []byte {
	frame := make([]byte, 64, len(payload)+64)
	copy(frame, words(0x4652414d, 0x01000000, 2, uint32(len(payload))))
	le.PutUint64(frame[16:], timestamp)
	copy(frame[32:], "g711a")
	copy(frame[40:], words(8000, 16, 1, math.Float32bits(2)))
	return append(frame, payload...)
}
func (s *session) audio(payload []byte) error {
	err := s.send(19, audioFrame(payload, s.timestamp))
	s.timestamp += uint64(len(payload)) / 8
	return err
}
func (s *session) close() {
	s.ticket++
	body := append(words(0x4b503250, 1, s.ticket, 60, 0, 8), words(2, 0)...)
	_ = s.send(19, body)
	_ = s.conn.Close()
}
