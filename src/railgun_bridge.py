"""
Railgun Sidecar Bridge — Python HTTP client for the Node.js Railgun sidecar.

This module manages the sidecar process lifecycle and provides a clean
Python API for the ColdStack GUI to interact with Railgun.

The sidecar is a Node.js Express server that wraps the @railgun-community/wallet
SDK. Communication is via localhost HTTP (JSON requests/responses).
"""

import os
import sys
import json
import time
import subprocess
import threading
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path


class RailgunSidecar:
    """Manages the Railgun Node.js sidecar process and HTTP communication."""

    DEFAULT_PORT = 8765
    DEFAULT_TIMEOUT = 30  # seconds for regular requests
    LONG_TIMEOUT = 120    # seconds for proof generation, transfers
    STARTUP_TIMEOUT = 30  # seconds to wait for sidecar to come online

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._running = False

    # ─── Process Lifecycle ───

    def start(self) -> bool:
        """Start the Node.js sidecar process.

        Returns True if the sidecar is running and healthy.
        Returns False if the sidecar could not be started.
        """
        with self._lock:
            if self._process and self._process.poll() is None:
                # Process already running
                return self.is_healthy()

            sidecar_dir = self._find_sidecar_dir()
            if sidecar_dir is None:
                raise FileNotFoundError(
                    "Railgun sidecar directory not found. Expected 'sidecar/' "
                    "in the ColdStack root or EXE directory."
                )

            server_js = sidecar_dir / "src" / "server.js"
            if not server_js.exists():
                raise FileNotFoundError(
                    f"Sidecar entry point not found: {server_js}"
                )

            node_exe = self._find_node_executable()
            if node_exe is None:
                raise FileNotFoundError(
                    "Node.js executable not found. The Railgun sidecar requires "
                    "Node.js to be installed, or the sidecar to be compiled to "
                    "a standalone executable."
                )

            env = os.environ.copy()
            env["SIDECAR_PORT"] = str(self.port)

            self._process = subprocess.Popen(
                [node_exe, str(server_js)],
                cwd=str(sidecar_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                creationflags=self._get_creation_flags(),
            )

            self._running = True

            threading.Thread(
                target=self._read_logs,
                daemon=True,
                name="sidecar-logs"
            ).start()

            return self._wait_for_health()

    def stop(self) -> None:
        """Stop the sidecar process gracefully."""
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None
            self._running = False

    def restart(self) -> bool:
        """Restart the sidecar process."""
        self.stop()
        time.sleep(1)
        return self.start()

    def is_running(self) -> bool:
        """Check if the sidecar process is running."""
        return self._process is not None and self._process.poll() is None

    def is_healthy(self) -> bool:
        """Check if the sidecar is responding to health checks."""
        try:
            result = self._request("GET", "/health", timeout=5)
            return result.get("status") == "ok"
        except Exception:
            return False

    # ─── Engine API ───

    def init_engine(
        self,
        rpc_config: Dict[str, Dict[str, Any]],
        ppoi_nodes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Initialize the Railgun engine with RPC config and POI nodes."""
        payload = {
            "rpcConfig": rpc_config,
            "ppoiNodes": ppoi_nodes or ["https://ppoi.fdi.network"],
        }
        return self._request("POST", "/engine/init", payload, timeout=self.LONG_TIMEOUT)

    def get_engine_status(self) -> Dict[str, Any]:
        """Get the current engine status."""
        return self._request("GET", "/engine/status")

    def shutdown_engine(self) -> Dict[str, Any]:
        """Shut down the Railgun engine (but keep sidecar running)."""
        return self._request("POST", "/engine/shutdown")

    # ─── Wallet API ───

    def load_wallet(
        self,
        mnemonic: str,
        encryption_key: str,
        creation_block_map: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """Load a Railgun wallet from mnemonic."""
        payload = {
            "mnemonic": mnemonic,
            "encryptionKey": encryption_key,
        }
        if creation_block_map:
            payload["creationBlockMap"] = creation_block_map
        return self._request("POST", "/wallet/load", payload, timeout=self.LONG_TIMEOUT)

    def list_wallets(self) -> Dict[str, Any]:
        """List all loaded Railgun wallets."""
        return self._request("GET", "/wallet/list")

    # ─── Balance API ───

    def refresh_balances(self, chain: str, wallet_ids: List[str]) -> Dict[str, Any]:
        """Trigger a balance scan for the specified wallets."""
        payload = {"chain": chain, "walletIds": wallet_ids}
        return self._request("POST", "/balances/refresh", payload, timeout=self.LONG_TIMEOUT)

    def get_balance_status(self) -> Dict[str, Any]:
        """Get current balance status for all wallets."""
        return self._request("GET", "/balances/status")

    # ─── Transfer API ───

    def shielded_transfer(
        self,
        chain: str,
        from_wallet_id: str,
        to_address: str,
        token_address: str,
        amount: str,
        memo_text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a 0zk→0zk private transfer."""
        payload = {
            "chain": chain,
            "fromWalletId": from_wallet_id,
            "toAddress": to_address,
            "tokenAddress": token_address,
            "amount": amount,
        }
        if memo_text:
            payload["memoText"] = memo_text
        return self._request("POST", "/transfer/shielded", payload, timeout=self.LONG_TIMEOUT)

    def shield(
        self,
        chain: str,
        from_public_address: str,
        to_wallet_id: str,
        token_address: str,
        amount: str
    ) -> Dict[str, Any]:
        """Shield tokens (public → private)."""
        payload = {
            "chain": chain,
            "fromPublicAddress": from_public_address,
            "toWalletId": to_wallet_id,
            "tokenAddress": token_address,
            "amount": amount,
        }
        return self._request("POST", "/transfer/shield", payload, timeout=self.LONG_TIMEOUT)

    def unshield(
        self,
        chain: str,
        from_wallet_id: str,
        to_public_address: str,
        token_address: str,
        amount: str
    ) -> Dict[str, Any]:
        """Unshield tokens (private → public)."""
        payload = {
            "chain": chain,
            "fromWalletId": from_wallet_id,
            "toPublicAddress": to_public_address,
            "tokenAddress": token_address,
            "amount": amount,
        }
        return self._request("POST", "/transfer/unshield", payload, timeout=self.LONG_TIMEOUT)

    # ─── POI API ───

    def refresh_poi(self, chain: str, wallet_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Trigger POI refresh for the specified wallets."""
        payload = {"chain": chain}
        if wallet_ids:
            payload["walletIds"] = wallet_ids
        return self._request("POST", "/poi/refresh", payload, timeout=self.LONG_TIMEOUT)

    def get_poi_status(self) -> Dict[str, Any]:
        """Get POI status for all wallets."""
        return self._request("GET", "/poi/status")

    # ─── Cache API ───

    def rebuild_cache(self, chain: Optional[str] = None) -> Dict[str, Any]:
        """Full cache rebuild — clears and rescans from block 0."""
        payload = {}
        if chain:
            payload["chain"] = chain
        return self._request("POST", "/cache/rebuild", payload, timeout=300)

    def clear_cache(self, chain: Optional[str] = None) -> Dict[str, Any]:
        """Clear scan cache without full rebuild."""
        payload = {}
        if chain:
            payload["chain"] = chain
        return self._request("POST", "/cache/clear", payload, timeout=60)

    # ─── Internal Helpers ───

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict] = None,
        timeout: int = DEFAULT_TIMEOUT
    ) -> Dict[str, Any]:
        """Make an HTTP request to the sidecar."""
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload else None

        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                error_data = json.loads(body)
                raise RuntimeError(f"Sidecar error: {error_data.get('error', body)}")
            except json.JSONDecodeError:
                raise RuntimeError(f"Sidecar HTTP {e.code}: {body}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Cannot connect to sidecar at {url}: {e}")
        except Exception as e:
            raise RuntimeError(f"Sidecar request failed: {e}")

    def _wait_for_health(self, timeout: int = STARTUP_TIMEOUT) -> bool:
        """Wait for the sidecar to become healthy."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._process and self._process.poll() is not None:
                return False
            if self.is_healthy():
                return True
            time.sleep(0.5)
        return False

    def _read_logs(self) -> None:
        """Read sidecar stdout/stderr in a background thread."""
        if not self._process:
            return
        while self._process and self._process.poll() is None:
            try:
                line = self._process.stdout.readline()
                if line:
                    print(f"[SIDECAR] {line.decode('utf-8', errors='replace').rstrip()}")
            except Exception:
                break

    def _find_sidecar_dir(self) -> Optional[Path]:
        """Find the sidecar directory relative to the ColdStack root."""
        candidates = []

        if getattr(sys, 'frozen', False):
            exe_dir = Path(os.path.dirname(sys.executable))
            candidates.append(exe_dir / "sidecar")
            candidates.append(exe_dir / "resources" / "sidecar")
        else:
            src_dir = Path(os.path.dirname(os.path.abspath(__file__)))
            root_dir = src_dir.parent
            candidates.append(root_dir / "sidecar")

        for candidate in candidates:
            if candidate.exists() and (candidate / "src" / "server.js").exists():
                return candidate

        return None

    def _find_node_executable(self) -> Optional[str]:
        """Find the Node.js executable.

        In dev mode, uses system Node.js.
        In production (packaged EXE), looks for a bundled sidecar executable
        compiled with `pkg`.
        """
        sidecar_dir = self._find_sidecar_dir()
        if sidecar_dir:
            compiled = sidecar_dir / "sidecar.exe"
            if compiled.exists():
                return str(compiled)

        for name in ("node", "node.exe"):
            result = subprocess.run(
                ["where", name] if os.name == "nt" else ["which", name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().split("\n")[0].strip()

        return None

    @staticmethod
    def _get_creation_flags() -> int:
        """Get subprocess creation flags for Windows."""
        if os.name == "nt":
            return 0x08000000
        return 0
