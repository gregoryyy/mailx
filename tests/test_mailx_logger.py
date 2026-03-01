from __future__ import annotations

from tests_support import ame


class TestLogger:
    def test_quiet_suppresses_info(self, capsys):
        log = ame.Logger(quiet=True)
        log.info("should not appear")
        assert capsys.readouterr().out == ""

    def test_info_prints(self, capsys):
        log = ame.Logger(quiet=False)
        log.info("hello")
        assert "hello" in capsys.readouterr().out

    def test_verbose_debug(self, capsys):
        log = ame.Logger(verbose=True)
        log.debug("detail")
        assert "detail" in capsys.readouterr().err

    def test_non_verbose_no_debug(self, capsys):
        log = ame.Logger(verbose=False)
        log.debug("detail")
        assert capsys.readouterr().err == ""

    def test_warn_always_prints(self, capsys):
        log = ame.Logger(quiet=True)
        log.warn("warning!")
        assert "warning!" in capsys.readouterr().err

    def test_log_file_written(self, tmp_path):
        log_path = tmp_path / "test.log"
        with open(log_path, "w") as f:
            log = ame.Logger(log_file=f)
            log.info("logged")
        content = log_path.read_text()
        assert "logged" in content
        assert "[" in content
