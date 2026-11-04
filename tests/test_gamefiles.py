"""游戏数据文件适配器测试：JSON/文本/Unity v15-21/UnityFS/GameMaker/RGSS
+ 文件夹模式端到端。全部使用合成 fixture，离线运行。"""

from __future__ import annotations

import json
import struct

import pytest

from zhres.adapters.gamefiles import detect_game_file, format_by_name
from zhres.adapters.gamejson import GameJsonAdapter
from zhres.adapters.gametext import GameTextAdapter
from zhres.adapters.gamedata_win import GameMakerAdapter, parse_dat
from zhres.adapters.rgss import (RGSSAdapter, parse_marshal, serialize_marshal,
                                 _write_long, MSymbol, MString, MArray,
                                 MObject, MFixnum)
from zhres.adapters.unity import UnityAssetsAdapter, parse_unity
from zhres.adapters.unityfs import UnityBundleAdapter, parse_bundle
from zhres.project import Project
from zhres.cli import main


def mark(entries, mapping):
    for e in entries:
        if e.source in mapping:
            e.translation = mapping[e.source]
            e.status = "human"
    return entries


# ---------------------------------------------------------------------------
# JSON 适配器
# ---------------------------------------------------------------------------

class TestGameJson:
    def make_mv_data(self):
        return {
            "events": [None, {"id": 1, "name": "EV001", "pages": [{"list": [
                {"code": 101, "indent": 0, "parameters": ["Harold", 0, 2, 2]},
                {"code": 401, "indent": 0, "parameters": ["Hello adventurer!"]},
                {"code": 355, "indent": 0, "parameters": ["const x = 1;"]},
                {"code": 108, "indent": 0, "parameters": ["comment"]},
            ]}]}],
            "bgm": {"name": "Battle1", "volume": 90, "pitch": 100, "pan": 0},
            "system": {"gameTitle": "My Cool Game"},
        }

    def test_extract_and_skip_rules(self):
        data = json.dumps(self.make_mv_data(), indent=2).encode("utf-8")
        fmt = detect_game_file(data, "data/Map001.json")
        assert fmt is not None and fmt.name == "json"
        es = fmt.extract("data/Map001.json", data)
        srcs = {e.source: e for e in es}
        assert "Hello adventurer!" in srcs
        assert "My Cool Game" in srcs
        assert srcs["Harold"].status == "untranslated"
        # 脚本/注释指令、素材名被跳过
        assert srcs["const x = 1;"].status == "skipped"
        assert srcs["comment"].status == "skipped"
        assert srcs["Battle1"].status == "skipped"

    def test_apply_roundtrip_and_style(self):
        data = json.dumps(self.make_mv_data(), indent=2).encode("utf-8")
        fmt = detect_game_file(data, "Map001.json")
        es = mark(fmt.extract("Map001.json", data),
                  {"Hello adventurer!": "你好，冒险者！"})
        new = fmt.apply(es, data)
        obj = json.loads(new.decode("utf-8"))
        assert obj["events"][1]["pages"][0]["list"][1]["parameters"] == \
            ["你好，冒险者！"]
        assert obj["events"][1]["pages"][0]["list"][2]["parameters"] == \
            ["const x = 1;"]
        assert obj["bgm"] == {"name": "Battle1", "volume": 90,
                              "pitch": 100, "pan": 0}
        assert b"\n  " in new          # 缩进风格保持
        assert not new.startswith(b"\xef")   # 无 BOM 泄漏

    def test_minified_style_preserved(self):
        data = json.dumps({"a": "Hello!", "b": 1},
                          separators=(",", ":")).encode("utf-8")
        fmt = detect_game_file(data, "x.json")
        es = mark(fmt.extract("x.json", data), {"Hello!": "你好！"})
        new = fmt.apply(es, data)
        assert b", " not in new and b": " not in new

    def test_no_change_byte_identical(self):
        data = json.dumps(self.make_mv_data(), indent=2).encode("utf-8")
        fmt = detect_game_file(data, "Map001.json")
        es = fmt.extract("Map001.json", data)
        assert fmt.apply(es, data) == data

    def test_skip_filenames(self):
        data = b'{"name": "mygame", "version": "1.0.0"}'
        assert detect_game_file(data, "package.json") is None


# ---------------------------------------------------------------------------
# 文本类适配器
# ---------------------------------------------------------------------------

