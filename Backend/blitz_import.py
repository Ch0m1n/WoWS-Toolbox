from __future__ import annotations

"""Import a stopped, user-installed Blitz client's assets through ADB."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid

from blitz_assets import resolve_blitz_layout
from runtime_i18n import is_english

PACKAGE = "net.wargaming.wows.blitz"
APP = f"/data/data/{PACKAGE}"
EXTERNAL = f"/sdcard/Android/data/{PACKAGE}/files"
BUNDLE = f"{APP}/files/bundle"


def message(ko: str, en: str) -> str:
    return en if is_english() else ko


def progress(percent: int, ko: str, en: str) -> None:
    print("[PROGRESS] " + json.dumps({"stage": "Blitz", "percent": percent,
          "message": message(ko, en)}, ensure_ascii=False), flush=True)


def adb_path(value: str) -> str:
    if value:
        path = Path(value).expanduser()
        if not path.is_file():
            raise RuntimeError(message("ADB 실행 파일 경로를 확인해 주세요.", "Check the ADB executable path."))
        return str(path.resolve())
    candidates = [shutil.which("adb")]
    for key in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        if os.environ.get(key):
            candidates.append(str(Path(os.environ[key]) / "platform-tools" / "adb.exe"))
    candidates.append(str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/sdk/platform-tools/adb.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(message("ADB를 찾지 못했어요. 에뮬레이터 또는 Android SDK의 adb.exe를 선택해 주세요.",
                               "ADB was not found. Select adb.exe from your emulator or Android SDK."))


class Adb:
    def __init__(self, executable: str, device: str = ""):
        self.executable, self.device = executable, device
        self.root_shell = False

    def command(self, *args: str) -> list[str]:
        return [self.executable] + (["-s", self.device] if self.device else []) + list(args)

    def run(self, *args: str, check: bool = True, timeout: int = 40) -> str:
        result = subprocess.run(self.command(*args), capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if check and result.returncode:
            # Do not publish shell/dumpsys contents or private device data in logs.
            raise RuntimeError(message("ADB 명령에 실패했어요. 연결과 root 권한을 확인해 주세요.",
                                       "ADB command failed. Check the connection and root permissions."))
        return result.stdout.decode("utf-8", errors="replace").strip()

    def shell_command(self, command: str) -> list[str]:
        # exec-out quotes argv itself. Adding shell quotes here makes Android
        # attempt to execute the entire script as one filename.
        return self.command("exec-out", "sh" if self.root_shell else "su", "-c", command)

    def shell(self, command: str) -> str:
        # shell carries remote exit status. Keep binary transfers on exec-out:
        # this emulator translates LF to CRLF even with shell -T.
        args = self.command("shell", "-T", "sh" if self.root_shell else "su", "-c", shlex.quote(command))
        result = subprocess.run(args, capture_output=True, timeout=40,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError(message("Android 데이터 확인에 실패했어요. root 권한과 다운로드 상태를 확인해 주세요.",
                                       "Android data check failed. Check root access and downloaded resources."))
        return result.stdout.decode("utf-8", errors="replace").strip()

    def select(self, requested: str) -> None:
        rows = self.run("devices").splitlines()[1:]
        devices = [parts[0] for row in rows if len(parts := row.split()) >= 2 and parts[1] == "device"]
        if requested and requested not in devices and re.fullmatch(r"[A-Za-z0-9.-]+:[0-9]{1,5}", requested):
            self.run("connect", requested)
            rows = self.run("devices").splitlines()[1:]
            devices = [parts[0] for row in rows if len(parts := row.split()) >= 2 and parts[1] == "device"]
        if requested:
            if requested not in devices:
                raise RuntimeError(message("지정한 기기에 연결할 수 없어요. ADB 주소와 연결 승인을 확인해 주세요.",
                                           "The selected device is unavailable. Check its ADB address and authorization."))
            self.device = requested
        elif len(devices) == 1:
            self.device = devices[0]
        elif not devices:
            raise RuntimeError(message("에뮬레이터가 연결되지 않았어요. '블리츠 에뮬레이터 실행'을 누르고 부팅이 끝난 뒤 다시 가져와 주세요.",
                                       "No emulator is connected. Click Start Blitz emulator, wait for boot, then import again."))
        else:
            raise RuntimeError(message("연결 기기가 여러 대예요. 고급 설정에 기기 ID 또는 ADB 주소를 입력해 주세요.",
                                       "Multiple devices connected. Enter a device ID or ADB address in advanced settings."))

    def root(self) -> None:
        if "uid=0(" not in self.run("shell", "id"):
            self.run("root", check=False)
            self.run("wait-for-device")
        self.root_shell = "uid=0(" in self.run("shell", "id")
        if "uid=0(" not in self.shell("id"):
            raise RuntimeError(message("root 권한이 필요해요. 에뮬레이터의 root 설정을 확인해 주세요.",
                                       "Root access is required. Check the emulator's root setting."))

    def stopped(self) -> None:
        # A missing pidof command must fail closed, rather than imply a stopped game.
        result = self.shell(f"command -v pidof >/dev/null && (pidof {PACKAGE} || true)")
        if result:
            raise RuntimeError(message("블리츠를 종료한 뒤 다시 눌러 주세요. 다운로드도 먼저 끝내야 해요.",
                                       "Close Blitz and finish resource downloads before importing."))

    def fingerprint(self, obb: str, design: str | None) -> str:
        assets = [BUNDLE, obb] + ([design] if design else [])
        command = "find " + " ".join(shlex.quote(path) for path in assets)
        command += " -type f -exec stat -c '%n|%s|%Y' {} +"
        inventory = self.shell(command)
        if not inventory:
            raise RuntimeError("Could not verify the source asset inventory")
        return hashlib.sha256("\n".join(sorted(inventory.splitlines())).encode("utf-8")).hexdigest()


def safe_tar_copy(archive: tarfile.TarFile, destination: Path, report_progress: bool = True) -> int:
    """Accept only ordinary files/directories contained in a fresh destination."""
    seen: set[str] = set()
    count = 0
    last = time.monotonic()
    root = destination.resolve()
    for member in archive:
        name = PurePosixPath(member.name)
        if (name.is_absolute() or ".." in name.parts or "\\" in member.name
                or ":" in member.name or any(part.rstrip(" .") != part for part in name.parts)):
            raise RuntimeError("Unsafe asset archive path")
        if any(re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
               for part in name.parts):
            raise RuntimeError("Invalid Windows asset filename")
        target = destination.joinpath(*name.parts)
        if not target.resolve().is_relative_to(root):
            raise RuntimeError("Asset archive escaped destination")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile() or member.size < 0:
            raise RuntimeError("Asset archive contains an unsupported link or entry")
        key = str(target).casefold()
        if key in seen:
            raise RuntimeError("Duplicate asset archive file")
        seen.add(key)
        if shutil.disk_usage(destination).free < member.size + 64 * 1024 * 1024:
            raise RuntimeError(message("복사할 디스크 공간이 부족해요.", "Not enough free disk space to copy assets."))
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError("Missing archive data")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)
        if target.stat().st_size != member.size:
            raise RuntimeError("Incomplete asset copy")
        count += 1
        if report_progress and time.monotonic() - last > 1:
            progress(35, f"번들 파일 {count}개를 복사했어요", f"Copied {count} bundle files")
            last = time.monotonic()
    return count


class TailReader:
    def __init__(self, stream):
        self.stream = stream
        self.tail = b""

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.tail = (self.tail + data)[-256:]
        return data


def copy_remote(adb: Adb, folder: str, entry: str, destination: Path) -> None:
    command = f"tar -C {shlex.quote(folder)} -cf - {shlex.quote(entry)}"
    token = "BLITZ_TAR_EXIT_" + uuid.uuid4().hex
    # exec-out does not reliably carry remote exit status. Append a private
    # status trailer after tar EOF and check it even if tarfile read ahead.
    command += f"; rc=$?; printf '\\n{token}:%s\\n' \"$rc\""
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(adb.shell_command(command), stdout=subprocess.PIPE, stderr=errors,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        timer = threading.Timer(3600, process.kill)
        timer.daemon = True
        timer.start()
        stream = TailReader(process.stdout)
        try:
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                safe_tar_copy(archive, destination, report_progress=entry == ".")
            # tar padding can remain after end-of-archive; drain to avoid a blocked writer.
            while stream.read(1024 * 1024):
                pass
            if process.wait(timeout=40) or not stream.tail.endswith(("\n" + token + ":0\n").encode("ascii")):
                raise RuntimeError(message("데이터 전송이 중단됐어요. 새 폴더로 다시 가져와 주세요.",
                                           "Transfer was interrupted. Retry into a new folder."))
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def import_data(adb: Adb, parent: Path, requested: str = "") -> dict:
    progress(5, "ADB 연결을 확인하는 중", "Checking ADB connection")
    adb.select(requested)
    adb.root()
    adb.stopped()
    adb.shell(f"test -d {BUNDLE}/prefab/ship/body")
    obbs = adb.shell(f"find /sdcard/Android/obb/{PACKAGE} -maxdepth 1 -type f -name 'main.*.obb'").splitlines()
    if len(obbs) != 1 or not re.fullmatch(rf"/sdcard/Android/obb/{re.escape(PACKAGE)}/main\.[0-9]+\.{re.escape(PACKAGE)}\.obb", obbs[0]):
        raise RuntimeError(message("현재 버전의 기본 OBB가 하나 있어야 해요. 게임 다운로드 상태를 확인해 주세요.",
                                   "Exactly one current main OBB is required. Check game downloads."))
    obb = PurePosixPath(obbs[0])
    external_design = f"{EXTERNAL}/Assets/Resources/DesignData"
    designs = adb.shell(
        f"if test -f {external_design}; then printf '%s\\n' {external_design}; "
        f"else find {APP}/files -type f -name DesignData; fi"
    ).splitlines()
    design = next((p for p in designs if p.startswith((APP + "/files/", EXTERNAL + "/"))
                   and ".." not in PurePosixPath(p).parts), None)
    before = adb.fingerprint(obbs[0], design)
    parent.mkdir(parents=True, exist_ok=True)
    token = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    partial = parent / ("Blitz-" + token + ".partial")
    final = parent / ("Blitz-" + token)
    partial.mkdir()
    # Failed/cancelled snapshots are visibly partial and never become the active game path.
    progress(15, "블리츠 번들을 새 폴더로 복사하는 중", "Copying Blitz bundles into a new folder")
    bundle = partial / "full_bundle"
    bundle.mkdir()
    copy_remote(adb, BUNDLE, ".", bundle)
    progress(70, "기본 OBB를 복사하는 중", "Copying the main OBB")
    downloads = partial / "downloads"
    downloads.mkdir()
    copy_remote(adb, str(obb.parent), obb.name, downloads)
    if design:
        progress(85, "함선 이름 정보를 복사하는 중", "Copying ship name data")
        path = PurePosixPath(design)
        copy_remote(adb, str(path.parent), path.name, partial)
    adb.stopped()
    if adb.fingerprint(obbs[0], design) != before:
        raise RuntimeError(message("복사 중 게임 데이터가 바뀌었어요. 업데이트가 끝난 뒤 다시 가져와 주세요.",
                                   "Game data changed during transfer. Finish updates and retry."))
    layout = resolve_blitz_layout(partial, require_obb=True)
    bodies = list(layout.body_root.glob("*.ab"))
    if not bodies or layout.obb_path.stat().st_size == 0:
        raise RuntimeError(message("복사한 함선 데이터가 비어 있어요. 게임 리소스를 먼저 내려받아 주세요.",
                                   "Copied ship data is empty. Download game resources first."))
    report = {"schema": "wows-toolbox-blitz-import/v1", "body_count": len(bodies),
              "obb": obb.name, "design_data": bool(design), "path": str(final.resolve())}
    (partial / "blitz-import.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.rename(final)
    progress(100, "블리츠 데이터 가져오기 완료", "Blitz data import complete")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adb", default="")
    parser.add_argument("--device", default="")
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = import_data(Adb(adb_path(args.adb)), args.destination, args.device)
        print("[RESULT] " + json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except (RuntimeError, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
        print("[ERROR] " + str(error), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
