"""Validated preferences and user-directory paths."""
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

COLORS = {'black': '#000000', 'white': '#ffffff', 'red': '#ff0000',
          'green': '#008000', 'blue': '#0000ff', 'navy': '#000080',
          'purple': '#800080', 'gray': '#808080'}


def color(value):
    if not isinstance(value, str):
        raise ValueError('Background must be a color name or #RRGGBB')
    value = COLORS.get(value.lower(), value.lower())
    if not re.fullmatch(r'#[0-9a-f]{6}', value):
        raise ValueError('Use #RRGGBB, or: ' + ', '.join(COLORS))
    return value


def rgba(value):
    value = color(value)
    return bytes(int(value[i:i + 2], 16) for i in (1, 3, 5)) + b'\xff'


@dataclass
class Preferences:
    movie: str = 'default'
    background: str = '#000000'
    audio: bool = True
    width: float = .65
    max_duration: float = 10.0
    key_black: bool = False
    runtime: str = '/opt/steamvr'

    def validate(self):
        self.background = color(self.background)
        for name in ('audio', 'key_black'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name} must be true or false')
        for name, minimum, maximum in [('width', .2, 1.5), ('max_duration', .1, 10)]:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f'{name} must be between {minimum} and {maximum}')
        if not isinstance(self.movie, str) or not self.movie:
            raise ValueError('movie must be default or a local filename')
        if self.movie != 'default':
            path = Path(self.movie).expanduser()
            if not path.is_absolute():
                raise ValueError('Custom movie must use an absolute local path')
            self.movie = str(path)
        if not isinstance(self.runtime, str) or not PurePosixPath(self.runtime).is_absolute():
            raise ValueError('runtime must be an absolute SteamVR directory')
        return self

    @classmethod
    def load(cls, path):
        if not path.exists():
            return cls().validate()
        values = json.loads(path.read_text())
        if not isinstance(values, dict):
            raise ValueError('Configuration must be a JSON object')
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(unknown)))
        return cls(**values).validate()

    def save(self, path):
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.preferences-',
                                             delete=False) as file:
                temporary = Path(file.name)
                file.write(json.dumps(asdict(self), indent=2) + '\n')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Paths:
    home: Path

    @classmethod
    def current(cls):
        return cls(Path.home().resolve())

    @property
    def root(self):
        return self.home / '.local/share/steam-frame-intro'

    @property
    def config_dir(self):
        return self.home / '.config/steam-frame-intro'

    @property
    def config(self):
        return self.config_dir / 'config.json'

    @property
    def manifest(self):
        return self.config_dir / 'installation.json'

    @property
    def dropin(self):
        return self.home / '.config/systemd/user/steam.service.d/80-steam-frame-intro.conf'

    @property
    def launcher(self):
        return self.home / '.local/bin/steam-frame-intro'

    @property
    def steam_root(self):
        return self.home / '.local/share/Steam'

    @property
    def placeholder(self):
        return self.steam_root / 'config/uioverrides/movies/steam_frame_intro_skip.webm'

    @property
    def placeholder_url(self):
        return '/uioverrides/movies/steam_frame_intro_skip.webm'

    def movie(self, preferences):
        if preferences.movie == 'default':
            return self.steam_root / 'steamui/movies/steamframe_startup_450x450.webm'
        return Path(preferences.movie)


def runtime_files(runtime=None):
    root = Path(runtime or '/opt/steamvr')
    header = root / 'tools/hellovr_vulkan_linux/src/openvr/headers/openvr_capi.h'
    library = root / 'bin/linuxarm64/libopenvr_api.so'
    for file in (header, library):
        if not file.is_file():
            raise ValueError(f'Required SteamVR file is missing: {file}')
    return header, library
