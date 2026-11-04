"""zhres.gui —— zhres 图形界面（tkinter / ttk，全部标准库）。

布局：目标文件与工程操作区 → 条目表格（双击编辑译文）→ 翻译选项 →
主流程按钮（翻译/回写/校验/还原）→ 日志区。

耗时操作（提取/翻译/回写/校验/还原）在后台线程执行，通过队列回传
结果，界面不冻结。LLM 默认关闭，与 CLI 行为一致。
"""

from __future__ import annotations

import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

from .cli import ensure_legal_notice, file_sha256, run_extract
from .config import load_config
from .errors import ZhresError
from .glossary import Glossary
from .project import (STATUS_APPROVED, STATUS_HUMAN, STATUS_UNTRANSLATED,
                      Project)
from .tm import TranslationMemory

BG_BUSY = "#f0f0f0"


class GuiApp:
    """主窗口。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("zhres 汉化助手")
        self.root.geometry("1080x720")
        self.root.minsize(860, 560)

        self.project: Project | None = None
        self.project_path: str | None = None
        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._dirty = False

        self._build_menu()
        self._build_widgets()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="新建工程（提取）…", command=self.on_extract)
        m_file.add_command(label="打开工程…", command=self.on_open_project)
        m_file.add_command(label="保存工程", command=self.on_save_project)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=m_file)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="法律与道德说明", command=self._show_legal)
        m_help.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=m_help)
        self.root.config(menu=menubar)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        # ---- 目标文件 / 工程 ------------------------------------------
        top = ttk.LabelFrame(outer, text=" 1. 目标文件 ", padding=6)
        top.pack(fill="x")
        self.var_exe = tk.StringVar()
        self.var_project = tk.StringVar()
        ttk.Label(top, text="exe/dll：").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_exe).grid(
            row=0, column=1, sticky="ew", padx=4)
        btn_row = ttk.Frame(top)
        btn_row.grid(row=0, column=2)
        ttk.Button(btn_row, text="浏览…", command=self._pick_exe).pack(
            side="left")
        ttk.Button(btn_row, text="游戏文件夹…",
                   command=self._pick_game_folder).pack(side="left",
                                                        padx=(4, 0))
        ttk.Button(btn_row, text="扫描本机软件",
                   command=self._open_software_picker).pack(side="left",
                                                            padx=(4, 0))
        ttk.Label(top, text="工程：").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.var_project).grid(
            row=1, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="浏览…", command=self._pick_project).grid(
            row=1, column=2)
        top.columnconfigure(1, weight=1)

        row = ttk.Frame(top)
        row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.btn_extract = ttk.Button(row, text="提取资源",
                                      command=self.on_extract)
        self.btn_extract.pack(side="left")
        self.btn_open = ttk.Button(row, text="打开已有工程",
                                   command=self.on_open_project)
        self.btn_open.pack(side="left", padx=6)
        self.btn_save = ttk.Button(row, text="保存工程",
                                   command=self.on_save_project, state="disabled")
        self.btn_save.pack(side="left")
        self.btn_export = ttk.Button(row, text="导出 CSV",
                                     command=self.on_export_csv,
                                     state="disabled")
        self.btn_export.pack(side="left", padx=6)
        self.btn_import = ttk.Button(row, text="导入 CSV",
                                     command=self.on_import_csv,
                                     state="disabled")
        self.btn_import.pack(side="left")

        # ---- 条目表格 --------------------------------------------------
        mid = ttk.LabelFrame(outer, text=" 2. 条目（双击“译文”列直接翻译；"
                             "右键可置状态）", padding=6)
        mid.pack(fill="both", expand=True, pady=6)
        cols = ("id", "source", "translation", "status", "skip")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings")
        headings = {"id": "ID", "source": "原文",
                    "translation": "译文", "status": "状态", "skip": "跳过原因"}
        widths = {"id": 230, "source": 260, "translation": 260,
                  "status": 80, "skip": 110}
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="w")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._begin_edit)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.edit_entry: ttk.Entry | None = None

        # ---- 翻译选项 --------------------------------------------------
        opt = ttk.LabelFrame(outer, text=" 3. 翻译选项 ", padding=6)
        opt.pack(fill="x")
        # 傻瓜式默认：翻译记忆自动使用 ~/.zhres/tm.db（越用越准）
        default_tm = Path.home() / ".zhres" / "tm.db"
        self.var_tm = tk.StringVar(value=str(default_tm))
        self.var_glossary = tk.StringVar()
        self.var_llm = tk.BooleanVar(value=False)
        self.var_dry = tk.BooleanVar(value=False)
        ttk.Label(opt, text="翻译记忆：").grid(row=0, column=0, sticky="w")
        ttk.Entry(opt, textvariable=self.var_tm, width=34).grid(
            row=0, column=1, sticky="w", padx=4)
        ttk.Button(opt, text="…", width=3, command=self._pick_tm).grid(
            row=0, column=2)
        ttk.Label(opt, text="术语表：").grid(row=0, column=3, sticky="w",
                                            padx=(12, 0))
        ttk.Entry(opt, textvariable=self.var_glossary, width=30).grid(
            row=0, column=4, sticky="w", padx=4)
        ttk.Button(opt, text="…", width=3, command=self._pick_glossary).grid(
            row=0, column=5)
        ttk.Checkbutton(opt, text="使用 LLM（需配置 API key，联网）",
                        variable=self.var_llm).grid(row=1, column=1,
                                                    sticky="w")
        ttk.Checkbutton(opt, text="试运行（不写盘、不联网）",
                        variable=self.var_dry).grid(row=1, column=4,
                                                    sticky="w")

        # ---- 主流程按钮 ------------------------------------------------
        act = ttk.LabelFrame(outer, text=" 4. 执行 ", padding=6)
        act.pack(fill="x")
        self.btn_translate = ttk.Button(act, text="自动翻译（TM/术语/LLM）",
                                        command=self.on_translate,
                                        state="disabled")
        self.btn_translate.pack(side="left")
        self.btn_writeback = ttk.Button(act, text="回写（自动备份+字体补丁）",
                                        command=self.on_writeback,
                                        state="disabled")
        self.btn_writeback.pack(side="left", padx=6)
        self.btn_verify = ttk.Button(act, text="校验", command=self.on_verify,
                                     state="disabled")
        self.btn_verify.pack(side="left")
        self.btn_restore = ttk.Button(act, text="还原原版",
                                      command=self.on_restore, state="disabled")
        self.btn_restore.pack(side="left", padx=6)

        # ---- 日志 ------------------------------------------------------
        logf = ttk.LabelFrame(outer, text=" 日志 ", padding=4)
        logf.pack(fill="both", expand=False, pady=(6, 0))
        self.log_text = tk.Text(logf, height=9, state="disabled",
                                font=("Microsoft YaHei UI", 9))
        self.log_text.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    # 日志 / 线程
    # ------------------------------------------------------------------

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        needs_project = (self.btn_save, self.btn_export, self.btn_import,
                         self.btn_translate, self.btn_writeback,
                         self.btn_verify)
        for btn in (self.btn_extract, self.btn_open, self.btn_save,
                    self.btn_export, self.btn_import, self.btn_translate,
                    self.btn_writeback, self.btn_verify, self.btn_restore):
            if busy or (btn in needs_project and self.project is None):
                btn.configure(state="disabled")
            else:
                btn.configure(state="normal")

    def _run_async(self, fn, done_text: str) -> None:
        """在后台线程执行 fn()；fn 可调用 self.log（线程安全化）。"""
        if self._busy:
            messagebox.showinfo("请稍候", "有任务正在执行。")
            return
        q = self._queue
        log_buf: list[str] = []

        def safe_log(msg: str) -> None:
            log_buf.append(msg)

        def worker():
            try:
                result = fn(safe_log)
                q.put(("ok", done_text, log_buf, result))
            except ZhresError as exc:
                msg = str(exc)
                if "Win32 5" in msg or "拒绝访问" in msg or "PermissionError" in msg:
                    msg += ("\n\n提示：目标可能位于 Program Files 等受保护目录，"
                            "或程序正在运行。\n请以管理员身份重新运行本工具，"
                            "并关闭目标程序后重试。")
                q.put(("error", msg, log_buf, None))
            except Exception as exc:  # 未知错误给完整栈，便于反馈
                q.put(("error", f"{exc}\n{traceback.format_exc()}",
                       log_buf, None))

        self._set_busy(True)
        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, text, log_buf, result = self._queue.get_nowait()
                for line in log_buf:
                    self.log(line)
                self._set_busy(False)
                if kind == "error":
                    self.log(f"[失败] {text}")
                    messagebox.showerror("操作失败", text)
                else:
                    self.log(f"[完成] {text}")
                    self._after_task(result)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _after_task(self, result) -> None:
        """任务成功后的界面刷新（由各 on_xxx 提供数据结构）。"""
        if isinstance(result, Project):
            self._load_project_object(result)
        elif isinstance(result, dict) and result.get("refresh_tree"):
            self._populate_tree()

    # ------------------------------------------------------------------
    # 文件选择
    # ------------------------------------------------------------------

    def _pick_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="选择目标 exe/dll",
            filetypes=[("PE 文件", "*.exe *.dll"), ("所有文件", "*.*")])
        if path:
            self.var_exe.set(path)
            if not self.var_project.get():
                self.var_project.set(path + ".zhres-project.json")

    def _open_software_picker(self) -> None:
        """傻瓜式入口：扫描本机已安装软件，选中即自动提取。"""
        picker = tk.Toplevel(self.root)
        picker.title("本机已安装软件")
        picker.geometry("860x560")
        picker.transient(self.root)

        bar = ttk.Frame(picker, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text="搜索：").pack(side="left")
        var_search = tk.StringVar()
        search_entry = ttk.Entry(bar, textvariable=var_search)
        search_entry.pack(side="left", fill="x", expand=True, padx=4)
        status = ttk.Label(picker, text="点击“重新扫描”开始。")
        status.pack(fill="x", padx=6)

        cols = ("name", "path")
        holder = ttk.Frame(picker)
        holder.pack(fill="both", expand=True, padx=6)
        tree = ttk.Treeview(holder, columns=cols, show="headings")
        tree.heading("name", text="软件名称")
        tree.heading("path", text="程序路径")
        tree.column("name", width=220)
        tree.column("path", width=560)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        apps: list = []

        def populate() -> None:
            kw = var_search.get().strip().lower()
            tree.delete(*tree.get_children())
            for a in apps:
                if kw and kw not in a.name.lower() and \
                        kw not in a.exe_path.lower():
                    continue
                tree.insert("", "end", iid=a.exe_path,
                            values=(a.name, a.exe_path))

        def do_scan_folder() -> None:
            folder = filedialog.askdirectory(title="选择要扫描的文件夹"
                                             "（游戏/便携版目录）")
            if not folder:
                return
            status.configure(text=f"正在扫描 {folder} …")
            tree.delete(*tree.get_children())

            def worker():
                from .software import scan_folder
                return scan_folder(folder, max_depth=4)

            def poll():
                if scan_thread.is_alive():
                    picker.after(150, poll)
                    return
                nonlocal apps
                apps = scan_result or []
                populate()
                status.configure(text=f"文件夹扫描完成：{len(apps)} 个程序")

            scan_result: list = []
            import threading as _threading
            def run():
                nonlocal scan_result
                from .software import scan_folder as _sf
                scan_result = _sf(folder, max_depth=4)
            scan_thread = _threading.Thread(target=run, daemon=True)
            scan_thread.start()
            poll()

        def do_rescan() -> None:
            status.configure(text="正在扫描注册表与开始菜单 …（首次约几秒）")
            tree.delete(*tree.get_children())
            import threading as _threading
            scan_result: list = []

            def run():
                nonlocal scan_result
                from .software import scan_all
                scan_result = scan_all()

            def poll():
                if scan_thread.is_alive():
                    picker.after(150, poll)
                    return
                nonlocal apps
                apps = scan_result or []
                populate()
                status.configure(text=f"扫描完成：{len(apps)} 个程序"
                                 "（双击选择并自动提取）")

            scan_result: list = []
            scan_thread = _threading.Thread(target=run, daemon=True)
            scan_thread.start()
            poll()

        def on_select(event) -> None:
            item = tree.identify_row(event.y)
            if not item:
                return
            exe = item
            self.var_exe.set(exe)
            if not self.var_project.get():
                self.var_project.set(exe + ".zhres-project.json")
            picker.destroy()
            self.on_extract()

        tree.bind("<Double-1>", on_select)
        var_search.trace_add("write", lambda *_: populate())

        btns = ttk.Frame(picker, padding=6)
        btns.pack(fill="x")
        ttk.Button(btns, text="重新扫描（注册表+开始菜单）",
                   command=do_rescan).pack(side="left")
        ttk.Button(btns, text="扫描指定文件夹…",
                   command=do_scan_folder).pack(side="left", padx=6)
        ttk.Button(btns, text="选择并提取", command=on_select).pack(
            side="right")

        do_rescan()

    def _pick_project(self) -> None:
        path = filedialog.askopenfilename(
            title="选择翻译工程",
            filetypes=[("zhres 工程", "*.zhres-project.json"),
                       ("所有文件", "*.*")])
        if path:
            self.var_project.set(path)

    def _pick_game_folder(self) -> None:
        """游戏文件夹提取：选择目录后自动扫描全部数据文件。"""
        folder = filedialog.askdirectory(
            title="选择游戏文件夹（RPG Maker / Unity / GameMaker 等）")
        if not folder:
            return
        self.var_exe.set(folder)
        if not self.var_project.get():
            self.var_project.set(folder.rstrip("/\\") +
                                 ".zhres-project.json")
        self.on_extract()

    def _pick_tm(self) -> None:
        path = filedialog.asksaveasfilename(title="选择/新建翻译记忆数据库",
                                            defaultextension=".db")
        if path:
            self.var_tm.set(path)

    def _pick_glossary(self) -> None:
        path = filedialog.askopenfilename(
            title="选择术语表",
            filetypes=[("术语表", "*.toml *.json"), ("所有文件", "*.*")])
        if path:
            self.var_glossary.set(path)

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------

    def on_extract(self) -> None:
        exe = self.var_exe.get().strip()
        if not exe:
            messagebox.showinfo("提示", "请先选择目标 exe/dll。")
            return
        out = self.var_project.get().strip() or exe + ".zhres-project.json"
        self.var_project.set(out)

        def task(log):
            project, warnings = run_extract(exe, out, force=False, log=log)
            for w in warnings:
                log(f"警告：{w}")
            return project

        self._run_async(task, "提取完成")

    def on_open_project(self) -> None:
        path = self.var_project.get().strip()
        if not path:
            self._pick_project()
            path = self.var_project.get().strip()
        if not path:
            return
        try:
            project = Project.load(path)
        except ZhresError as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self._load_project_object(project)

    def on_save_project(self) -> None:
        if self.project is None or not self.project_path:
            return
        try:
            self.project.save(self.project_path)
        except ZhresError as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self._dirty = False
        self.log(f"[保存] {self.project_path}")

    def on_export_csv(self) -> None:
        if self.project is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出 CSV", defaultextension=".csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        from .csvio import export_csv
        try:
            export_csv(self.project, path)
        except ZhresError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.log(f"[导出] {len(self.project.entries)} 条 → {path}"
                 "（UTF-8 with BOM，Excel 可直接打开）")
        messagebox.showinfo("导出完成",
                            f"已导出 {len(self.project.entries)} 条。\n"
                            "在 Excel 中填写 translation 列后，"
                            "用“导入 CSV”合并回工程。")

    def on_import_csv(self) -> None:
        if self.project is None:
            return
        path = filedialog.askopenfilename(
            title="选择要导入的 CSV", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        tm_path = self.var_tm.get().strip()  # tkinter 变量只能在主线程读

        def task(log):
            from .csvio import import_csv
            tm = TranslationMemory(tm_path) if tm_path else None
            try:
                result = import_csv(self.project, path, tm=tm)
            finally:
                if tm is not None:
                    tm.close()
            self.project.save(self.project_path)
            return {"import_result": result, "refresh_tree": True}

        self._run_async(task, "导入完成")

    def on_translate(self) -> None:
        if self.project is None:
            return
        dry = self.var_dry.get()
        use_llm = self.var_llm.get() and not dry
        tm_path = self.var_tm.get().strip()
        glossary_path = self.var_glossary.get().strip()

        def task(log):
            from .tm import TranslationMemory
            from .translate import translate_project
            config = load_config()
            tm = TranslationMemory(tm_path) if tm_path else None
            glossary = Glossary.load(glossary_path) if glossary_path else None
            llm = None
            if use_llm:
                from .llm import make_llm_filler
                llm = make_llm_filler(config)
            try:
                result = translate_project(
                    self.project, tm=tm, glossary=glossary, llm=llm,
                    dry_run=dry,
                    save_callback=lambda: self.project.save(self.project_path))
            finally:
                if tm is not None:
                    tm.close()
            summary = (f"TM 命中 {result.tm_hits}，术语 {result.glossary_hits}，"
                       f"LLM {result.llm_hits}，不动 {result.untouched}，"
                       f"失败 {len(result.failed)}")
            log("统计：" + summary)
            for w in result.warnings[:10]:
                log(f"警告：{w}")
            for f in result.failed[:10]:
                log(f"校验失败：{f}")
            if not dry:
                self.project.save(self.project_path)
            return {"refresh_tree": not dry}

        self._run_async(task, "试运行完成（未写盘）" if dry else "翻译完成")

    def on_writeback(self) -> None:
        if self.project is None:
            return
        exe = self.var_exe.get().strip() or self.project.target.get("path")
        if not exe or not Path(exe).is_file():
            messagebox.showerror("回写失败", f"目标文件不存在：{exe}")
            return
        if not messagebox.askyesno(
                "确认回写",
                "将修改目标文件（会先自动备份原文件）。\n"
                f"目标：{exe}\n\n继续？"):
            return

        def task(log):
            from .writeback import make_backup, writeback
            current = file_sha256(exe)
            if current != self.project.target.get("sha256"):
                raise ZhresError(
                    f"目标文件与工程记录不一致（SHA-256 不符）：{exe}\n"
                    "文件可能已被修改或替换。")
            backup, _ = make_backup(Path(exe), self.project)
            log(f"已备份：{backup}")
            config = load_config()
            wb = writeback(self.project, exe, project_path=self.project_path,
                           font=config["writeback"]["font"],
                           font_charset=config["writeback"]["font_charset"])
            log(f"回写完成：更新 {wb.changed_resources} 个资源")
            for fp in wb.font_patches:
                log(f"字体补丁：{fp['resource']} → {fp['to']}")
            for w in wb.length_warnings:
                log(f"警告：{w}")
            if wb.verify_ok:
                log("自动校验：通过")
            else:
                for p in wb.verify_problems:
                    log(f"校验差异：{p}")
            self.project.save(self.project_path)
            return {"verify_ok": wb.verify_ok}

        self._run_async(task, "回写完成")

    def on_verify(self) -> None:
        if self.project is None:
            return
        exe = self.var_exe.get().strip() or self.project.target.get("path")

        def task(log):
            from .verify import verify_project
            vr = verify_project(self.project, exe)
            log(f"对照 {vr.checked} 条，资源集合一致性："
                f"{'一致' if vr.ok else '存在差异'}")
            for p in vr.problems[:20]:
                log(f"差异：{p}")
            return {"verify_ok": vr.ok}

        self._run_async(task, "校验完成")

    def on_restore(self) -> None:
        exe = self.var_exe.get().strip() or (
            self.project.target.get("path") if self.project else "")
        if not exe or not Path(exe).is_file():
            messagebox.showerror("还原失败", f"目标文件不存在：{exe}")
            return
        if not messagebox.askyesno(
                "确认还原",
                f"将从备份还原：\n{exe}\n\n当前文件会先另存副本。继续？"):
            return

        def task(log):
            from .restore import restore
            r = restore(exe, yes=True)
            log(f"已备份当前文件：{r.before_restore}")
            log(f"还原完成，SHA-256 校验通过：{r.restored_sha}")
            return None

        self._run_async(task, "还原完成")

    # ------------------------------------------------------------------
    # 表格
    # ------------------------------------------------------------------

    def _load_project_object(self, project: Project) -> None:
        self.project = project
        self.project_path = self.var_project.get().strip() or \
            self.project_path
        if self.project.target.get("path"):
            self.var_exe.set(self.project.target["path"])
        self._populate_tree()
        self._set_busy(False)
        seen = project.resources_seen or {}
        translatable = sum(1 for e in project.entries
                           if e.status != "skipped")
        self.log(f"[工程] {len(project.entries)} 条（可翻译 "
                 f"{translatable}），资源概览：{seen}")

    def _populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.project is None:
            return
        for e in self.project.entries:
            self.tree.insert("", "end", iid=e.id, values=(
                e.id, e.source, e.translation or "", e.status,
                e.skip_reason))

    def _begin_edit(self, event) -> None:
        if self.project is None:
            return
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or column != "#3":  # 仅译文列可编辑
            return
        entry = self.project.entry_by_id(item)
        if entry is None or entry.status == "skipped":
            return
        x, y, w, h = self.tree.bbox(item, column)
        value = self.tree.set(item, column="translation")
        editor = ttk.Entry(self.tree)
        editor.place(x=x, y=y, width=w, height=h)
        editor.insert(0, value)
        editor.focus_set()
        editor.select_range(0, "end")
        self.edit_entry = editor

        def commit(event=None):
            new_value = editor.get()
            self._commit_edit(item, new_value)
            editor.destroy()
            self.edit_entry = None

        def cancel(event=None):
            editor.destroy()
            self.edit_entry = None

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def _commit_edit(self, entry_id: str, text: str) -> None:
        entry = self.project.entry_by_id(entry_id)
        if entry is None:
            return
        text = text.strip()
        if text == "":
            entry.translation = None
            entry.status = STATUS_UNTRANSLATED
            entry.notes = ""
        else:
            from . import validate as validate_mod
            errors, warnings = validate_mod.validate_pair(
                entry.source, text,
                context_kind=entry.context.get("kind", ""))
            if errors:
                messagebox.showwarning(
                    "校验未通过", f"{entry.id}\n" + "\n".join(errors)
                    + "\n\n未保存。")
                return
            entry.translation = text
            entry.status = STATUS_HUMAN
            entry.notes = ""
        self.tree.set(entry_id, column="translation",
                      value=entry.translation or "")
        self.tree.set(entry_id, column="status", value=entry.status)
        self._dirty = True

    def _show_context_menu(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if not item or self.project is None:
            return
        self.tree.selection_set(item)
        entry = self.project.entry_by_id(item)
        if entry is None or entry.status == "skipped":
            return
        menu = tk.Menu(self.tree, tearoff=0)

        def set_status(status: str):
            entry.status = status
            self.tree.set(item, column="status", value=status)
            self._dirty = True

        menu.add_command(label="置为 已批准（approved）",
                         command=lambda: set_status(STATUS_APPROVED))
        menu.add_command(label="置为 人工（human）",
                         command=lambda: set_status(STATUS_HUMAN))
        menu.add_separator()
        menu.add_command(label="撤销译文", command=lambda: self._commit_edit(
            item, ""))
        menu.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------------
    # 杂项
    # ------------------------------------------------------------------

    def _show_legal(self) -> None:
        from .cli import LEGAL_NOTICE
        messagebox.showinfo("法律与道德说明", LEGAL_NOTICE.replace(
            "本提示仅显示一次（--help 可再次查看）。", ""))

    def _show_about(self) -> None:
        from . import __version__
        messagebox.showinfo(
            "关于 zhres",
            f"zhres 汉化助手 {__version__}\n\n"
            "Windows 静态资源汉化工具（提取 → 翻译 → 回写）。\n"
            "仅用于自用汉化合法拥有的软件；修改后的文件不得再分发。")

    def _on_close(self) -> None:
        if self._dirty:
            if not messagebox.askyesno(
                    "退出", "有未保存的修改，确定退出？"):
                return
        self.root.destroy()


def main() -> int:
    """启动图形界面。"""
    try:
        ensure_legal_notice()
    except Exception:
        pass
    root = tk.Tk()
    try:
        ttk.Style(root).theme_use("vista")
    except tk.TclError:
        pass
    GuiApp(root)

    def report_exception(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, Exception):
            root.__class__.report_callback_exception(
                root, exc_type, exc_value, exc_tb)
            return
        messagebox.showerror(
            "界面错误", "".join(traceback.format_exception(
                exc_type, exc_value, exc_tb)))

    root.report_callback_exception = report_exception
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