class TestGameText:
    def test_txt_mode(self):
        raw = "Hello brave world!\n[start]\n;comment\nScene text\r\n".encode()
        fmt = detect_game_file(raw, "strings.txt")
        es = fmt.extract("strings.txt", raw)
        assert [e.source for e in es] == ["Hello brave world!", "Scene text"]
        mark(es, {"Hello brave world!": "你好，世界",
                  "Scene text": "场景文本"})
        new = fmt.apply(es, raw)
        assert new.decode("utf-8").splitlines()[0] == "你好，世界"
        assert b"\r\n" in new         # 行终止符保持
        assert fmt.apply(fmt.extract("strings.txt", raw), raw) == raw

    def test_txt_rejects_code_chars_in_translation(self):
        raw = b"Hello world\n"
        fmt = detect_game_file(raw, "a.txt")
        es = fmt.extract("a.txt", raw)
        es[0].translation = "bad = injection"; es[0].status = "human"
        with pytest.raises(Exception):
            fmt.apply(es, raw)

    def test_ini_mode(self):
        raw = "[Game]\ntitle = My Game\r\nicon=app.ico\r\n".encode()
        fmt = detect_game_file(raw, "config.ini")
        es = fmt.extract("config.ini", raw)
        srcs = {e.source: e for e in es}
        assert "My Game" in srcs
        assert "app.ico" in srcs and srcs["app.ico"].status == "skipped"
        mark(es, {"My Game": "我的游戏"})
        new = fmt.apply(es, raw).decode("utf-8")
        assert "title = 我的应用" not in new
        assert "title = 我的游戏" in new

    def test_csv_mode(self):
        raw = ('key,en,zh-CN\r\nMENU_START,"Start, Game",\r\n'
               "MENU_QUIT,Quit,\r\n").encode()
        fmt = detect_game_file(raw, "translations.csv")
        es = fmt.extract("translations.csv", raw)
        assert {e.source for e in es} == {"Start, Game", "Quit"}
        mark(es, {"Start, Game": "开始，游戏", "Quit": "退出"})
        new = fmt.apply(es, raw)
        assert new == (b'key,en,zh-CN\r\nMENU_START,"\xe5\xbc\x80\xe5\xa7\x8b'
                       b'\xef\xbc\x8c\xe6\xb8\xb8\xe6\x88\x8f",\r\n'
                       b"MENU_QUIT,\xe9\x80\x80\xe5\x87\xba,\r\n")
        # 回写后重新提取一致
        es2 = fmt.extract("translations.csv", new)
        assert {e.source for e in es2} == {"开始，游戏", "退出"}


# ---------------------------------------------------------------------------
# Unity SerializedFile v15-21
# ---------------------------------------------------------------------------

def build_unity_v(ver: int, script: bytes = b'{"key": "Hello"}') -> bytes:
    out = bytearray()

    def u32(v):
        out.extend(struct.pack("<I", v))

    def i32(v):
        out.extend(struct.pack("<i", v))

    def i64(v):
        out.extend(struct.pack("<q", v))

    def u8(v):
        out.append(v)

    def i16(v):
        out.extend(struct.pack("<h", v))

    def strz(s):
        out.extend(s.encode() + b"\x00")

    def align(n=4):
        while len(out) % n:
            out.append(0)

    def pad(n):
        out.extend(b"\x00" * n)

    u32(0); u32(0); u32(ver); u32(0)
    u8(0); pad(3)
    strz("2021.3.16f1" if ver >= 17 else "2017.4.1f1")
    i32(19); u8(0)
    i32(2)
    i32(49)
    if ver >= 16:
        u8(0)
    if ver >= 17:
        i16(-1)
    if (ver < 16 and 49 < 0) or (ver >= 16 and 49 == 114):
        pad(16)
    pad(16)
    i32(114)
    if ver >= 16:
        u8(0)
    if ver >= 17:
        i16(-1)
    if (ver < 16 and 114 < 0) or (ver >= 16 and 114 == 114):
        pad(16)
    pad(16)
    i32(1); align()
    i64(123)
    bs_pos = len(out); u32(0)
    sz_pos = len(out); u32(0)
    i32(0)
    if 11 <= ver < 17:
        i16(-1)
    if ver in (15, 16):
        u8(0)
    align()
    byte_start = len(out)
    name = b"loc_strings"
    i32(len(name)); out.extend(name); align()
    i32(len(script)); out.extend(script); align()
    byte_size = len(out) - byte_start
    struct.pack_into("<I", out, bs_pos, byte_start)
    struct.pack_into("<I", out, sz_pos, byte_size)
    struct.pack_into("<I", out, 4, len(out))
    return bytes(out)


