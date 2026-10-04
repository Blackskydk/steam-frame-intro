"""Persistent OpenGL textures in a small, headless EGL context."""
import ctypes as C


class GLTextures:
    def __init__(self):
        self.display = self.context = self.surface = None
        self.textures = []
        self.egl = C.CDLL('libEGL.so.1')
        self._egl('eglGetPlatformDisplay', C.c_void_p, C.c_uint, C.c_void_p, C.c_void_p)
        self._egl('eglInitialize', C.c_uint, C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int))
        self._egl('eglBindAPI', C.c_uint, C.c_uint)
        self._egl('eglChooseConfig', C.c_uint, C.c_void_p, C.POINTER(C.c_int),
                  C.POINTER(C.c_void_p), C.c_int, C.POINTER(C.c_int))
        self._egl('eglCreateContext', C.c_void_p, C.c_void_p, C.c_void_p,
                  C.c_void_p, C.POINTER(C.c_int))
        self._egl('eglCreatePbufferSurface', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int))
        self._egl('eglMakeCurrent', C.c_uint, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
        self._egl('eglDestroySurface', C.c_uint, C.c_void_p, C.c_void_p)
        self._egl('eglDestroyContext', C.c_uint, C.c_void_p, C.c_void_p)
        self._egl('eglTerminate', C.c_uint, C.c_void_p)
        self._egl('eglGetError', C.c_uint)
        self._egl('eglGetProcAddress', C.c_void_p, C.c_char_p)
        try:
            self.display = self.egl.eglGetPlatformDisplay(0x31DD, None, None)
            major, minor = C.c_int(), C.c_int()
            self._check(self.egl.eglInitialize(self.display, C.byref(major), C.byref(minor)), 'eglInitialize')
            self._check(self.egl.eglBindAPI(0x30A2), 'eglBindAPI(OpenGL)')
            attrs = (C.c_int * 15)(0x3033, 1, 0x3040, 8, 0x3024, 8,
                                  0x3023, 8, 0x3022, 8, 0x3021, 8, 0x3025, 0, 0x3038)
            config, count = C.c_void_p(), C.c_int()
            self._check(self.egl.eglChooseConfig(self.display, attrs, C.byref(config), 1, C.byref(count)),
                        'eglChooseConfig')
            if not count.value:
                raise RuntimeError('No EGL OpenGL pbuffer configuration')
            end = (C.c_int * 1)(0x3038)
            self.context = self.egl.eglCreateContext(self.display, config, None, end)
            self._check(self.context, 'eglCreateContext')
            pbuffer = (C.c_int * 5)(0x3057, 1, 0x3056, 1, 0x3038)
            self.surface = self.egl.eglCreatePbufferSurface(self.display, config, pbuffer)
            self._check(self.surface, 'eglCreatePbufferSurface')
            self._check(self.egl.eglMakeCurrent(self.display, self.surface, self.surface, self.context),
                        'eglMakeCurrent')
            self._gl('glGetString', C.c_char_p, C.c_uint)
            self._gl('glGetError', C.c_uint)
            self._gl('glGenTextures', None, C.c_int, C.POINTER(C.c_uint))
            self._gl('glDeleteTextures', None, C.c_int, C.POINTER(C.c_uint))
            self._gl('glBindTexture', None, C.c_uint, C.c_uint)
            self._gl('glTexParameteri', None, C.c_uint, C.c_uint, C.c_int)
            self._gl('glPixelStorei', None, C.c_uint, C.c_int)
            self._gl('glTexImage2D', None, C.c_uint, C.c_int, C.c_int, C.c_int,
                     C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p)
            self._gl('glTexSubImage2D', None, C.c_uint, C.c_int, C.c_int, C.c_int,
                     C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p)
            self._gl('glFinish', None)
            self.renderer = self.glGetString(0x1F01).decode()
            self.version = self.glGetString(0x1F02).decode()
            if any(word in self.renderer.lower() for word in ('llvmpipe', 'softpipe', 'swrast')):
                raise RuntimeError('Refusing software rendering for headset playback')
            self.glPixelStorei(0x0CF5, 1)
        except BaseException:
            self.close()
            raise

    def _egl(self, name, result, *args):
        function = getattr(self.egl, name)
        function.restype, function.argtypes = result, list(args)

    def _gl(self, name, result, *args):
        address = self.egl.eglGetProcAddress(name.encode())
        if not address:
            raise RuntimeError(f'Missing OpenGL function {name}')
        setattr(self, name, C.CFUNCTYPE(result, *args)(address))

    def _check(self, success, name):
        if not success:
            raise RuntimeError(f'{name} failed: EGL 0x{self.egl.eglGetError():x}')

    def _error(self):
        error = self.glGetError()
        if error:
            raise RuntimeError(f'OpenGL error 0x{error:x}')

    def create(self, width, height, data):
        handle = C.c_uint()
        self.glGenTextures(1, C.byref(handle))
        self.textures.append(handle.value)
        self.glBindTexture(0x0DE1, handle.value)
        for parameter, value in ((0x2801, 0x2601), (0x2800, 0x2601),
                                 (0x2802, 0x812F), (0x2803, 0x812F)):
            self.glTexParameteri(0x0DE1, parameter, value)
        buffer = C.create_string_buffer(data, len(data))
        self.glTexImage2D(0x0DE1, 0, 0x8058, width, height, 0, 0x1908, 0x1401, buffer)
        self.glFinish()
        self._error()
        return handle.value

    def update(self, handle, width, height, data):
        self.glBindTexture(0x0DE1, handle)
        buffer = C.create_string_buffer(data, len(data))
        self.glTexSubImage2D(0x0DE1, 0, 0, 0, width, height, 0x1908, 0x1401, buffer)
        self.glFinish()
        self._error()

    def close(self):
        if self.context and hasattr(self, 'glDeleteTextures'):
            for value in self.textures:
                handle = C.c_uint(value)
                self.glDeleteTextures(1, C.byref(handle))
            self.textures.clear()
        if self.display:
            self.egl.eglMakeCurrent(self.display, None, None, None)
            if self.surface:
                self.egl.eglDestroySurface(self.display, self.surface)
            if self.context:
                self.egl.eglDestroyContext(self.display, self.context)
            self.egl.eglTerminate(self.display)
        self.display = self.context = self.surface = None
