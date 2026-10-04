"""Headset-relative playback with persistent GPU textures and optional audio."""
import ctypes as C
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import wave

from .gl_texture import GLTextures
from .media import FPS, audio_samples, decode
from .overlay import Overlay, TextureBounds
from .settings import rgba


def log(**values):
    print(json.dumps(values), flush=True)


def stop(signum, frame):
    raise RuntimeError(f'Player stopped by signal {signum}')


def runtime_directory():
    return Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))


def already_played(stamp, boot_id):
    return stamp.is_file() and not stamp.is_symlink() and stamp.read_text().strip() == boot_id


def prepare_audio(movie, seconds):
    samples = audio_samples(movie, seconds)
    with tempfile.NamedTemporaryFile(prefix='steam-frame-intro-', suffix='.wav',
                                     dir=runtime_directory(), delete=False) as file:
        path = Path(file.name)
    try:
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(samples)
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def finish_audio(process):
    try:
        process.wait(timeout=.35)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    diagnostics = process.stderr.read().decode(errors='replace')[:1000]
    process.stderr.close()
    log(audio_exit=process.returncode, audio_diagnostics=diagnostics)


def play(preferences, paths, probe=False, startup=False):
    preferences.validate()
    stamp = boot_id = None
    if startup:
        stamp = runtime_directory() / 'steam-frame-intro.played'
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if already_played(stamp, boot_id):
            log(skipped='Already played during this boot')
            return
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(sig, stop)
    signal.alarm(28)
    vr = gpu = audio_file = audio_process = None
    shown = finished = False
    try:
        movie = paths.movie(preferences)
        info, frames = decode(movie, preferences, probe)
        frame_size = info.width * info.height * 4
        count = len(frames) // frame_size
        if preferences.audio and info.has_audio and not probe:
            audio_file = prepare_audio(movie, count / FPS)
        gpu = GLTextures()
        log(gpu_renderer=gpu.renderer, opengl_version=gpu.version)
        vr = Overlay(wait_seconds=5 if startup else 0, runtime=preferences.runtime)
        background = vr.create('background', 100, 1.1, 20000)
        logo = vr.create('movie', preferences.width, 1, 20001)
        background_texture = gpu.create(1, 1, rgba(preferences.background))
        movie_textures = [gpu.create(info.width, info.height, frames[:frame_size]) for _ in range(3)]
        vr.texture(background, background_texture)
        vr.texture(logo, movie_textures[0])
        vr.call('SetOverlayTextureBounds', logo, C.byref(TextureBounds(0, 1, 1, 0)))
        if probe:
            log(probe_ok=True, interface=vr.version, hidden_overlays=2,
                background=preferences.background, width_pixels=info.width,
                height_pixels=info.height, has_audio=info.has_audio)
            return
        if audio_file:
            audio_process = subprocess.Popen(['pw-play', '--latency', '30ms', '--media-role',
                                               'Movie', str(audio_file)], stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE)
        vr.call('ShowOverlay', background)
        vr.call('ShowOverlay', logo)
        shown = True
        log(playing=True, frames=count, seconds=count / FPS, background=preferences.background,
            width_pixels=info.width, height_pixels=info.height, head_relative=True,
            audio=bool(audio_process), key_black=preferences.key_black)
        start = time.monotonic()
        for index in range(1, count):
            delay = start + index / FPS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            vr.call('WaitFrameSync', 100)
            texture = movie_textures[index % len(movie_textures)]
            gpu.update(texture, info.width, info.height,
                       frames[index * frame_size:(index + 1) * frame_size])
            vr.texture(logo, texture)
        delay = start + count / FPS - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        log(playback_finished=True, elapsed_seconds=round(time.monotonic() - start, 3))
        finished = True
    finally:
        # Each resource gets cleaned up even if another cleanup operation fails.
        try:
            if audio_process is not None:
                finish_audio(audio_process)
        finally:
            try:
                if vr is not None:
                    vr.close()
            finally:
                try:
                    if gpu is not None:
                        gpu.close()
                finally:
                    if audio_file is not None:
                        audio_file.unlink(missing_ok=True)
                    signal.alarm(0)
                    if shown:
                        log(overlays_removed=True)
    if finished and startup:
        if stamp.is_symlink():
            raise RuntimeError('Unexpected symlink at runtime stamp')
        stamp.write_text(boot_id + '\n')
