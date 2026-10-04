"""Installation, configuration, diagnostics, and playback commands."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .install import configure, doctor, install, uninstall
from .player import play
from .settings import Paths, Preferences


def options(parser):
    parser.add_argument('--movie', help='Local video path, or default for the installed Frame movie')
    parser.add_argument('--background', help='Background color name or quoted #RRGGBB')
    parser.add_argument('--width', type=float, help='Movie width in virtual meters (0.2–1.5)')
    parser.add_argument('--max-duration', type=float, help='Clip playback after this many seconds (0.1–10)')
    parser.add_argument('--runtime', help='Absolute directory of the installed ARM64 SteamVR runtime')
    audio = parser.add_mutually_exclusive_group()
    audio.add_argument('--audio', dest='audio', action='store_true', default=None)
    audio.add_argument('--mute', dest='audio', action='store_false', default=None)
    key = parser.add_mutually_exclusive_group()
    key.add_argument('--key-black', dest='key_black', action='store_true', default=None,
                     help='Make near-black video pixels transparent over the chosen background')
    key.add_argument('--no-key-black', dest='key_black', action='store_false', default=None)


def preferences_for(args, paths):
    preferences = Preferences.load(paths.config) if args.command != 'install' else Preferences()
    for name in ('movie', 'background', 'width', 'max_duration', 'runtime', 'audio', 'key_black'):
        value = getattr(args, name, None)
        if value is not None:
            if name == 'movie' and value != 'default':
                value = str(Path(value).expanduser().resolve())
            setattr(preferences, name, value)
    return preferences.validate()


def main(argv=None):
    parser = argparse.ArgumentParser(prog='steam-frame-intro',
                                     description='A standalone startup animation for Steam Frame.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'install', 'configure', 'probe', 'play'):
        command = commands.add_parser(name)
        options(command)
        if name == 'install':
            command.add_argument('--keep-dashboard-intro', action='store_true',
                                 help='Do not replace Steam’s dashboard intro with a silent blank frame')
        if name == 'play':
            command.add_argument('--startup', action='store_true',
                                 help='Internal: wait briefly for SteamVR, and play only once per boot')
    commands.add_parser('show', help='Show the current saved preferences')
    commands.add_parser('uninstall', help='Remove owned files and restore the original Steam selection')
    args = parser.parse_args(argv)
    paths = Paths.current()
    try:
        if args.command == 'uninstall':
            result = uninstall(paths)
        elif args.command == 'show':
            result = Preferences.load(paths.config).__dict__
        else:
            preferences = preferences_for(args, paths)
            if args.command == 'doctor':
                result = doctor(preferences, paths)
                print(json.dumps(result, indent=2))
                if not result['ok']:
                    raise SystemExit(1)
                return
            if args.command == 'install':
                result = install(preferences, paths, not args.keep_dashboard_intro)
            elif args.command == 'configure':
                result = configure(preferences, paths)
            else:
                play(preferences, paths, probe=args.command == 'probe',
                     startup=getattr(args, 'startup', False))
                return
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        raise SystemExit(1)
