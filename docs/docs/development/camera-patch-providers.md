---
title: Camera Patch Providers
---

Camera patches add camera-specific controls to the single-camera live view. A
provider is opt-in on each camera and is included when building the Frigate
image. Add its class to the explicit registry in
`frigate/camera_patches/registry.py`. YAML selects a provider by ID and supplies
its settings. Each provider may appear once per camera.

```yaml
cameras:
  front_door:
    # Existing FFmpeg and camera configuration goes here.
    patches:
      - id: example_provider
        settings:
          password_env: FRIGATE_FRONT_DOOR_PASSWORD
```

`example_provider` above is an illustrative registry ID. Implement
`CameraPatch` from `frigate/camera_patches/provider.py`. Validate settings
without I/O in `validate_settings`. Require named secret references and resolve
them server-side. Keep resolved secrets out of state, errors, and logs. Avoid
camera access in the constructor.

Declare controls with unique IDs, labels, and `toggle`, `select`, or `button`
kinds. For select controls, declare all accepted values. To bound duration,
declare `minimum`, `maximum`, and `default` in seconds. Use
`duration_values` to enable duration only for particular select values.

Implement async `state()`, `command()`, and any needed `close()`. Controls
marked `device` report genuine device readback, keyed by the control ID or
`state_key`. Use `ack` for operations where the provider can verify command
receipt. Where a separate camera query can still observe the underlying output,
use an explicit `state_key` to expose that readback. Provider-specific errors may use `PatchError`
with a safe HTTP message. Bound network calls and keep state reads free of
writes. Keep transient sessions or timers in memory, and release provider
resources in `close()`. Prefer a device-side expiry when the device supports
it, so a server outage does not leave a timed output on.

The API serializes requests per camera and closes obsolete instances after an
applied config replacement, or during API shutdown. The status route is scoped
to users with camera access. Commands require the admin role and accept an
explicit value and optional duration. Configuration and command state are
separate. Command handling does not persist anything to Frigate's YAML or
runtime override store.

## ANNKE I51CP NetSDK

Enable `netsdk` on an I51CP camera to expose manual and smart lighting, the
camera microphone, image orientation, and siren controls.

```yaml
cameras:
  front_door:
    # Keep the camera's existing FFmpeg and ONVIF configuration.
    patches:
      - id: netsdk
        settings:
          model: annke_i51cp
          host: camera.example
          port: 80
          username: camera-user
          password_env: FRIGATE_NETSDK_FRONT_DOOR_PASSWORD
```

Set the named credential in the container environment or as a Frigate secret.
The variable must begin with `FRIGATE_NETSDK_`. Set it explicitly to an empty
value if the camera account has no password. The provider speaks the device's
local HTTP NetSDK protocol. Limit access to the camera to trusted networks.

Light On and Off confirm the camera's reported lamp state. Smart light
returns control to the camera, whose lamp behavior depends on the scene.
Starting and stopping the siren wait for the camera to accept the command.
The separate sound status can update later. Microphone controls affect the
camera's outgoing audio stream. Use Frigate's player volume to control local
playback. A live stream started while the camera microphone was disabled may
need to reconnect to pick up the audio track.
