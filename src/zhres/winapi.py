"""winapi —— kernel32 资源 API 的 ctypes 封装。

所有 Windows API 调用集中在此模块，处理附录 C 列出的全部坑点：

1. ``MAKEINTRESOURCE``：资源名为序数时必须把整数 cast 成 ``LPCWSTR``；
   Enum* 回调**收到**的 type/name 用 ``c_void_p`` 接收，先判序数
   （指针值高 16 位为 0）再转字符串，避免 ctypes 把小整数当指针解引用。
2. 回调签名按 x64 声明，回调对象在调用期间由局部变量持引用防 GC。
3. ``UpdateResourceW`` 的 ``lpData`` 用 ``from_buffer_copy`` 拷贝。
4. 语言 ID 全程使用枚举得到的原值（含 0=中性语言），不改写。
5. ``LoadLibraryExW`` 用完必须 ``FreeLibrary``；``LockResource`` 返回的
   指针仅在模块存活期有效——立刻拷贝成 bytes 再用。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from .errors import PeError

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LOAD_LIBRARY_AS_DATAFILE = 0x00000002
LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020

RT_MENU = 4
RT_DIALOG = 5
RT_STRING = 6
RT_RCDATA = 10
RT_VERSION = 16

# ---------------------------------------------------------------------------
# 函数原型声明
# ---------------------------------------------------------------------------

kernel32.LoadLibraryExW.restype = wintypes.HMODULE
kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]

kernel32.FreeLibrary.restype = wintypes.BOOL
kernel32.FreeLibrary.argtypes = [wintypes.HMODULE]

kernel32.FindResourceExW.restype = wintypes.HRSRC
kernel32.FindResourceExW.argtypes = [
    wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.WORD,
]

kernel32.LoadResource.restype = wintypes.HGLOBAL
kernel32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]

kernel32.LockResource.restype = wintypes.LPVOID
kernel32.LockResource.argtypes = [wintypes.HGLOBAL]

kernel32.SizeofResource.restype = wintypes.DWORD
kernel32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HRSRC]

kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]

kernel32.UpdateResourceW.restype = wintypes.BOOL
kernel32.UpdateResourceW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.WORD,
    wintypes.LPVOID, wintypes.DWORD,
]

kernel32.EndUpdateResourceW.restype = wintypes.BOOL
kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]

# Enum* 回调：type/name 一律用 c_void_p 接收（可能是 MAKEINTRESOURCE 序数），
# 回调签名参照 x64: BOOL CALLBACK EnumResXxxProc(HMODULE, LPWSTR, [LPWSTR,] WORD, LONG_PTR)
ENUMRESTYPEPROCW = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMODULE,       # hModule
    ctypes.c_void_p,        # lpType（可能为序数）
    ctypes.c_ssize_t,       # lParam (LONG_PTR)
)

ENUMRESNAMEPROCW = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMODULE,       # hModule
    ctypes.c_void_p,        # lpType（可能为序数）
    ctypes.c_void_p,        # lpName（可能为序数）
    wintypes.WORD,          # wIDLanguage
    ctypes.c_ssize_t,       # lParam (LONG_PTR)
)

ENUMRESLANGPROCW = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMODULE,       # hModule
    ctypes.c_void_p,        # lpType（可能为序数）
    ctypes.c_void_p,        # lpName（可能为序数）
    wintypes.WORD,          # wIDLanguage
    ctypes.c_ssize_t,       # lParam (LONG_PTR)
)

kernel32.EnumResourceTypesW.restype = wintypes.BOOL
kernel32.EnumResourceTypesW.argtypes = [wintypes.HMODULE, ENUMRESTYPEPROCW, ctypes.c_ssize_t]

kernel32.EnumResourceNamesW.restype = wintypes.BOOL
kernel32.EnumResourceNamesW.argtypes = [
    wintypes.HMODULE, wintypes.LPCWSTR, ENUMRESNAMEPROCW, ctypes.c_ssize_t,
]

kernel32.EnumResourceLanguagesW.restype = wintypes.BOOL
kernel32.EnumResourceLanguagesW.argtypes = [
    wintypes.HMODULE, wintypes.LPCWSTR, wintypes.LPCWSTR, ENUMRESLANGPROCW, ctypes.c_ssize_t,
]


# ---------------------------------------------------------------------------
# MAKEINTRESOURCE 辅助
# ---------------------------------------------------------------------------

def make_intresource(value: int | str):
    """把资源名（整数序数或字符串）转换成 API 可接受的 LPCWSTR 参数。

    序数必须把整数 cast 成 LPCWSTR 传入；字符串直接传 str。
    """
    if isinstance(value, int):
        return ctypes.cast(ctypes.c_void_p(value), wintypes.LPCWSTR)
    return value


def decode_intresource(ptr: int | None) -> int | str:
    """把回调收到的 LPVOID（LPCWSTR 语义）还原为序数 int 或字符串。

    IS_INTRESOURCE 规则：指针值高 16 位为 0 即视为序数（低 16 位为 ID）。
    """
    if ptr is None:
        raise PeError("Enum 回调收到 NULL 资源名指针")
    if (ptr >> 16) == 0:
        return ptr & 0xFFFF
    return ctypes.wstring_at(ptr)


def _win_error(context: str, path: str | None = None) -> PeError:
    code = ctypes.get_last_error()
    buf = ctypes.create_unicode_buffer(512)
    kernel32.FormatMessageW(
        0x00001000 | 0x00000200,  # FORMAT_MESSAGE_FROM_SYSTEM | IGNORE_INSERTS
        None, code, 0, buf, len(buf), None,
    )
    msg = buf.value.strip() or f"Win32 错误码 {code}"
    target = f"（文件：{path}）" if path else ""
    return PeError(f"{context}失败{target}：{msg} (Win32 {code})")


# ---------------------------------------------------------------------------
# 只读加载与枚举
# ---------------------------------------------------------------------------

class PeResourceLibrary:
    """以 LOAD_LIBRARY_AS_DATAFILE|LOAD_LIBRARY_AS_IMAGE_RESOURCE 打开的 PE。

    只读，不执行任何代码。用完必须 close()（或用 with）。
    """

    def __init__(self, path: str | Path) -> None:
        # 绝对路径：LoadLibraryExW 对不含分隔符的名字按 DLL 搜索顺序
        # 解析（应用目录/系统目录），不会命中当前目录
        self.path = str(Path(path).resolve())
        self._handle = kernel32.LoadLibraryExW(
            self.path, None,
            LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE,
        )
        if not self._handle:
            raise _win_error(f"无法以数据文件方式加载 {self.path}", self.path)

    # -- 枚举 ---------------------------------------------------------------

    def enum_types(self) -> list[int | str]:
        """枚举全部资源类型（序数 int 或字符串名）。"""
        result: list[int | str] = []

        def on_type(_hmod, type_ptr, _param):
            result.append(decode_intresource(type_ptr))
            return True

        cb = ENUMRESTYPEPROCW(on_type)  # 局部变量持引用，调用期间不被 GC
        if not kernel32.EnumResourceTypesW(self._handle, cb, 0):
            raise _win_error(f"枚举资源类型失败", self.path)
        return result

    def enum_names(self, type_id: int | str) -> list[int | str]:
        """枚举某类型下全部资源名（序数 int 或字符串名）。"""
        result: list[int | str] = []

        def on_name(_hmod, _type_ptr, name_ptr, _lang, _param):
            result.append(decode_intresource(name_ptr))
            return True

        cb = ENUMRESNAMEPROCW(on_name)
        if not kernel32.EnumResourceNamesW(
                self._handle, make_intresource(type_id), cb, 0):
            raise _win_error(f"枚举资源名失败（type={type_id!r}）", self.path)
        return result

    def enum_languages(self, type_id: int | str, name: int | str) -> list[int]:
        """枚举某 (type, name) 下全部语言 ID（保持原值，含 0）。"""
        result: list[int] = []

        def on_lang(_hmod, _type_ptr, _name_ptr, lang, _param):
            result.append(lang)
            return True

        cb = ENUMRESLANGPROCW(on_lang)
        if not kernel32.EnumResourceLanguagesW(
                self._handle, make_intresource(type_id),
                make_intresource(name), cb, 0):
            raise _win_error(
                f"枚举资源语言失败（type={type_id!r}, name={name!r}）", self.path)
        return result

    # -- 读取 ---------------------------------------------------------------

    def _locate(self, type_id: int | str, name: int | str, lang: int):
        res = kernel32.FindResourceExW(
            self._handle, make_intresource(type_id),
            make_intresource(name), lang,
        )
        if not res:
            raise _win_error(
                f"查找资源失败（type={type_id!r}, name={name!r}, lang={lang}）",
                self.path)
        hglob = kernel32.LoadResource(self._handle, res)
        if not hglob:
            raise _win_error(
                f"加载资源失败（type={type_id!r}, name={name!r}, lang={lang}）",
                self.path)
        ptr = kernel32.LockResource(hglob)
        size = kernel32.SizeofResource(self._handle, res)
        return ptr, size

    def read(self, type_id: int | str, name: int | str, lang: int) -> bytes:
        """读取指定三元组的资源原始字节。"""
        ptr, size = self._locate(type_id, name, lang)
        if not ptr or size == 0:
            return b""
        # LockResource 指针仅在模块存活期有效，立即拷贝
        return ctypes.string_at(ptr, size)

    def read_head(self, type_id: int | str, name: int | str, lang: int,
                  nbytes: int) -> bytes:
        """只读资源开头 ``nbytes`` 字节（用于 TPF0 签名探测，
        避免把超大 RCDATA 整体拷入内存——映射本身是文件映射）。"""
        ptr, size = self._locate(type_id, name, lang)
        if not ptr or size == 0:
            return b""
        return ctypes.string_at(ptr, min(nbytes, size))

    # -- 生命周期 -----------------------------------------------------------

    def close(self) -> None:
        if getattr(self, "_handle", None):
            kernel32.FreeLibrary(self._handle)
            self._handle = None

    def __enter__(self) -> "PeResourceLibrary":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# 回写（BeginUpdateResource / UpdateResource / EndUpdateResource）
# ---------------------------------------------------------------------------

class ResourceUpdater:
    """逐三元组更新资源的句柄封装。

    任一步失败后必须调用 discard() 丢弃全部修改（EndUpdateResourceW(TRUE)），
    保证目标文件保持原样。
    """

    def __init__(self, path: str | Path) -> None:
        # 同 PeResourceLibrary：BeginUpdateResourceW 的相对名按搜索顺序解析
        self.path = str(Path(path).resolve())
        self._handle = kernel32.BeginUpdateResourceW(self.path, False)
        if not self._handle:
            raise _win_error(f"打开资源更新句柄失败", self.path)
        self._finished = False

    def update(self, type_id: int | str, name: int | str, lang: int,
               data: bytes) -> None:
        """更新一个 (type, name, lang) 三元组的资源体。"""
        if self._finished:
            raise PeError("资源更新句柄已关闭")
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data) if data else None
        if not kernel32.UpdateResourceW(
                self._handle, make_intresource(type_id),
                make_intresource(name), lang, buf, len(data)):
            raise _win_error(
                f"更新资源失败（type={type_id!r}, name={name!r}, lang={lang}）",
                self.path)

    def commit(self) -> None:
        """提交全部修改。失败时资源保持原样。"""
        if self._finished:
            raise PeError("资源更新句柄已关闭")
        self._finished = True
        if not kernel32.EndUpdateResourceW(self._handle, False):
            raise _win_error("提交资源更新失败", self.path)

    def discard(self) -> None:
        """丢弃全部修改（回滚），目标文件保持原样。"""
        if self._finished:
            return
        self._finished = True
        kernel32.EndUpdateResourceW(self._handle, True)

    def __enter__(self) -> "ResourceUpdater":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None:
            self.discard()
