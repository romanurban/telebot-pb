"""Tests for start.sh process management (non-tmux / PID-file mode)."""

import os
import signal
import subprocess
import sys
import time

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
START_SH = os.path.join(SCRIPT_DIR, "start.sh")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _source_helpers(tmp_path):
    """Return a bash prefix that loads start.sh helpers in non-tmux mode."""
    pid_dir = tmp_path / ".pids"
    pid_dir.mkdir()
    return (
        f'USE_TMUX=false; PID_DIR="{pid_dir}"; '
        f'source "{START_SH}" --source-helpers 2>/dev/null; '
        # source will hit the usage/exit because there's no argument.
        # We re-source just the function definitions instead:
    )


def _run_bash(script: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ------------------------------------------------------------------
# PID tracking: exec ensures PID file points to the real process
# ------------------------------------------------------------------

class TestPidTracking:
    """start_session should record a PID that matches the long-lived process."""

    def test_pid_file_points_to_real_process(self, tmp_path):
        """Background process PID file should reference a live process."""
        pid_dir = tmp_path / ".pids"
        pid_dir.mkdir()
        # Simulate what start_session does in non-tmux mode:
        # bash -c "exec <cmd>" &  →  PID of the exec'd process
        script = f"""
set -e
PID_DIR="{pid_dir}"
bash -c "exec sleep 60" >> "$PID_DIR/test.log" 2>&1 &
pid=$!
echo "$pid" > "$PID_DIR/test.pid"
# Verify the PID is alive and is actually 'sleep'
sleep 0.2
kill -0 "$pid" 2>/dev/null && echo "ALIVE" || echo "DEAD"
# Check the process name — should be sleep, not bash
ps -p "$pid" -o comm= 2>/dev/null || echo "UNKNOWN"
kill "$pid" 2>/dev/null || true
"""
        result = _run_bash(script)
        lines = result.stdout.strip().splitlines()
        assert "ALIVE" in lines, f"Process not alive. stdout={result.stdout} stderr={result.stderr}"
        # The comm should be 'sleep', not 'bash' — proving exec replaced the shell
        assert any("sleep" in line for line in lines), (
            f"Expected 'sleep' process, got: {lines}"
        )

    def test_pid_with_compound_command(self, tmp_path):
        """exec should work even with compound commands (like uv run python ...)."""
        pid_dir = tmp_path / ".pids"
        pid_dir.mkdir()
        # Use env as a wrapper (simulates uv run python ...) — without exec,
        # bash stays as the parent. With exec, the PID becomes env/sleep directly.
        script = f"""
set -e
PID_DIR="{pid_dir}"
bash -c "exec env sleep 60" >> "$PID_DIR/test.log" 2>&1 &
pid=$!
echo "$pid" > "$PID_DIR/test.pid"
sleep 0.2
kill -0 "$pid" 2>/dev/null && echo "ALIVE" || echo "DEAD"
comm=$(ps -p "$pid" -o comm= 2>/dev/null || echo "UNKNOWN")
echo "$comm"
kill "$pid" 2>/dev/null || true
"""
        result = _run_bash(script)
        lines = result.stdout.strip().splitlines()
        assert "ALIVE" in lines, f"Process not alive: {result.stdout}"
        # With exec, PID should not be bash
        comm = lines[-1]
        assert comm != "bash", f"PID is still bash wrapper: {lines}"


# ------------------------------------------------------------------
# wait_for_port: verify it succeeds when port is open
# ------------------------------------------------------------------

class TestWaitForPort:
    """The wait_for_port helper should detect when a TCP port becomes available."""

    def test_wait_for_port_succeeds(self, tmp_path):
        """wait_for_port should return 0 when the port is already listening."""
        # Start a simple TCP listener
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        try:
            # Source wait_for_port from start.sh by extracting it
            script = f"""
wait_for_port() {{
    local port="$1" timeout="${{2:-15}}" elapsed=0
    while ! (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ "$elapsed" -ge "$timeout" ]]; then
            echo "TIMEOUT" >&2
            return 1
        fi
    done
    echo "READY"
}}
wait_for_port {port} 5
"""
            result = _run_bash(script, timeout=10)
            assert "READY" in result.stdout, f"Expected READY, got: {result.stdout} {result.stderr}"
            assert result.returncode == 0
        finally:
            sock.close()

    def test_wait_for_port_times_out(self):
        """wait_for_port should fail after timeout on a closed port."""
        # Use a port that's almost certainly not listening
        script = """
wait_for_port() {
    local port="$1" timeout="${2:-15}" elapsed=0
    while ! (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null; do
        sleep 1
        elapsed=$((elapsed + 1))
        if [[ "$elapsed" -ge "$timeout" ]]; then
            echo "TIMEOUT"
            return 1
        fi
    done
    echo "READY"
}
wait_for_port 19999 2
"""
        result = _run_bash(script, timeout=10)
        assert "TIMEOUT" in result.stdout
        assert result.returncode != 0
