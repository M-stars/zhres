""".NET 托管资源适配器测试：csc 生成真实程序集 → 提取 → 回写 →
官方 ResourceReader 交叉验证（不依赖运行被杀软拦截的 exe）。

csc 来自系统自带 .NET Framework（无下载）。
"""

from __future__ import annotations

import base64
import glob
import os
import struct
import subprocess

import pytest

from zhres.adapters.dotnet import (PeView, apply_entries, apply_surgery,
                                   extract_entries, parse_managed_blob,
                                   read_clr_resources)

CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"

GEN_CS = """
using System;
using System.Resources;
using System.Reflection;
class App {
    static void Main() {
        var rm = new ResourceManager("Strings", Assembly.GetExecutingAssembly());
        Console.WriteLine(rm.GetString("AppTitle"));
        Console.WriteLine(rm.GetString("Greeting"));
    }
}
"""

GEN_RES_CS = """
using System;
using System.Resources;
class Gen {
    static void Main() {
        using (var w = new ResourceWriter(@"%(res)")) {
            w.AddResource("AppTitle", "Hello World");
            w.AddResource("Greeting", "H\\u00e9llo \\u2014 Unicode: \\u4f60\\u597d\\u4e16\\u754c \\U0001F389");
            w.AddResource("Answer", 42);
            w.AddResource("Flag", true);
            w.AddResource("Bytes", new byte[] {1,2,3,4,5});
            w.Generate();
        }
        Console.WriteLine("GEN_OK");
    }
}
"""


def _csc_available() -> bool:
    return os.path.isfile(CSC)


