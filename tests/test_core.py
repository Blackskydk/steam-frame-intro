import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from frame_intro.install import configure, digest, install, safe_file, uninstall, verify_owned, write_new
from frame_intro.media import parse_info
from frame_intro.player import already_played
from frame_intro.settings import Paths, Preferences, color, rgba


class PreferencesTests(unittest.TestCase):
    def test_color(self):
        self.assertEqual(color('BLUE'), '#0000ff')
        self.assertEqual(rgba('#123AbC'), bytes([18, 58, 188, 255]))
        for invalid in ('#123', '#12345678', 'rgb(1,2,3)', 4):
            with self.assertRaises(ValueError):
                color(invalid)

    def test_limits_and_types(self):
        for values in ({'width': float('nan')}, {'max_duration': 100},
                       {'audio': 'false'}, {'key_black': 1}, {'movie': 'relative.mp4'}):
            with self.assertRaises(ValueError):
                Preferences(**values).validate()

    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'preferences.json'
            Preferences(background='navy', audio=False).save(path)
            self.assertEqual(Preferences.load(path).background, '#000080')
            self.assertFalse(Preferences.load(path).audio)


class MediaTests(unittest.TestCase):
    def info(self, width, height, **extra):
        return parse_info({'streams': [{'codec_type': 'video', 'width': width,
                                         'height': height, **extra}, {'codec_type': 'audio'}]})

    def test_landscape_not_stretched_to_square(self):
        info = self.info(1920, 1080)
        self.assertEqual((info.width, info.height), (450, 253))
        self.assertTrue(info.has_audio)

    def test_phone_rotation_and_pixel_aspect(self):
        info = self.info(1920, 1080, side_data_list=[{'rotation': -90}])
        self.assertEqual((info.width, info.height), (253, 450))
        info = self.info(720, 576, sample_aspect_ratio='16:15')
        self.assertAlmostEqual(info.width / info.height, 4 / 3, places=2)

    def test_bad_streams(self):
        for value in ({'streams': []}, {'streams': [{'codec_type': 'video', 'width': 0, 'height': 4}]}):
            with self.assertRaises(ValueError):
                parse_info(value)


class OwnershipTests(unittest.TestCase):
    def test_install_configure_uninstall_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(Path(directory).resolve())
            before = {'startup_movie_id': '0', 'startup_movie_local_path': '',
                      'startup_movie_shuffle': True}

            def create_placeholder(current_paths, files):
                write_new(current_paths.placeholder, b'test-placeholder', current_paths, files)

            def fake_command(*args, **kwargs):
                from types import SimpleNamespace
                return SimpleNamespace(stdout=str(paths.dropin), returncode=0)

            with patch('frame_intro.install.doctor', return_value={'ok': True, 'legacy_intro_detected': False}), \
                    patch('frame_intro.install.play'), \
                    patch('frame_intro.install.steam.snapshot', return_value=before), \
                    patch('frame_intro.install.steam.select', return_value=paths.placeholder_url), \
                    patch('frame_intro.install.placeholder', side_effect=create_placeholder), \
                    patch('frame_intro.install.subprocess.run', side_effect=fake_command):
                install(Preferences(), paths)
            self.assertTrue(paths.dropin.is_file())
            self.assertTrue(paths.launcher.is_file())
            self.assertTrue(paths.manifest.is_file())
            self.assertEqual(json.loads(paths.manifest.read_text())['dashboard_before'], before)
            with patch('frame_intro.install.inspect'):
                configure(Preferences(background='#123456', audio=False), paths)
            self.assertEqual(Preferences.load(paths.config).background, '#123456')
            with patch('frame_intro.install.steam.restore', return_value=True), \
                    patch('frame_intro.install.subprocess.run', side_effect=fake_command):
                uninstall(paths)
            self.assertFalse(paths.root.exists())
            self.assertFalse(paths.config_dir.exists())
            self.assertFalse(paths.dropin.exists())
            self.assertFalse(paths.placeholder.exists())

    def test_modified_file_prevents_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(Path(directory).resolve())
            paths.root.mkdir(parents=True)
            file = paths.root / 'run.py'
            file.write_text('original')
            manifest = {'files': {str(file): digest(file)}}
            file.write_text('user modification')
            with self.assertRaises(ValueError):
                verify_owned(paths, manifest)
            self.assertEqual(file.read_text(), 'user modification')

    def test_unmanaged_path_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(Path(directory).resolve())
            with self.assertRaises(ValueError):
                safe_file(paths.home / 'important.txt', paths)

    def test_uninstall_preserves_unrelated_files_and_uses_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = Paths(Path(directory).resolve())
            paths.root.mkdir(parents=True)
            paths.config_dir.mkdir(parents=True)
            owned = paths.root / 'run.py'
            other = paths.root / 'my-notes.txt'
            owned.write_text('owned')
            other.write_text('keep')
            before = {'startup_movie_id': '7', 'startup_movie_local_path': '/my-old.webm',
                      'startup_movie_shuffle': True}
            paths.manifest.write_text(json.dumps({'schema': 1, 'project': 'steam-frame-intro',
                                                 'files': {str(owned): digest(owned)},
                                                 'dashboard_before': before,
                                                 'dashboard_url': paths.placeholder_url}))
            with patch('frame_intro.install.steam.restore', return_value=True) as restore, \
                    patch('frame_intro.install.subprocess.run'):
                uninstall(paths)
            restore.assert_called_once_with(before, paths.placeholder_url)
            self.assertFalse(owned.exists())
            self.assertEqual(other.read_text(), 'keep')

    def test_once_per_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'played'
            self.assertFalse(already_played(path, 'boot-a'))
            path.write_text('boot-a\n')
            self.assertTrue(already_played(path, 'boot-a'))
            self.assertFalse(already_played(path, 'boot-b'))


if __name__ == '__main__':
    unittest.main()
