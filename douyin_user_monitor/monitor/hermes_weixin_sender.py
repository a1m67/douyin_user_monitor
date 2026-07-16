from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence


class CommandRunner(Protocol):
    async def run(self, command: Sequence[str], *, timeout_seconds: float) -> tuple[int, str, str]:
        ...


@dataclass(frozen=True)
class HermesWeixinConfig:
    enabled: bool
    ssh_host: str
    ssh_user: str
    hermes_home: str
    hermes_bin: str
    target: str
    timeout_seconds: float


class SubprocessCommandRunner:
    async def run(self, command: Sequence[str], *, timeout_seconds: float) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"hermes 微信发送超时: {timeout_seconds}s") from exc
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        return int(process.returncode or 0), stdout, stderr


class HermesWeixinSender:
    def __init__(
        self,
        config: HermesWeixinConfig,
        *,
        runner: Optional[CommandRunner] = None,
    ):
        self._config = config
        self._runner = runner or SubprocessCommandRunner()

    async def send(self, message: str) -> None:
        if not self._config.enabled:
            raise RuntimeError("hermes 微信通知未启用")
        text = str(message or "").strip()
        if not text:
            raise ValueError("告警消息不能为空")

        remote = (
            f"export HERMES_HOME={_shell_quote(self._config.hermes_home)}; "
            f"{_shell_quote(self._config.hermes_bin)} send "
            f"--to {_shell_quote(self._config.target)} --quiet -- "
            f"{_shell_quote(text)}"
        )
        command = self._build_ssh_command(remote)
        code, stdout, stderr = await self._runner.run(
            command,
            timeout_seconds=self._config.timeout_seconds,
        )
        if code != 0:
            detail = (stderr or stdout or "").strip() or f"exit={code}"
            raise RuntimeError(f"hermes 微信发送失败: {detail}")

    def _build_ssh_command(self, remote: str) -> List[str]:
        host = self._config.ssh_host.strip()
        user = self._config.ssh_user.strip() or "root"
        if not host:
            raise ValueError("hermes_weixin.ssh_host 不能为空")
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{user}@{host}",
            remote,
        ]


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
