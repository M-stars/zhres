"""GUI 全流程集成测试：驱动真实事件循环与后台线程完成
extract → import CSV → writeback → verify → restore（对话框打桩）。"""

from __future__ import annotations

import sys
import time
import unittest.mock as mock

import pytest

tk = pytest.importorskip("tkinter", reason="无 tkinter 环境")

def _make_root():
    """构造 Tk root；本机杀软会间歇性锁住 tcl 脚本导致初始化失败，
    重试若干次，仍失败则跳过（环境问题，非代码缺陷）。"""
    last = None
    for _ in range(6):
        try:
            root = tk.Tk()
            root.withdraw()
            return root
        except tk.TclError as exc:
            last = exc
            time.sleep(1.0)
    pytest.skip(f"Tk 初始化被环境间歇阻断（杀软锁 tcl 文件）：{last}")


from zhres.gui import GuiApp  # noqa: E402
from zhres.project import Project  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="仅 Windows")


def _drive(app, root, timeout=60):
    """等待后台任务完成（轮询事件循环，让 after 回调处理队列）。"""
    deadline = time.time() + timeout
    while app._busy and time.time() < deadline:
        root.update()
        time.sleep(0.02)
    # 再转几圈让 _poll_queue 吸干队列
    for _ in range(5):
        root.update()
        time.sleep(0.02)
    assert not app._busy, "任务超时未完成"


@pytest.fixture(autouse=True)
def _no_dialogs(monkeypatch):
    """无头环境：所有 message box 打桩，错误收集到列表。"""
    seen = []
    import zhres.gui as gui
    monkeypatch.setattr(gui.messagebox, "showerror",
                        lambda *a, **k: seen.append(("error", a)))
    monkeypatch.setattr(gui.messagebox, "showwarning",
                        lambda *a, **k: seen.append(("warning", a)))
    monkeypatch.setattr(gui.messagebox, "showinfo",
                        lambda *a, **k: seen.append(("info", a)))
    monkeypatch.setattr(gui.messagebox, "askyesno",
                        lambda *a, **k: seen.append(("ask", a)) or True)
    yield seen


@pytest.fixture
def env(tmp_path, monkeypatch, clean_home):
    from fixture_pe import build_pe, make_string_block
    exe = tmp_path / "app.exe"
    exe.write_bytes(build_pe([(6, 1, 1033, make_string_block(
        ["Open", "Save", "Cancel", "Close"] + [""] * 12))]))
    project = tmp_path / "app.exe.zhres-project.json"
    csv_path = tmp_path / "t.csv"
    csv_path.write_text(
        "id,source,translation,status,notes\r\n"
        '"RT_STRING/1/1033/0","Open","打开","untranslated",""\r\n'
        '"RT_STRING/1/1033/1","Save","保存","untranslated",""\r\n'
        '"RT_STRING/1/1033/2","Cancel","取消","untranslated",""\r\n'
        '"RT_STRING/1/1033/3","Close","关闭","untranslated",""\r\n',
        encoding="utf-8-sig")
    return {"exe": str(exe), "project": str(project), "csv": str(csv_path),
            "tm": str(tmp_path / "tm.db"), "original": exe.read_bytes()}


def test_gui_full_pipeline(env):
    root = _make_root()
    app = GuiApp(root)
    try:
        # 1. extract
        app.var_exe.set(env["exe"])
        app.var_project.set(env["project"])
        app.on_extract()
        _drive(app, root)
        assert app.project is not None
        assert len(app.tree.get_children()) == 4

        # 2. import CSV（打桩文件选择框）
        app.var_tm.set(env["tm"])
        with mock.patch("zhres.gui.filedialog.askopenfilename",
                        return_value=env["csv"]):
            app.on_import_csv()
        _drive(app, root)
        assert app.tree.set("RT_STRING/1/1033/0", column="translation") == "打开"
        assert app.tree.set("RT_STRING/1/1033/0", column="status") == "human"

        # 3. writeback（打桩确认框）
        with mock.patch("zhres.gui.messagebox.askyesno", return_value=True):
            app.on_writeback()
        _drive(app, root)
        project = Project.load(env["project"])
        assert project.target.get("backup_path")

        # 4. verify
        app.on_verify()
        _drive(app, root)
        project = Project.load(env["project"])
        from zhres.verify import verify_project
        assert verify_project(project, env["exe"]).ok

        # 5. restore（打桩确认框）
        before_restore = list(app.project.entries)
        with mock.patch("zhres.gui.messagebox.askyesno", return_value=True):
            app.on_restore()
        _drive(app, root)
        with open(env["exe"], "rb") as f:
            assert f.read() == env["original"]
        assert before_restore[0].translation == "打开"  # 工程内译文仍在
    finally:
        root.destroy()
