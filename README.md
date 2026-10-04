# Steam Frame Intro

Play a custom startup animation directly in front of your eyes, with a background color and optional sound, before the Steam dashboard loads.

The sequence is **initial static logo → SteamVR becomes ready → standalone intro → Steam**. This project uses a headset-relative OpenVR overlay. It does not replace the earliest boot image or play before the VR compositor is available.

## Features

- Choose a background using `#RRGGBB` or a color name.
- Use the Frame's installed startup movie or your own local video.
- Preserve landscape and portrait aspect ratios, including phone-video rotation.
- Play the video's audio through the current headset output, or mute it.
- Use persistent GPU textures to avoid the flicker seen with repeated raw-image uploads.
- Play once per boot; manual previews remain available.
- Prevent the full animation from repeating in the dashboard.
- Install entirely in the user home, without sudo, OS patches, or disabling write protection.
- Record the previous Steam startup selection and owned files for removal.

## Requirements and tested setup

This is an experimental Steam Frame / ARM64 Linux project. The original player was verified visually through complete boots on SteamOS `0.3.0`, VR build `20260922.6101926`, using OpenVR `IVROverlay_027` and the Adreno 750 / Zink / Turnip renderer. Other builds may change the required runtime files or Steam customization API.

The packaged player passed hidden GPU probes on that Frame with the installed movie and a custom widescreen clip, a hex background color, and black-pixel transparency. Its 11 automated tests also passed on the Frame. Installation, configuration, and removal are covered by filesystem tests with mocked Steam integration; the packaged installer has not yet been tested through a complete boot on a clean headset.

On the Frame you need:

- Developer Mode and SSH access.
- A running SteamVR session for the hidden probe and manual preview.
- Python 3.10 or later, FFmpeg / ffprobe, `timeout`, and user systemd.
- PipeWire's `pw-play` for audio.
- The installed ARM64 SteamVR library and its bundled OpenVR C header.

No Python packages are installed. The default movie is read from Steam's own installation; no Valve media is bundled in this repository.

Automatic dashboard suppression additionally needs Steam's **already-enabled local CEF debugger at `127.0.0.1:8080`**, with its shared UI context available. The installer does not enable a debugger or change network settings. If it is unavailable, use `--keep-dashboard-intro`; the standalone intro still works, but Steam may show its own intro afterward.

## Installation

Keep the Frame awake with SteamVR and Steam running. From your PC, connect using your own SSH credentials:

```powershell
ssh steamos@frame
```

Then, in the Frame shell:

```bash
git clone https://github.com/Blackskydk/steam-frame-intro.git
cd steam-frame-intro
python3 manage.py doctor
python3 manage.py probe
```

`doctor` inspects dependencies. `probe` decodes a frame and configures **hidden** overlays, then removes them; it does not display an intro, enable startup, or change Steam settings.

If Git is unavailable on the Frame, use GitHub's **Code → Download ZIP** on your PC, extract it, and copy the extracted project folder with `scp -r` to the Frame. Run the same Python commands inside that folder; Git is only needed to obtain the source.

For a visible preview, wear the awake headset and close the dashboard:

```bash
python3 manage.py play
```

The preview temporarily covers the view, then removes its overlays automatically. Once it looks right, install:

```bash
python3 manage.py install
```

The installer runs a hidden probe first, copies the program into your home, backs up the relevant Steam choices, and adds a user-directory service override. It does **not** restart the headset or Steam. Restart the Frame using its normal power menu when you are ready to watch the boot sequence.

If you want to retain Steam's dashboard intro, or the local debugger is unavailable:

```bash
python3 manage.py install --keep-dashboard-intro
```

Installation refuses to overwrite an existing project or unrelated files. On a failed partial installation, the recorded state is retained for `uninstall` where available; do not repeatedly install over it.

## Background color

Use a quoted hex color or a name (`black`, `white`, `red`, `green`, `blue`, `navy`, `purple`, `gray`):

```bash
~/.local/bin/steam-frame-intro configure --background '#182438'
~/.local/bin/steam-frame-intro play
```

Preferences apply to the next preview or boot. To choose a color during installation:

```bash
python3 manage.py install --background navy
```

The background fills the surrounding view. An opaque background encoded **inside the video** remains part of that video. For a logo on black, optionally key out near-black pixels so the chosen color also appears behind the logo:

```bash
~/.local/bin/steam-frame-intro configure --background '#182438' --key-black
```

Keying can also remove intentionally dark details. Turn it off with `--no-key-black`. Colors are stored as ordinary sRGB values.

## Custom animation

Copy your video to the Frame first. For example, from Windows PowerShell:

```powershell
scp "C:\Videos\my-intro.webm" steamos@frame:~/my-intro.webm
```

Then, on the Frame:

