"""Cross-platform process supervisor for backend and frontend services."""

import getpass
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from codelens.bootstrap.settings import Settings

_IS_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class SupervisorConfig:
    """Frontend-specific configuration for the supervisor."""

    frontend_host: str = "127.0.0.1"
    frontend_port: int = 5173


def _resolve_health_host(bind_host: str) -> str:
    """Resolve a bind address to a connectable address for health checks."""
    if bind_host == "0.0.0.0":
        return "127.0.0.1"
    return bind_host


def _is_running(pid: int) -> bool:
    """Check whether a process with the given PID exists."""
    if _IS_WINDOWS:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _get_child_pids(pid: int) -> list[int]:
    """Return direct child PIDs of the given process."""
    if _IS_WINDOWS:
        try:
            result = subprocess.run(
                ["wmic", "process", "where", f"(ParentProcessId={pid})", "get", "ProcessId"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            pids: list[int] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
            return pids
        except (subprocess.SubprocessError, OSError):
            return []
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    except (subprocess.SubprocessError, FileNotFoundError):
        return []


def _kill_process_tree(pid: int) -> None:
    """Kill a process and all its descendants."""
    if not _is_running(pid):
        return

    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            pass
        return

    # Unix: kill children first (bottom-up), then the parent
    for child in _get_child_pids(pid):
        _kill_process_tree(child)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return

    # Wait up to 5 seconds for graceful shutdown
    for _ in range(50):
        if not _is_running(pid):
            return
        time.sleep(0.1)

    # Force kill
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _get_user_id() -> str:
    """Return a user identifier for the runtime directory name."""
    if _IS_WINDOWS:
        return os.environ.get("USERNAME", "unknown")
    return os.environ.get("USER", getpass.getuser())


class Supervisor:
    """Manage the lifecycle of backend and frontend processes."""

    def __init__(self, project_root: Path | None = None) -> None:
        if project_root is None:
            self._project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
        else:
            self._project_root = project_root
        self._runtime_dir = Path(tempfile.gettempdir()) / f"codelens-review-{_get_user_id()}"
        self._backend_pid_file = self._runtime_dir / "backend.pid"
        self._frontend_pid_file = self._runtime_dir / "frontend.pid"
        self._config_file = self._runtime_dir / "config.json"
        self._log_dir = self._project_root / "logs"

    def start(
        self,
        settings: Settings,
        config: SupervisorConfig,
        *,
        default_root: Path | None = None,
    ) -> None:
        """Install dependencies, start backend and frontend, and wait for readiness."""
        self._check_dependencies()
        self._ensure_not_running()

        # Prepare directories
        self._runtime_dir.mkdir(parents=True, exist_ok=True)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Clean old unified log
        unified_log = self._log_dir / "unified.log"
        if unified_log.exists():
            unified_log.unlink()

        backend_pid: int | None = None
        frontend_pid: int | None = None

        try:
            # Start backend
            backend_pid = self._start_backend(settings, default_root)
            self._write_pid(self._backend_pid_file, backend_pid)

            # Start frontend
            frontend_pid = self._start_frontend(config, settings.host, settings.port)
            self._write_pid(self._frontend_pid_file, frontend_pid)

            # Save running config for stop()
            self._save_running_config(settings.port, config.frontend_port)

            # Wait for readiness
            backend_health_host = _resolve_health_host(settings.host)
            frontend_health_host = _resolve_health_host(config.frontend_host)

            self._wait_for_http(
                f"http://{backend_health_host}:{settings.port}/api/health",
                backend_pid,
                "Backend",
            )
            self._wait_for_http(
                f"http://{frontend_health_host}:{config.frontend_port}",
                frontend_pid,
                "Frontend",
            )
        except Exception:
            # Clean up on failure
            if backend_pid and _is_running(backend_pid):
                _kill_process_tree(backend_pid)
            if frontend_pid and _is_running(frontend_pid):
                _kill_process_tree(frontend_pid)
            self._cleanup_pid_files()
            raise

        self._print_ready(settings.host, settings.port, config.frontend_host, config.frontend_port)

    def stop(self) -> None:
        """Stop all running services and clean up PID files."""
        was_running = False

        # Stop tracked processes
        for pid_file in (self._backend_pid_file, self._frontend_pid_file):
            pid = self._read_pid(pid_file)
            if pid and _is_running(pid):
                was_running = True
                _kill_process_tree(pid)
            if pid_file.exists():
                pid_file.unlink()

        # Fallback: kill processes on our ports
        fallback_ports = self._load_running_ports()
        for port in fallback_ports:
            for pid in self._find_pids_on_port(port):
                if _is_running(pid):
                    was_running = True
                    _kill_process_tree(pid)

        # Fallback: kill codelens-review processes by name
        for pid in self._find_pids_by_name("codelens-review start"):
            if _is_running(pid):
                was_running = True
                _kill_process_tree(pid)

        # Clean runtime directory
        if self._runtime_dir.exists():
            try:
                shutil.rmtree(self._runtime_dir)
            except OSError:
                pass

        if was_running:
            print("CodeLens stopped.")
        else:
            print("CodeLens is not running.")

    def restart(
        self,
        settings: Settings,
        config: SupervisorConfig,
        *,
        default_root: Path | None = None,
    ) -> None:
        """Stop all services and start them again."""
        self.stop()
        self.start(settings, config, default_root=default_root)

    # ── Private helpers ────────────────────────────────────────────────────

    def _check_dependencies(self) -> None:
        """Verify that required tools are available."""
        for cmd, url in [
            ("uv", "https://docs.astral.sh/uv/"),
            ("pnpm", "https://pnpm.io/installation"),
            ("git", "https://git-scm.com/downloads"),
        ]:
            if not shutil.which(cmd):
                raise RuntimeError(f"{cmd} is required: {url}")

        print("\n[1/3] Installing backend dependencies...")
        result = subprocess.run(
            ["uv", "sync", "--project", "backend"],
            cwd=self._project_root,
        )
        if result.returncode != 0:
            raise RuntimeError("Backend dependency installation failed")

        print("\n[2/3] Installing frontend dependencies...")
        result = subprocess.run(
            ["pnpm", "--dir", "frontend", "install"],
            cwd=self._project_root,
        )
        if result.returncode != 0:
            raise RuntimeError("Frontend dependency installation failed")

    def _ensure_not_running(self) -> None:
        """Raise if any service is already running."""
        for pid_file in (self._backend_pid_file, self._frontend_pid_file):
            pid = self._read_pid(pid_file)
            if pid and _is_running(pid):
                raise RuntimeError(
                    "CodeLens is already running; "
                    "use 'codelens-review restart' or 'codelens-review stop'"
                )

    def _start_backend(self, settings: Settings, default_root: Path | None = None) -> int:
        """Spawn the backend process and return its PID."""
        env = os.environ.copy()
        env["CODELENS_HOST"] = settings.host
        env["CODELENS_PORT"] = str(settings.port)

        supervisor_log = self._log_dir / "supervisor.log"
        log_file = open(supervisor_log, "a")

        cmd = [
            "uv", "run", "--project", "backend",
            "codelens-review", "run-backend",
            "--host", settings.host,
            "--port", str(settings.port),
        ]
        if settings.data_dir != self._project_root / "data":
            cmd.extend(["--data-dir", str(settings.data_dir)])
        # When the user has not configured explicit repository roots, trust the
        # directory from which the start command was invoked so that repositories
        # under the user's working directory are reviewable by default.
        if not settings.repository_roots and default_root is not None:
            cmd.append(str(default_root))
        for root in settings.repository_roots:
            cmd.append(str(root))

        proc = subprocess.Popen(
            cmd,
            cwd=self._project_root,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        log_file.close()
        return proc.pid

    def _start_frontend(
        self, config: SupervisorConfig, backend_host: str, backend_port: int
    ) -> int:
        """Spawn the frontend process and return its PID."""
        env = os.environ.copy()
        env["CODELENS_API_HOST"] = _resolve_health_host(backend_host)
        env["CODELENS_API_PORT"] = str(backend_port)
        env["CODELENS_FRONTEND_PORT"] = str(config.frontend_port)

        frontend_log = self._log_dir / "frontend.log"
        log_file = open(frontend_log, "a")

        cmd = [
            "pnpm", "--dir", "frontend", "dev",
            "--host", config.frontend_host,
            "--port", str(config.frontend_port),
            "--strictPort",
        ]

        proc = subprocess.Popen(
            cmd,
            cwd=self._project_root,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
        )
        log_file.close()
        return proc.pid

    def _wait_for_http(self, url: str, pid: int, name: str, timeout_seconds: int = 30) -> None:
        """Poll an HTTP endpoint until it responds or timeout."""
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not _is_running(pid):
                raise RuntimeError(f"{name} failed to start; inspect the logs")
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=1) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError, ConnectionError):
                pass
            time.sleep(0.5)
        raise RuntimeError(f"{name} did not become ready within {timeout_seconds} seconds")

    def _read_pid(self, pid_file: Path) -> int | None:
        """Read a PID from a file, returning None if invalid or missing."""
        if not pid_file.exists():
            return None
        try:
            content = pid_file.read_text().strip()
            return int(content) if content.isdigit() else None
        except OSError:
            return None

    def _write_pid(self, pid_file: Path, pid: int) -> None:
        """Write a PID to a file."""
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(pid))

    def _save_running_config(self, backend_port: int, frontend_port: int) -> None:
        """Save running ports so stop() can use them instead of hardcoded defaults."""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        self._config_file.write_text(
            json.dumps({"backend_port": backend_port, "frontend_port": frontend_port})
        )

    def _load_running_ports(self) -> list[int]:
        """Read running ports from config file, falling back to defaults."""
        if self._config_file.exists():
            try:
                data = json.loads(self._config_file.read_text())
                return [data["backend_port"], data["frontend_port"]]
            except (OSError, json.JSONDecodeError, KeyError):
                pass
        return [8800, 5173]

    def _cleanup_pid_files(self) -> None:
        """Remove PID files and runtime directory."""
        for pid_file in (self._backend_pid_file, self._frontend_pid_file):
            if pid_file.exists():
                try:
                    pid_file.unlink()
                except OSError:
                    pass
        if self._runtime_dir.exists():
            try:
                self._runtime_dir.rmdir()
            except OSError:
                pass

    def _find_pids_on_port(self, port: int) -> list[int]:
        """Find PIDs of processes listening on the given port."""
        if _IS_WINDOWS:
            try:
                result = subprocess.run(
                    ["netstat", "-ano", "-p", "TCP"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                pids: list[int] = []
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        if parts and parts[-1].isdigit():
                            pids.append(int(parts[-1]))
                return list(set(pids))
            except (subprocess.SubprocessError, OSError):
                return []
        try:
            result = subprocess.run(
                ["ss", "-tlnp"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            pids = []
            for line in result.stdout.splitlines():
                if f":{port} " in line:
                    # Extract pid=NNNN from ss output
                    for part in line.split():
                        if part.startswith("pid="):
                            pid_str = part.split("=")[1].split(",")[0]
                            if pid_str.isdigit():
                                pids.append(int(pid_str))
            return list(set(pids))
        except (subprocess.SubprocessError, FileNotFoundError):
            return []

    def _find_pids_by_name(self, name: str) -> list[int]:
        """Find PIDs of processes matching the given command name."""
        if _IS_WINDOWS:
            try:
                result = subprocess.run(
                    ["wmic", "process", "where", f"CommandLine like '%{name}%'"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                pids: list[int] = []
                for line in result.stdout.splitlines():
                    if "ProcessId" in line:
                        parts = line.split()
                        for part in parts:
                            if part.isdigit():
                                pids.append(int(part))
                return list(set(pids))
            except (subprocess.SubprocessError, OSError):
                return []
        try:
            result = subprocess.run(
                ["pgrep", "-f", name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
        except (subprocess.SubprocessError, FileNotFoundError):
            return []

    def _print_ready(
        self,
        backend_host: str,
        backend_port: int,
        frontend_host: str,
        frontend_port: int,
    ) -> None:
        """Print the ready message with service addresses."""
        display_backend_host = _resolve_health_host(backend_host)
        display_frontend_host = _resolve_health_host(frontend_host)

        print("\nCodeLens is ready. Open these addresses:")
        print(f"  Frontend:  http://{display_frontend_host}:{frontend_port}")
        print(f"  Backend:   http://{display_backend_host}:{backend_port}")
        print(f"  OpenAPI:   http://{display_backend_host}:{backend_port}/docs")
        print("\nAll locally accessible Git repositories are allowed by default.")
        print("Choose a repository and configure model gateways in the Web UI.")
        print("Run 'codelens-review stop' to stop all services.\n")
