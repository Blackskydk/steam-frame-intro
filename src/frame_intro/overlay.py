"""OpenVR overlay bindings derived from the runtime's installed C header."""
import ctypes as C
import os
from pathlib import Path
import re
import time

from .settings import runtime_files


def log(**values):
    import json
    print(json.dumps(values), flush=True)

class Matrix34(C.Structure):
    _fields_ = [('m', (C.c_float * 4) * 3)]


class Texture(C.Structure):
    _fields_ = [('handle', C.c_void_p), ('kind', C.c_int), ('color_space', C.c_int)]


class TextureBounds(C.Structure):
    _fields_ = [('u_min', C.c_float), ('v_min', C.c_float),
                ('u_max', C.c_float), ('v_max', C.c_float)]


def transform(distance):
    matrix = Matrix34()
    for axis in range(3):
        matrix.m[axis][axis] = 1
    matrix.m[2][3] = -distance
    return matrix


class Overlay:
    def __init__(self, wait_seconds=0, runtime=None):
        header_path, library_path = runtime_files(runtime)
        header = header_path.read_text()
        table = re.search(r'struct VR_IVROverlay_FnTable\s*\{(.*?)\n\};', header, re.S)
        version = re.search(r'IVROverlay_Version\s*=\s*"([^"]+)"', header)
        app = re.search(r'\b\w*VRApplication_Overlay\s*=\s*(\d+)', header)
        if not (table and version and app):
            raise RuntimeError('Installed OpenVR header does not match expected C API')
        self.names = re.findall(r'OPENVR_FNTABLE_CALLTYPE\s*\*\s*(\w+)\s*\)', table.group(1))
        self.header = header
        self.library = C.CDLL(str(library_path))
        self.library.VR_InitInternal.argtypes = [C.POINTER(C.c_int), C.c_int]
        self.library.VR_InitInternal.restype = C.c_uint32
        self.library.VR_ShutdownInternal.argtypes = []
        self.library.VR_ShutdownInternal.restype = None
        self.library.VR_GetGenericInterface.argtypes = [C.c_char_p, C.POINTER(C.c_int)]
        self.library.VR_GetGenericInterface.restype = C.c_void_p
        self.library.VR_GetVRInitErrorAsEnglishDescription.argtypes = [C.c_int]
        self.library.VR_GetVRInitErrorAsEnglishDescription.restype = C.c_char_p
        self.initialized = False
        self.handles = []
        deadline = time.monotonic() + wait_seconds
        while True:
            error = C.c_int()
            self.library.VR_InitInternal(C.byref(error), int(app.group(1)))
            if not error.value:
                self.initialized = True
                break
            if time.monotonic() >= deadline:
                detail = self.library.VR_GetVRInitErrorAsEnglishDescription(error.value)
                raise RuntimeError(f'OpenVR init {error.value}: {detail.decode() if detail else "unknown"}')
            time.sleep(.25)
        try:
            address = self.library.VR_GetGenericInterface(
                ('FnTable:' + version.group(1)).encode(), C.byref(error))
            if error.value or not address:
                raise RuntimeError(f'OpenVR interface failed: {error.value}')
            self.table = C.cast(address, C.POINTER(C.c_void_p))
            self.version = version.group(1)
            self.functions = {}
            signatures = {
                'CreateOverlay': (C.c_int, C.c_char_p, C.c_char_p, C.POINTER(C.c_uint64)),
                'DestroyOverlay': (C.c_int, C.c_uint64),
                'ShowOverlay': (C.c_int, C.c_uint64),
                'HideOverlay': (C.c_int, C.c_uint64),
                'IsOverlayVisible': (C.c_bool, C.c_uint64),
                'SetOverlayWidthInMeters': (C.c_int, C.c_uint64, C.c_float),
                'SetOverlaySortOrder': (C.c_int, C.c_uint64, C.c_uint32),
                'SetOverlayFlag': (C.c_int, C.c_uint64, C.c_int, C.c_bool),
                'SetOverlayTransformTrackedDeviceRelative':
                    (C.c_int, C.c_uint64, C.c_uint32, C.POINTER(Matrix34)),
                'SetOverlayRaw': (C.c_int, C.c_uint64, C.c_void_p,
                                  C.c_uint32, C.c_uint32, C.c_uint32),
                'SetOverlayTexture': (C.c_int, C.c_uint64, C.POINTER(Texture)),
                'SetOverlayTextureBounds': (C.c_int, C.c_uint64, C.POINTER(TextureBounds)),
                'WaitFrameSync': (C.c_int, C.c_uint32),
                'GetOverlayErrorNameFromEnum': (C.c_char_p, C.c_int),
            }
            for name, signature in signatures.items():
                index = self.names.index(name)
                self.functions[name] = C.CFUNCTYPE(*signature)(self.table[index])
        except BaseException:
            self.close()
            raise

    def call(self, name, *args):
        result = self.functions[name](*args)
        if name not in ('IsOverlayVisible', 'GetOverlayErrorNameFromEnum') and result:
            detail = self.functions['GetOverlayErrorNameFromEnum'](result)
            raise RuntimeError(f'{name}: {result} {detail.decode() if detail else ""}')
        return result

    def create(self, name, width, distance, order):
        handle = C.c_uint64()
        key = f'steam.frame.intro.{os.getpid()}.{name}'.encode()
        self.call('CreateOverlay', key, f'Steam Frame intro {name}'.encode(), C.byref(handle))
        self.handles.append(handle.value)
        self.call('SetOverlayWidthInMeters', handle.value, width)
        self.call('SetOverlaySortOrder', handle.value, order)
        self.call('SetOverlayTransformTrackedDeviceRelative', handle.value, 0,
                  C.byref(transform(distance)))
        for flag in ('VROverlayFlags_VisibleInDashboard', 'VROverlayFlags_SortWithNonSceneOverlays'):
            match = re.search(r'\b' + flag + r'\s*=\s*(\d+)', self.header)
            if match:
                self.call('SetOverlayFlag', handle.value, int(match.group(1)), True)
        return handle.value

    def raw(self, handle, data, width, height):
        # Keep the Python allocation alive through the synchronous native call.
        buffer = C.create_string_buffer(data, len(data))
        self.call('SetOverlayRaw', handle, buffer, width, height, 4)

    def texture(self, handle, texture):
        kind = re.search(r'\b\w*TextureType_OpenGL\s*=\s*(\d+)', self.header)
        gamma = re.search(r'\b\w*ColorSpace_Gamma\s*=\s*(\d+)', self.header)
        if not (kind and gamma):
            raise RuntimeError('OpenGL texture enum missing from installed C API')
        native = Texture(texture, int(kind.group(1)), int(gamma.group(1)))
        self.call('SetOverlayTexture', handle, C.byref(native))

    def close(self):
        if not self.initialized:
            return
        for handle in reversed(self.handles):
            try:
                self.call('HideOverlay', handle)
                self.call('DestroyOverlay', handle)
            except Exception as error:
                log(cleanup_warning=str(error))
        self.handles.clear()
        self.library.VR_ShutdownInternal()
        self.initialized = False