```bash
~/.local/bin/steam-frame-intro configure --movie ~/my-intro.webm
~/.local/bin/steam-frame-intro probe
~/.local/bin/steam-frame-intro play
```

The configuration command copies the clip into the managed installation, so the source file can subsequently be moved or removed. It does not delete your source. A different clip can also be selected at installation:

```bash
python3 manage.py install --movie ~/my-intro.mp4 --background '#182438'
```

WebM and MP4 are good starting points; other local video formats depend on the installed FFmpeg decoders. Movies are decoded at 30 fps with a maximum dimension of 450 pixels, preserving aspect ratio. File size is limited to 256 MiB, and playback is capped at 10 seconds. Short clips are recommended for boot; longer files are trimmed, not looped. The player accepts local files, not network URLs. Clips without an audio stream play silently.

Useful controls:

```bash
# Return to the Frame's installed movie.
~/.local/bin/steam-frame-intro configure --movie default

# Silence the intro, or restore its audio.
~/.local/bin/steam-frame-intro configure --mute
~/.local/bin/steam-frame-intro configure --audio

# Change virtual width or shorten playback.
~/.local/bin/steam-frame-intro configure --width 0.8 --max-duration 6

# Show saved preferences.
~/.local/bin/steam-frame-intro show
```

Width is measured in virtual meters at a distance of 1 meter. Allowed widths are 0.2–1.5 meters. The default is 0.65 meters.

## What gets installed

| Location | Purpose |
| --- | --- |
| `~/.local/share/steam-frame-intro/` | Player, helper modules, and copied custom clips |
| `~/.local/bin/steam-frame-intro` | Command-line launcher |
| `~/.config/steam-frame-intro/config.json` | Preferences |
| `~/.config/steam-frame-intro/installation.json` | Owned-file checksums and previous Steam selection |
| `~/.config/systemd/user/steam.service.d/80-steam-frame-intro.conf` | Runs the intro before Steam starts |
| `~/.local/share/Steam/config/uioverrides/movies/steam_frame_intro_skip.webm` | Optional silent dashboard placeholder |

Steam's existing service already starts after SteamVR. The new `ExecStartPre` runs the player with a 30-second outer timeout and ignores player failure so Steam can continue. The player has its own bounded work and cleanup. A runtime stamp prevents replay on subsequent Steam-service restarts during the same boot. Temporary audio and the stamp live in the user runtime directory, not the OS installation.

Dashboard suppression selects a one-frame silent black movie through Steam's existing customization API. The dashboard technically consumes that blank frame instead of its full animation. Shuffle is disabled while this selection is active, with its previous value recorded. Resume-movie preferences and installed Steam movies are left alone.

## Removal

Keep Steam running so its original selection can be restored, then run:

```bash
~/.local/bin/steam-frame-intro uninstall
```

If the launcher is missing, run this from the downloaded source folder:

```bash
python3 manage.py uninstall
```

Removal restores the recorded Steam selection and shuffle preference **only if this project's placeholder is still selected**. A later selection you made yourself is preserved. It removes checksum-matching owned files and empty folders, then reloads the user service manager. Modified managed files cause removal to stop rather than delete your edits. Unrelated files are preserved. No reboot is required.

## Troubleshooting

```bash
~/.local/bin/steam-frame-intro doctor
~/.local/bin/steam-frame-intro probe
journalctl --user -b -u steam.service --no-pager
```

- **Steam debugger unavailable:** keep Steam running; if your build does not already expose the local debugger, install with `--keep-dashboard-intro`.
- **Required SteamVR header/library missing:** `doctor` names the missing file. An alternate installed ARM64 runtime can be selected with `--runtime /absolute/path/to/steamvr`; it must include the corresponding bundled C header.
- **No sound:** check the current headset output/volume and whether the clip actually has audio. The player does not change system volume.
- **Color appears around a black rectangle:** that black rectangle is encoded in the movie; try `--key-black` for logo-style clips.
- **Existing prototype detected:** the earlier experimental `70-frame-intro.conf` must be removed or disabled first, along with its old player as appropriate. Preserve its setup if you want to return to it. This installer deliberately does not take over that separate experiment.
- **Player fails after a runtime update:** the timeout allows Steam to start. Diagnose with `doctor`/`probe` or remove the integration.

## Development

Standard-library tests run without a headset or third-party Python packages:

```bash
python3 -m unittest discover -s tests -v
```

Hardware rendering and complete boot behavior require a real Frame. GitHub Actions tests configuration validation, aspect ratios, ownership checks, restoration, and per-boot bookkeeping; CI does not emulate VR hardware.

## License

Project code is MIT licensed. Valve's startup media is not distributed here. Steam and Steam Frame are Valve trademarks; this is an independent community project.
