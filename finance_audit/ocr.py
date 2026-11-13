import json
import subprocess
from pathlib import Path

from .config import BASE_DIR


def windows_ocr(image_path):
    if not Path(image_path).is_file():
        raise ValueError("图片文件不存在")
    script = BASE_DIR / "scripts" / "windows_ocr.ps1"
    process = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Path", str(Path(image_path).resolve())],
        capture_output=True, timeout=60,
    )
    if process.returncode:
        error = process.stderr.decode("gb18030", errors="replace")[-1500:]
        raise ValueError(f"本地 OCR 失败：{error or 'PowerShell 未成功执行'}")
    try:
        return json.loads(process.stdout.decode("utf-8-sig").strip())
    except json.JSONDecodeError as exc:
        raise ValueError("本地 OCR 返回内容无法解析") from exc
