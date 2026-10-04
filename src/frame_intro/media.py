"""Bounded local-file decoding, preserving display aspect ratio."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess

FPS = 30
MAX_SIDE = 450


@dataclass(frozen=True)
class MovieInfo:
    width: int
    height: int
    has_audio: bool


def ratio(value):
    if not value or value in ('N/A', '0:1'):
        return 1.0
    left, right = value.split(':')
    result = float(left) / float(right)
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Invalid video pixel aspect ratio')
    return result


def parse_info(data):
    if not isinstance(data, dict) or not isinstance(data.get('streams'), list):
        raise ValueError('Invalid ffprobe stream metadata')
    videos = [s for s in data['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise ValueError('Movie has no video stream')
    stream = videos[0]
    width, height = float(stream['width']), float(stream['height'])
    if not (0 < width <= 16384 and 0 < height <= 16384):
        raise ValueError('Unsupported video dimensions')
    width *= ratio(stream.get('sample_aspect_ratio'))
    rotations = [s['rotation'] for s in stream.get('side_data_list', []) if 'rotation' in s]
    rotation = float(rotations[0] if rotations else stream.get('tags', {}).get('rotate', 0))
    if not math.isfinite(rotation):
        raise ValueError('Invalid video rotation')
    if round(rotation) % 180:
        width, height = height, width
    if not .125 <= width / height <= 8:
        raise ValueError('Video aspect ratio must be between 1:8 and 8:1')
    scale = min(1, MAX_SIDE / max(width, height))
    return MovieInfo(max(2, round(width * scale)), max(2, round(height * scale)),
                     any(s.get('codec_type') == 'audio' for s in data['streams']))


def inspect(movie):
    movie = Path(movie)
    if not movie.is_file():
        raise ValueError(f'Movie file does not exist: {movie}')
    result = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                             '-show_streams', '-of', 'json', str(movie)],
                            capture_output=True, timeout=5, check=True)
    return parse_info(json.loads(result.stdout))


def decode(movie, preferences, probe=False):
    info = inspect(movie)
    filters = f'scale={info.width}:{info.height},setsar=1,fps={FPS},format=rgba'
    if preferences.key_black:
        filters += ',colorkey=0x000000:0.1:0.04'
    command = ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2',
               '-protocol_whitelist', 'file,pipe', '-i', str(movie), '-map', '0:V:0',
               '-an', '-vf', filters, '-t', str(preferences.max_duration),
               '-pix_fmt', 'rgba', '-f', 'rawvideo']
    if probe:
        command += ['-frames:v', '1']
    command += ['pipe:1']
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=8, check=True)
    size = info.width * info.height * 4
    if not result.stdout or len(result.stdout) % size:
        raise ValueError('Movie did not decode into complete RGBA frames')
    # Filter duration limits output, and reject an unexpectedly large decoder result.
    if len(result.stdout) > size * (math.ceil(preferences.max_duration * FPS) + 2):
        raise ValueError('Decoder exceeded the configured movie limit')
    return info, result.stdout


def audio_samples(movie, seconds):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin',
                             '-protocol_whitelist', 'file,pipe', '-i', str(movie),
                             '-map', '0:a:0', '-vn', '-t', str(seconds), '-ar', '48000',
                             '-ac', '2', '-f', 's16le', 'pipe:1'], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=5, check=True)
    if not result.stdout:
        raise ValueError('Movie audio decoded to no samples')
    return result.stdout
