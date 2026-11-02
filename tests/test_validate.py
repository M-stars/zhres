"""校验器测试（A10）：占位符丢失 / & 丢失 / 术语缺失 分别产生 error/warning。"""

from __future__ import annotations

from zhres.glossary import Glossary
from zhres.validate import accel_conflicts, extract_placeholders, validate_pair


class TestPlaceholders:
    def test_collect_kinds(self):
        ph = extract_placeholders("Hi %s, %1$d of {0} items\\n%x%")
        assert "%s" in ph and "%1$d" in ph and "{0}" in ph and "\\n" in ph
        assert "%x%" not in ph

    def test_missing_placeholder_is_error(self):
        errors, _ = validate_pair("File %s not found (%d)", "文件未找到")
        assert any("占位符丢失" in e for e in errors)

    def test_extra_placeholder_is_error(self):
        errors, _ = validate_pair("Ready", "就绪 %s")
        assert any("占位符多余" in e for e in errors)

    def test_reorder_ok(self):
        errors, _ = validate_pair("%s and %d", "A %d B %s")
        assert not errors

    def test_curly_and_escapes(self):
        errors, _ = validate_pair("Line1\\nLine2 {name}", "行1\\n行2 {name}")
        assert not errors


class TestAccelerators:
    def test_missing_amp_is_error(self):
        errors, _ = validate_pair("E&xit", "退出")
        assert any("快捷键丢失" in e for e in errors)

    def test_amp_convention_ok(self):
        errors, _ = validate_pair("E&xit", "退出(&X)")
        assert not errors

    def test_amp_plain_ok(self):
        errors, warnings = validate_pair("&New", "&新建")
        assert not errors

    def test_double_amp_literal(self):
        errors, _ = validate_pair("A&&B", "A&&B")
        assert not errors

    def test_conflicts_warning(self):
        conflicts = accel_conflicts([
            ("menu", ["F"]), ("menu", ["F"]), ("other", ["F"]),
        ])
        assert len(conflicts) == 1 and "menu" in conflicts[0]


class TestTermsAndEmpty:
    def test_missing_term_is_warning_not_error(self):
        g = Glossary(terms={"File": "文件"})
        errors, warnings = validate_pair("Open File", "打开档案",
                                         glossary=g)
        assert not errors
        assert any("术语缺失" in w for w in warnings)

    def test_empty_translation_is_error(self):
        errors, _ = validate_pair("Open", "  ")
        assert any("译文为空" in e for e in errors)

    def test_cjk_translation_fine(self):
        errors, _ = validate_pair("Select All", "全选(&A)")
        assert not errors
