"""计划任务入口必须把生长循环失败传给 Windows 任务计划器。"""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows cmd 批处理测试")
@pytest.mark.parametrize("exit_code", [0, 23])
def test_growth_scheduled_returns_child_status(tmp_path, exit_code):
    source = Path(__file__).with_name("growth_scheduled.bat")
    text = source.read_bytes().decode("gbk")
    text = text.replace("cd /d E:\\ai-learn\\rag_kb", f'cd /d "{tmp_path}"')
    text = text.replace("timeout /t 30 /nobreak >nul", "rem no wait in test")
    assert 'exit /b %run_result%' in text
    batch = tmp_path / "growth_scheduled_test.bat"
    batch.write_bytes(text.encode("gbk"))
    (tmp_path / "uv.cmd").write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="ascii")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path) + os.pathsep + env["PATH"]
    result = subprocess.run(["cmd", "/c", str(batch)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code
    assert (tmp_path / "growth_failed.flag").exists() == (exit_code != 0)
