# KP2P speaker backchannel

This image builds the bundled go2rtc revision with an audio-only KP2P source.
Use the normal Frigate WebRTC player on localhost or a trusted HTTPS origin.
Give the browser a reachable LAN ICE candidate and forward port 8555 for media.
The receive stream continues to use the camera's regular RTSP or FFmpeg input.

```yaml
go2rtc:
  kp2p:
    porch: camera.example.lan:10000
  streams:
    porch:
      - rtsp://camera.example.lan/live
      - kp2p:porch
cameras:
  porch:
    live:
      push_to_talk: true
      streams:
        Live: porch
```

Mount the separately provisioned speaker account read-only at
`/run/secrets/kp2p-porch.json` with mode 0600. It has two string fields,
`talk_user` and `talk_password`. Use a unique alias for each physical speaker.
The alias is the only information carried in the backchannel source URL.
A capability probe only advertises 8 kHz PCMA. The first incoming audio packet
opens the device call. Track teardown and a 1.5-second idle timeout release it.

The adapter and its loopback protocol test live together under `kp2p/`.
The registration patch adds the module to go2rtc's normal startup list.