class TestUnityV15To21:
    @pytest.mark.parametrize("ver", [15, 16, 17, 19, 21])
    def test_roundtrip(self, ver):
        data = build_unity_v(ver)
        uf = parse_unity(data)
        assert uf.version == ver and len(uf.text_assets()) == 1
        es = UnityAssetsAdapter().extract("t.assets", data)
        assert len(es) == 1 and es[0].source == '{"key": "Hello"}'
        mark(es, {'{"key": "Hello"}': '{"key": "你好"}'})
        new = UnityAssetsAdapter().apply(es, data)
        uf2 = parse_unity(new)
        assert uf2.text_assets()[0].script == '{"key": "你好"}'
        assert len(new) > len(data)

    def test_no_change_identical(self):
        data = build_unity_v(19)
        es = UnityAssetsAdapter().extract("t.assets", data)
        assert UnityAssetsAdapter().apply(es, data) == data

    def test_v22_still_works(self):
        # v22 旧路径不受影响（头 8 字节为 0 + 大端版本号）
        pytest.importorskip("struct")
        data = bytearray()
        data += b"\x00" * 8 + struct.pack(">i", 22)
        data += struct.pack(">B", 0) + b"\x00" * 3
        data += struct.pack(">I", 0)          # metadata size
        data += struct.pack(">q", 0)          # fileSize
        data += struct.pack(">q", 0)          # dataOffset
        data += struct.pack(">q", 0)          # unknown
        data += b"2022.3.17f1\x00"
        data += struct.pack("<i", 19) + b"\x00"
        data += struct.pack("<i", 1)          # typeCount
        data += struct.pack("<i", 49) + b"\x00" + struct.pack("<h", -1)
        data += b"\x00" * 16
        data += struct.pack("<i", 1)          # objectCount
        while len(data) % 4:
            data.append(0)
        import struct as s
        data += s.pack("<q", 123)
        bs_pos = len(data)
        data += s.pack("<q", 0) + s.pack("<I", 0) + s.pack("<i", 0)
        start = len(data)
        name = b"a"
        data += s.pack("<i", len(name)) + name
        while len(data) % 4:
            data.append(0)
        script = b"hello"
        data += s.pack("<i", len(script)) + script
        while len(data) % 4:
            data.append(0)
        size = len(data) - start
        s.pack_into("<q", data, bs_pos, start)
        s.pack_into("<I", data, bs_pos + 8, size)
        s.pack_into(">q", data, 0x18, len(data))
        uf = parse_unity(bytes(data))
        assert uf.version == 22 and uf.text_assets()[0].script == "hello"


# ---------------------------------------------------------------------------
# UnityFS
# ---------------------------------------------------------------------------

def lz4_store_only(data: bytes) -> bytes:
    """合法 LZ4 块：纯字面量序列。"""
    out = bytearray()
    i = 0
    n = len(data)
    while True:
        chunk = data[i:i + 0xFFFF]
        i += len(chunk)
        lit = len(chunk)
        last = i >= n
        out.append(min(lit, 15) << 4)
        if lit >= 15:
            rem = lit - 15
            while rem >= 255:
                out.append(255)
                rem -= 255
            out.append(rem)
        out += chunk
        if last:
            break
    return bytes(out)


def build_bundle(sf_content: bytes, *, version=6, engine=b"2021.3.16f1",
                 comp_data=0, comp_info=0):
    if comp_data == 2:
        comp = lz4_store_only(sf_content)
        blk_flags = 2
    else:
        comp = sf_content
        blk_flags = 0
    info = bytearray(b"\x11" * 16)
    info += struct.pack(">i", 1)
    info += struct.pack(">IIH", len(sf_content), len(comp), blk_flags)
    info += struct.pack(">i", 1)
    info += struct.pack(">qqI", 0, len(sf_content), 0)
    info += b"CAB-1234\x00"
    info = bytes(info)
    info_final = lz4_store_only(info) if comp_info == 2 else info
    info_flags = 2 if comp_info == 2 else 0
    out = bytearray(b"UnityFS\x00")
    out += struct.pack(">I", version)
    out += b"0.0\x00" + engine + b"\x00"
    size_pos = len(out)
    out += struct.pack(">q", 0)
    out += struct.pack(">III", len(info_final), len(info), info_flags)
    if version >= 7:
        while len(out) % 16:
            out.append(0)
    out += info_final
    out += comp
    struct.pack_into(">q", out, size_pos, len(out))
    return bytes(out)