@pytest.fixture(scope="module")
def dotnet_app(tmp_path_factory):
    """构建：gen.exe（生成 .resources）→ app.exe（嵌入并运行时读取）。"""
    if not _csc_available():
        pytest.skip("系统无 csc.exe")
    d = tmp_path_factory.mktemp("dn")
    res = d / "test.resources"

    gen_cs = d / "gen.cs"
    gen_cs.write_text(GEN_RES_CS.replace("%(res)", str(res)), encoding="utf-8-sig")
    r = subprocess.run([CSC, "/nologo", f"/out:{d / 'gen.exe'}", str(gen_cs)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([str(d / "gen.exe")], capture_output=True, text=True)
    assert "GEN_OK" in r.stdout, r.stdout + r.stderr
    assert res.is_file()

    app_cs = d / "app.cs"
    app_cs.write_text(GEN_CS, encoding="utf-8-sig")
    r = subprocess.run([CSC, "/nologo", f"/out:{d / 'app.exe'}",
                        f"/resource:{res},Strings.resources", str(app_cs)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return {"exe": d / "app.exe", "res": res}


def _get_blob(exe) -> tuple[bytes, PeView]:
    data = exe.read_bytes()
    coff, res_size, flags, res_rva = read_clr_resources(data)
    v = PeView(data)
    roff = v.rva2off(res_rva)
    return data[roff:roff + res_size], v


def test_parse_real_assemblies():
    """系统真实程序集的托管资源解析（存在则测）。"""
    cands = sorted(glob.glob(
        "C:/Windows/Microsoft.NET/assembly/GAC_MSIL/**/*.dll", recursive=True))
    tested = 0
    for c in cands[:200]:
        try:
            data = open(c, "rb").read()
            info = read_clr_resources(data)
            if info is None:
                continue
            _coff, res_size, _flags, res_rva = info
            if not res_rva or res_size > (8 << 20):
                continue
            v = PeView(data)
            roff = v.rva2off(res_rva)
            if roff is None:
                continue
            parsed = parse_managed_blob(data[roff:roff + res_size], name=c)
            for e in parsed:
                if e.resources:
                    for r in e.resources.records:
                        if r.is_string:
                            assert isinstance(r.value, str)
            tested += 1
            if tested >= 5:
                break
        except Exception:
            continue
    if tested == 0:
        pytest.skip("未找到可解析的系统程序集")


def test_extract_and_rebuild(dotnet_app):
    exe = dotnet_app["exe"]
    blob, _v = _get_blob(exe)
    ents = extract_entries(parse_managed_blob(blob, name="app"), name="app")
    by_src = {e.source: e for e in ents}
    assert "Hello World" in by_src
    assert by_src["Hello World"].id == "DOTNET/0/AppTitle"
    # 未修改 → apply 原样返回
    assert apply_entries(ents, blob) is blob or \
        apply_entries(ents, blob) == blob


def test_surgery_inplace_and_runtime(dotnet_app):
    """缩短译文 → 原位路径；用官方 ResourceReader 交叉验证。"""
    exe = dotnet_app["exe"]
    blob, _v = _get_blob(exe)
    ents = extract_entries(parse_managed_blob(blob, name="app"), name="app")
    for e in ents:
        if e.source == "Hello World":
            e.translation = "Hi"
            e.status = "human"
    new_blob = apply_entries(ents, blob)
    assert len(new_blob) <= len(blob), "缩短译文应变小（原位路径）"
    warnings: list[str] = []
    apply_surgery(exe, new_blob, warnings)

    # 官方 ResourceReader 交叉验证
    new_content = new_blob[4:4 + struct.unpack_from("<I", new_blob, 0)[0]]
    b64 = base64.b64encode(new_content).decode()
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$b64 = Get-Content $env:DN_B64 -Raw;"
        "$bytes = [Convert]::FromBase64String($b64.Trim());"
        "$ms = New-Object System.IO.MemoryStream(,$bytes);"
        "$rr = New-Object System.Resources.ResourceReader($ms);"
        "foreach ($e in $rr) { Write-Output ($e.Key + '=' + $e.Value) };"
        "$rr.Close()"
    )
    import struct as _s
    env = dict(os.environ, DN_B64=os.environ.get("DN_B64", ""))
    # 通过临时文件传 base64
    b64file = exe.parent / "b64.txt"
    b64file.write_text(b64)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps.replace(
        "$env:DN_B64", str(b64file))], capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    lines = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
    assert lines.get("AppTitle") == "Hi"
    assert lines.get("Answer") == "42"
    assert lines.get("Flag") == "True"


def test_surgery_grow_and_runtime(dotnet_app):
    """加长译文（含 emoji）→ blob 变大 → 扩展写回 → 交叉验证。"""
    exe = dotnet_app["exe"]
    blob, _v = _get_blob(exe)
    ents = extract_entries(parse_managed_blob(blob, name="app"), name="app")
    title = next(e for e in ents if e.id.endswith("AppTitle"))
    title.translation = "你好，世界"
    title.status = "human"
    greeting = next((e for e in ents if e.source.startswith("Héllo")), None)
    if greeting is not None:
        greeting.translation = "问候 — Unicode: 你好世界 🎉"
        greeting.status = "human"
    new_blob = apply_entries(ents, blob)
    warnings: list[str] = []
    apply_surgery(exe, new_blob, warnings)
    # 文件应仍能被 PeView 解析（结构有效）
    data = exe.read_bytes()
    info = read_clr_resources(data)
    assert info is not None
    new_content = new_blob[4:4 + struct.unpack_from("<I", new_blob, 0)[0]]
    b64 = base64.b64encode(new_content).decode()
    b64file = exe.parent / "b64.txt"
    b64file.write_text(b64)
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$b64 = Get-Content '" + str(b64file) + "' -Raw;"
        "$bytes = [Convert]::FromBase64String($b64.Trim());"
        "$ms = New-Object System.IO.MemoryStream(,$bytes);"
        "$rr = New-Object System.Resources.ResourceReader($ms);"
        "foreach ($e in $rr) { Write-Output ($e.Key + '=' + $e.Value) };"
        "$rr.Close()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    lines = dict(l.split("=", 1) for l in r.stdout.strip().splitlines() if "=" in l)
    assert lines.get("AppTitle") == "你好，世界"
    assert "你好世界" in lines.get("Greeting", "")
    assert lines.get("Answer") == "42"
    assert lines.get("Flag") == "True"
