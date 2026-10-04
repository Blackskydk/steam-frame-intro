"""Install only in the user's home, record ownership, and restore Steam choices."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

from . import steam
from .media import inspect
from .player import play
from .settings import Preferences, runtime_files

DROPIN = '''# Managed by steam-frame-intro. No installed OS unit is modified.
[Service]
ExecStartPre=-/usr/bin/timeout 30s /usr/bin/python3 %h/.local/share/steam-frame-intro/run.py play --startup
'''
RUNNER = '''#!/usr/bin/python3
import sys
sys.dont_write_bytecode = True
from frame_intro.cli import main
if __name__ == '__main__':
    main()
'''
MAX_CUSTOM_BYTES = 256 * 1024 * 1024


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def doctor(preferences, paths):
    issues = []
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        issues.append('This player requires the ARM64 Linux Steam Frame runtime')
    for program in ('ffmpeg', 'ffprobe', 'timeout', 'systemctl') + (('pw-play',) if preferences.audio else ()):
        if not shutil.which(program):
            issues.append(f'Missing installed program: {program}')
    try:
        runtime_files(preferences.runtime)
        inspect(paths.movie(preferences))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        issues.append(str(error))
    legacy = paths.dropin.parent / '70-frame-intro.conf'
    return {'ok': not issues, 'issues': issues, 'legacy_intro_detected': legacy.exists(),
            'movie': str(paths.movie(preferences)), 'runtime': preferences.runtime}


def safe_file(path, paths):
    path = Path(path)
    allowed = (paths.root, paths.config_dir)
    known = (paths.dropin, paths.launcher, paths.placeholder)
    if path.is_symlink() or path.resolve() != path:
        raise ValueError(f'Refusing symlink or redirected path: {path}')
    if path not in known and not any(path.is_relative_to(root) for root in allowed):
        raise ValueError(f'Path is outside the managed installation: {path}')
    if not path.is_relative_to(paths.home):
        raise ValueError('Installation must stay inside the user home')
    return path


def write_new(path, data, paths, files, mode=0o644):
    safe_file(path, paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as file:
        file.write(data)
    os.chmod(path, mode)
    files[str(path)] = digest(path)


def manifest_save(paths, manifest):
    safe_file(paths.manifest, paths)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=paths.config_dir, prefix='.state-',
                                         delete=False) as file:
            temporary = Path(file.name)
            json.dump(manifest, file, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, paths.manifest)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def manifest_load(paths):
    safe_file(paths.manifest, paths)
    value = json.loads(paths.manifest.read_text())
    if (not isinstance(value, dict) or value.get('project') != 'steam-frame-intro'
            or value.get('schema') != 1 or not isinstance(value.get('files'), dict)
            or not all(isinstance(name, str) and isinstance(checksum, str) and len(checksum) == 64
                       for name, checksum in value['files'].items())):
        raise ValueError('Unexpected installation manifest')
    return value


def verify_owned(paths, manifest):
    changed = []
    for name, checksum in manifest['files'].items():
        path = safe_file(Path(name), paths)
        if path.exists() and (not path.is_file() or digest(path) != checksum):
            changed.append(str(path))
    if changed:
        raise ValueError('Managed files were modified. Preserve them elsewhere before retrying:\n' + '\n'.join(changed))


def copy_movie(source, paths, files):
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_CUSTOM_BYTES:
        raise ValueError('Custom movie must be a regular file of at most 256 MiB')
    inspect(source)
    suffix = source.suffix.lower() if len(source.suffix) <= 12 else '.video'
    target = paths.root / 'media' / ('custom-' + digest(source)[:16] + suffix)
    safe_file(target, paths)
    if target.exists():
        if str(target) not in files or digest(target) != digest(source):
            raise ValueError('Unexpected existing custom movie')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as reader, target.open('xb') as writer:
        shutil.copyfileobj(reader, writer)
    files[str(target)] = digest(target)
    return target


def placeholder(paths, files):
    safe_file(paths.placeholder, paths)
    if paths.placeholder.exists():
        raise ValueError('Skip movie already exists; refusing to overwrite it')
    paths.placeholder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.frame-intro-', dir=paths.placeholder.parent) as temporary:
        output = Path(temporary) / 'skip.webm'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
                        'color=c=black:s=16x16:r=60', '-frames:v', '1', '-an', '-c:v',
                        'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', str(output)],
                       timeout=8, check=True)
        write_new(paths.placeholder, output.read_bytes(), paths, files)


def install(preferences, paths, suppress_dashboard=True):
    preferences.validate()
    for path in (paths.root, paths.config, paths.manifest, paths.dropin, paths.launcher):
        safe_file(path, paths)
        if path.exists():
            raise ValueError(f'Already exists; uninstall or preserve it before installing: {path}')
    status = doctor(preferences, paths)
    if not status['ok'] or status['legacy_intro_detected']:
        raise ValueError(json.dumps(status, indent=2) + '\nRemove the earlier prototype override first if present.')
    # Check everything that can be checked before enabling startup or changing Steam.
    play(preferences, paths, probe=True)
    before = steam.snapshot() if suppress_dashboard else None
    files = {}
    manifest = {'project': 'steam-frame-intro', 'schema': 1, 'files': files,
                'dashboard_before': before, 'dashboard_url': paths.placeholder_url if suppress_dashboard else None}
    paths.root.mkdir(parents=True)
    manifest_save(paths, manifest)
    for source in Path(__file__).parent.glob('*.py'):
        write_new(paths.root / 'frame_intro' / source.name, source.read_bytes(), paths, files)
        manifest_save(paths, manifest)
    license_file = Path(__file__).resolve().parents[2] / 'LICENSE'
    if license_file.is_file():
        write_new(paths.root / 'LICENSE', license_file.read_bytes(), paths, files)
    write_new(paths.root / 'run.py', RUNNER.encode(), paths, files, 0o755)
    launcher = '#!/usr/bin/python3\nimport sys\nsys.dont_write_bytecode = True\nsys.path.insert(0, ' + repr(str(paths.root)) + ')\nfrom frame_intro.cli import main\nmain()\n'
    write_new(paths.launcher, launcher.encode(), paths, files, 0o755)
    manifest_save(paths, manifest)
    if preferences.movie != 'default':
        preferences.movie = str(copy_movie(preferences.movie, paths, files))
    preferences.save(paths.config)
    files[str(paths.config)] = digest(paths.config)
    manifest_save(paths, manifest)
    if suppress_dashboard:
        placeholder(paths, files)
    # Record original Steam choices before making the native selection.
    manifest_save(paths, manifest)
    try:
        if suppress_dashboard:
            selected = steam.select(paths.placeholder_url)
            if selected != paths.placeholder_url:
                raise RuntimeError('Steam did not select the skip movie')
        write_new(paths.dropin, DROPIN.encode(), paths, files)
        manifest_save(paths, manifest)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        loaded = subprocess.run(['systemctl', '--user', 'show', 'steam.service', '-p', 'DropInPaths'],
                                capture_output=True, text=True, check=True)
        if str(paths.dropin) not in loaded.stdout:
            raise RuntimeError('Steam service did not load the intro override')
    except BaseException:
        # Keep the recorded state available even if Steam becomes unavailable.
        manifest_save(paths, manifest)
        raise RuntimeError('Installation did not finish. Original settings and owned files are recorded; '
                           'run uninstall to recover before retrying.')
    return {'installed': str(paths.root), 'startup_enabled': True,
            'dashboard_repeat_suppressed': suppress_dashboard}


def configure(preferences, paths):
    manifest = manifest_load(paths)
    verify_owned(paths, manifest)
    files = manifest['files']
    preferences.validate()
    old = Preferences.load(paths.config)
    if preferences.movie != 'default' and not Path(preferences.movie).is_relative_to(paths.root / 'media'):
        preferences.movie = str(copy_movie(preferences.movie, paths, files))
    inspect(paths.movie(preferences))
    preferences.save(paths.config)
    files[str(paths.config)] = digest(paths.config)
    manifest_save(paths, manifest)
    # Retire only an unchanged, managed copy; never delete the user's source movie.
    if old.movie != preferences.movie and old.movie in files and Path(old.movie).is_relative_to(paths.root / 'media'):
        Path(old.movie).unlink(missing_ok=True)
        del files[old.movie]
        manifest_save(paths, manifest)
    return {'configured': str(paths.config), 'preferences': preferences.__dict__}


def uninstall(paths):
    manifest = manifest_load(paths)
    verify_owned(paths, manifest)
    if manifest.get('dashboard_url'):
        # If another movie was selected later, keep the user's new choice.
        restored = steam.restore(manifest['dashboard_before'], manifest['dashboard_url'])
    else:
        restored = False
    parents = set()
    for name in sorted(manifest['files'], reverse=True):
        path = safe_file(Path(name), paths)
        path.unlink(missing_ok=True)
        parents.add(path.parent)
    paths.manifest.unlink()
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    for directory in sorted(parents | {paths.root, paths.config_dir}, key=lambda p: len(p.parts), reverse=True):
        try:
            directory.rmdir()
        except (FileNotFoundError, OSError):
            pass  # Only empty directories are removed; shared folders are left alone.
    return {'uninstalled': True, 'previous_steam_selection_restored': restored}