class TestUnityFS:
    @pytest.mark.parametrize("comp_data,comp_info,version",
                             [(0, 0, 6), (2, 0, 6), (2, 2, 6), (0, 2, 7),
                              (2, 0, 7)])
    def test_roundtrip(self, comp_data, comp_info, version):
        sf = build_unity_v(19)
        bundle = build_bundle(sf, version=version, comp_data=comp_data,
                              comp_info=comp_info)
        b = parse_bundle(bundle)
        assert b.buffer == sf
        es = UnityBundleAdapter().extract("t.bundle", bundle)
        assert len(es) == 1 and es[0].source == '{"key": "Hello"}'
        mark(es, {'{"key": "Hello"}': '{"key": "你好"}'})
        new = UnityBundleAdapter().apply(es, bundle)
        es2 = UnityBundleAdapter().extract("t.bundle", new)
        assert es2[0].source == '{"key": "你好"}'

    def test_no_change_identical(self):
        bundle = build_bundle(build_unity_v(19), comp_data=2)
        es = UnityBundleAdapter().extract("t.bundle", bundle)
        assert UnityBundleAdapter().apply(es, bundle) == bundle


# ---------------------------------------------------------------------------
# GameMaker data.win
# ---------------------------------------------------------------------------

def build_dat(strings):
    n = len(strings)
    fp = bytearray()
    fp += b"GEN8" + struct.pack("<I", 16) + b"\x00" * 16
    hdr = len(fp)
    fp += b"STRG" + struct.pack("<I", 0)
    sp = len(fp)
    fp += struct.pack("<I", n) + b"\x00" * (n * 4)
    for i, s in enumerate(strings):
        raw = s.encode("utf-8")
        while len(fp) % 4:
            fp.append(0)
        struct.pack_into("<I", fp, sp + 4 + i * 4, len(fp) + 8)
        fp += struct.pack("<I", len(raw)) + raw + b"\x00"
    struct.pack_into("<I", fp, hdr + 4, len(fp) - sp)
    while len(fp) % 0x80:
        fp.append(0)
    return b"FORM" + struct.pack("<I", len(fp)) + bytes(fp)


class TestGameMaker:
    def test_extract_and_inplace_writeback(self):
        strings = ["Press Start", "Game Over",
                   "The quick brown fox jumps over the lazy dog"]
        data = build_dat(strings)
        fmt = detect_game_file(data, "data.win")
        assert fmt is not None and fmt.name == "gamemaker"
        df = parse_dat(data)
        assert [s.content for s in df.strings] == strings
        es = fmt.extract("data.win", data)
        assert all(e.max_len for e in es)
        mark(es, {"Press Start": "开始", "Game Over": "终局",
                  "The quick brown fox jumps over the lazy dog":
                      "敏捷的棕色狐狸跳过了懒狗"})
        new = fmt.apply(es, data)
        assert len(new) == len(data)            # 原位回写等长
        df2 = parse_dat(new)
        assert [s.content for s in df2.strings] == \
            ["开始", "终局", "敏捷的棕色狐狸跳过了懒狗"]
        assert df2.strings[0].length_off == df.strings[0].length_off
        assert struct.unpack_from("<I", new,
                                  df2.strings[0].length_off)[0] == 6

    def test_overlong_translation_rejected(self):
        data = build_dat(["Game Over"])
        fmt = GameMakerAdapter()
        es = fmt.extract("data.win", data)
        es[0].translation = "太长" * 10
        es[0].status = "human"
        with pytest.raises(Exception):
            fmt.apply(es, data)

    def test_no_change_identical(self):
        data = build_dat(["abc", "def"])
        es = GameMakerAdapter().extract("data.win", data)
        assert GameMakerAdapter().apply(es, data) == data


# ---------------------------------------------------------------------------
# RGSS（Ruby Marshal + RGSSAD 档案）
# ---------------------------------------------------------------------------

def L(n: int) -> bytes:
    out = bytearray()
    _write_long(out, n)
    return bytes(out)


