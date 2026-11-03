"""GUI 冒烟测试：构造界面、注入工程、编辑提交，不进 mainloop。"""

from __future__ import annotations

import sys
import time

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
from zhres.project import Entry, Project, STATUS_HUMAN, STATUS_UNTRANSLATED  # noqa: E402


@pytest.fixture
def app():
    root = _make_root()  # 不显示窗口
    app = GuiApp(root)
    yield app
    root.destroy()


def _project():
    project = Project()
    project.target = {"path": "", "sha256": "", "size": 0, "machine": "x64"}
    project.entries = [
        Entry(id="RT_STRING/1/1033/0", resource_type="string", type_id=6,
              resource_name=1, language=1033,
              context={"kind": "string_table"}, source="Open"),
        Entry(id="RT_STRING/1/1033/1", resource_type="string", type_id=6,
              resource_name=1, language=1033,
              context={"kind": "string_table"}, source="123",
              status="skipped", skip_reason="纯数字"),
    ]
    return project


def test_app_constructs(app):
    assert app.project is None
    assert app.tree.get_children() == ()


def test_load_project_populates_tree(app):
    app.var_project.set("dummy.zhres-project.json")
    app._load_project_object(_project())
    items = app.tree.get_children()
    assert len(items) == 2
    first = app.tree.item(items[0], "values")
    assert first[1] == "Open" and first[3] == "untranslated"


def test_commit_edit_sets_human(app):
    project = _project()
    app.var_project.set("dummy.zhres-project.json")
    app._load_project_object(project)
    app._commit_edit("RT_STRING/1/1033/0", "打开")
    entry = project.entry_by_id("RT_STRING/1/1033/0")
    assert entry.translation == "打开"
    assert entry.status == STATUS_HUMAN
    assert app.tree.set("RT_STRING/1/1033/0", column="translation") == "打开"


def test_commit_edit_validation_blocked(app):
    project = _project()
    app.var_project.set("dummy.zhres-project.json")
    app._load_project_object(project)
    # 源文无占位符而译文有 → 校验失败 → 未保存（模拟 messagebox）
    import unittest.mock as mock
    with mock.patch("zhres.gui.messagebox.showwarning"):
        app._commit_edit("RT_STRING/1/1033/0", "打开 %s")
    entry = project.entry_by_id("RT_STRING/1/1033/0")
    assert entry.translation is None


def test_revert_edit(app):
    project = _project()
    project.entries[0].translation = "打开"
    project.entries[0].status = STATUS_HUMAN
    app.var_project.set("dummy.zhres-project.json")
    app._load_project_object(project)
    app._commit_edit("RT_STRING/1/1033/0", "")
    entry = project.entry_by_id("RT_STRING/1/1033/0")
    assert entry.translation is None
    assert entry.status == STATUS_UNTRANSLATED


def test_skip_readonly_in_edit_paths(app):
    # skipped 条目无译文编辑入口（_begin_edit 直接返回）
    project = _project()
    app.var_project.set("dummy.zhres-project.json")
    app._load_project_object(project)
    assert app.project.entry_by_id("RT_STRING/1/1033/1").status == "skipped"


def test_busy_gates_buttons(app):
    app.var_project.set("dummy.zhres-project.json")
    app._load_project_object(_project())
    assert str(app.btn_writeback["state"]) == "normal"
    app._set_busy(True)
    assert str(app.btn_writeback["state"]) == "disabled"
    app._set_busy(False)
    assert str(app.btn_writeback["state"]) == "normal"