def S(b: bytes) -> bytes:
    return L(len(b)) + b


class TestMarshal:
    @pytest.mark.parametrize("data", [
        b"\x04\x080",
        b"\x04\x08T",
        b"\x04\x08F",
        b"\x04\x08i" + L(1),
        b"\x04\x08i" + L(-1),
        b"\x04\x08i" + L(122),
        b"\x04\x08i" + L(123),
        b"\x04\x08i" + L(300),
        b"\x04\x08i" + L(-200),
        b"\x04\x08[" + L(2) + b"i" + L(1) + b"i" + L(2),
        b"\x04\x08{" + L(1) + b"i" + L(1) + b"i" + L(2),
        b"\x04\x08}" + L(1) + b"i" + L(1) + b"i" + L(2) + b"0",
        b"\x04\x08:" + S(b"aa"),
        b"\x04\x08I" + b'"' + S(b"hello") + L(1) + b":" + S(b"E") + b"T",
        b"\x04\x08f" + S(b"1.5"),
        b"\x04\x08o:" + S(b"Object") + L(1) + b":" + S(b"@a") + b"0",
        b"\x04\x08[" + L(1) + b"@" + L(0),
        b"\x04\x08u:" + S(b"Table") + S(bytes([1])),
        b"\x04\x08S:" + S(b"Pt") + L(1) + b":" + S(b"x") + b"i" + L(3),
        b"\x04\x08c" + S(b"Foo"),
    ])
    def test_byte_exact_roundtrip(self, data):
        node = parse_marshal(data)
        assert serialize_marshal(node) == data

    def test_shared_string_link(self):
        a = MString(b"x")
        arr = MArray([a, a])
        assert serialize_marshal(arr) == \
            b"\x04\x08[" + L(2) + b'"' + S(b"x") + b"@" + L(1)

    def test_trailing_data_rejected(self):
        import pytest
        from zhres.errors import ParseError
        with pytest.raises(ParseError):
            parse_marshal(b"\x04\x080garbage")


def build_v3(entries):
    """entries: [(name_bytes, content_bytes)] → RGSSAD v3 档案。"""
    seed = 0x12345678
    key = (seed * 9 + 3) & 0xFFFFFFFF
    from zhres.adapters.rgss import _encrypt_stream
    data_start = 12 + sum(16 + len(n) for n, _c in entries) + 4
    metas = []
    o = data_start
    for name, content in entries:
        ekey = (o * 31 + 7) & 0xFFFFFFFF
        metas.append((name, content, ekey, o))
        o += len(content)
    headers = bytearray()
    for name, content, ekey, real_off in metas:
        headers += struct.pack("<I", real_off ^ key)
        headers += struct.pack("<I", len(content) ^ key)
        headers += struct.pack("<I", ekey ^ key)
        headers += struct.pack("<I", len(name) ^ key)
        ne = bytearray(name)
        for i in range(len(name)):
            ne[i] ^= (key >> ((i % 4) * 8)) & 0xFF
        headers += ne
    headers += struct.pack("<I", 0 ^ key)
    out = bytearray(b"RGSSAD\x00\x03") + struct.pack("<I", seed) + headers
    for _n, content, ekey, _o in metas:
        out += _encrypt_stream(content, ekey)
    return bytes(out)


class TestRGSS:
    def make_actor(self, name: bytes):
        return MObject(MSymbol(b"RPG::Actor"),
                       [(MSymbol(b"@name"), MString(name)),
                        (MSymbol(b"@id"), MFixnum(1))])

    def test_archive_roundtrip(self):
        root = MArray([self.make_actor(b"Harold"),
                       self.make_actor(b"Gale")])
        arch = build_v3([
            (b"Data/Actors.rvdata2", serialize_marshal(root)),
            (b"Data/Classes.rvdata2", serialize_marshal(MArray([]))),
        ])
        fmt = RGSSAdapter()
        es = fmt.extract("Game.rgss3a", arch)
        assert {e.source for e in es} == {"Harold", "Gale"}
        mark(es, {"Harold": "哈罗德", "Gale": "盖尔"})
        new = fmt.apply(es, arch)
        es2 = fmt.extract("Game.rgss3a", new)
        assert {e.source for e in es2} == {"哈罗德", "盖尔"}

    def test_no_change_identical(self):
        root = MArray([self.make_actor(b"Harold")])
        arch = build_v3([(b"Data/Actors.rvdata2",
                          serialize_marshal(root))])
        es = RGSSAdapter().extract("Game.rgss3a", arch)
        assert RGSSAdapter().apply(es, arch) == arch

    def test_loose_marshal(self):
        data = serialize_marshal(self.make_actor(b"Harold"))
        fmt = detect_game_file(data, "Data/Actors.rvdata2")
        assert fmt is not None and fmt.name == "rgss"
        es = fmt.extract("Actors.rvdata2", data)
        assert es[0].source == "Harold"
        mark(es, {"Harold": "哈罗德"})
        new = fmt.apply(es, data)
        assert parse_marshal(new) is not None


# ---------------------------------------------------------------------------
# CLI 端到端：单文件 + 文件夹模式
# ---------------------------------------------------------------------------

class TestCliEndToEnd:
    def test_single_file_cycle(self, tmp_path):
        f = tmp_path / "Actors.json"
        f.write_text(json.dumps({"actors": [{"name": "Harold"}]},
                                indent=2), encoding="utf-8")
        assert main(["extract", str(f)]) == 0
        p = Project.load(tmp_path / "Actors.json.zhres-project.json")
        assert p.target["kind"] == "json"
        p.entries[0].translation = "哈罗德"
        p.entries[0].status = "human"
        p.save(tmp_path / "Actors.json.zhres-project.json")
        assert main(["writeback",
                     str(tmp_path / "Actors.json.zhres-project.json")]) == 0
        assert main(["verify",
                     str(tmp_path / "Actors.json.zhres-project.json")]) == 0
        assert main(["restore", str(f), "--yes"]) == 0
        obj = json.loads(f.read_text(encoding="utf-8"))
        assert obj["actors"][0]["name"] == "Harold"   # 已还原

    def test_folder_cycle(self, tmp_path):
        game = tmp_path / "MyGame"
        (game / "data").mkdir(parents=True)
        mv = {"events": [None, {"pages": [{"list": [
            {"code": 401, "parameters": ["Hello adventurer!"]}]}]}],
            "system": {"gameTitle": "My Cool Game"}}
        (game / "data" / "Map001.json").write_text(
            json.dumps(mv, indent=2), encoding="utf-8")
        (game / "strings.ini").write_text(
            "[UI]\nmenu_start = Start Game\n", encoding="utf-8")

        assert main(["extract", str(game)]) == 0
        p = Project.load(game.parent / "MyGame.zhres-project.json")
        assert p.target["kind"] == "folder"
        assert {f["rel"] for f in p.target["files"]} == \
            {"data/Map001.json", "strings.ini"}
        trans = {"Hello adventurer!": "你好，冒险者！",
                 "My Cool Game": "我的酷游戏",
                 "Start Game": "开始游戏"}
        for e in p.entries:
            if e.source in trans:
                e.translation = trans[e.source]
                e.status = "human"
        p.save(game.parent / "MyGame.zhres-project.json")
        assert main(
            ["writeback", str(game.parent / "MyGame.zhres-project.json")]) == 0
        assert main(
            ["verify", str(game.parent / "MyGame.zhres-project.json")]) == 0
        # 回写后再次提取：不把 zhres 产物当数据文件
        assert main(["extract", str(game)]) == 0
        p2 = Project.load(game.parent / "MyGame.zhres-project.json")
        assert len(p2.target["files"]) == 2
        # 还原
        assert main(["restore", str(game), "--yes"]) == 0
        mv2 = json.loads((game / "data" / "Map001.json").read_text(
            encoding="utf-8"))
        assert mv2["events"][1]["pages"][0]["list"][0]["parameters"] == \
            ["Hello adventurer!"]

    def test_folder_no_data_detected(self, tmp_path):
        empty = tmp_path / "Empty"
        empty.mkdir()
        (empty / "readme.md").write_text("nothing here", encoding="utf-8")
        assert main(["extract", str(empty)]) == 2


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def test_format_by_name():
    for name in ("qm", "unity", "unityfs", "gamemaker", "rgss", "json",
                 "text"):
        assert format_by_name(name) is not None
    assert format_by_name("nope") is None


def test_detect_rejects_binary():
    assert detect_game_file(b"\x00\x01\x02\x03" * 64, "blob.bin") is None
    assert detect_game_file(b"MZ\x90\x00" + b"\x00" * 128, "game.exe") is None
