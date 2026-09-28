"""Aerodrome staked-position discovery helper.

ColdStack v5.3.24 — read-only transfer-history scan that finds Aerodrome
SlipStream NFTs staked into CL gauges.

The wallet no longer owns the NFT after staking, so a plain balanceOf() scan
misses the position.  This module reconstructs ERC-721 Transfer logs for both
Aerodrome Position Managers, adapts to aggressive Base RPC limits (rotating
across endpoints and shrinking eth_getLogs ranges), and verifies that sent-out
NFTs are held by a CL gauge via pool().

No third-party APIs — only JSON-RPC eth_getLogs/eth_call on BASE.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from lp_engine import LPPosition


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_RPC_URLS = [
    "https://mainnet.base.org",
    "https://base.publicnode.com",
    "https://1rpc.io/base",
    "https://base-rpc.publicnode.com",
]

AERO_SLIPSTREAM_POSITION_MANAGER = "0x827922686190790b37229fd06084350E74485b72"
AERO_SLIPSTREAM_POSITION_MANAGER_2 = "0xe1f8cd9AC4e4A65F54f38a5CdAfCA44f6dD68b53"

V3_POSITION_MANAGERS = [
    AERO_SLIPSTREAM_POSITION_MANAGER,
    AERO_SLIPSTREAM_POSITION_MANAGER_2,
]

SELECTOR_OWNER_OF = "0x6352211e"
SELECTOR_POOL = "0x16f0115b"  # gauge.pool() -> address

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Look back this far when we cannot discover the wallet's first activity block.
DEFAULT_MAX_BLOCKS = 3_800_000  # ~90 days on Base

# Initial chunk size; halved on RPC failure until MIN_CHUNK_SIZE.
DEFAULT_CHUNK_SIZE = 200_000
MIN_CHUNK_SIZE = 50

# Margins used for first-activity discovery and incremental cache overlap.
FIRST_ACTIVITY_MARGIN = 100_000
CACHE_OVERLAP = 1_000

# Parallel getLogs workers.  Public RPCs are the bottleneck and rate-limit
# aggressively, so keep concurrency low.
MAX_WORKERS = 2

# Cache path override hook for tests.
CACHE_FILE: Path = Path.home() / ".coldstack" / "aerodrome_staked_cache.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class StakedScanError(Exception):
    """Raised when no RPC can satisfy the transfer-history scan."""
    pass


class _RpcRetryable(Exception):
    """Internal: a single RPC attempt failed; retry/rotate is appropriate."""
    pass


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _ensure_cache_dir() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        _ensure_cache_dir()
        tmp = CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_FILE)
    except Exception as exc:
        print(f"[aerodrome-staked] cache write failed: {exc}")


def clear_scan_cache(wallet_address: str = "") -> None:
    """Clear the incremental scan cache for one wallet, or all wallets if empty."""
    if not wallet_address:
        try:
            CACHE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return
    cache = _load_cache()
    key = wallet_address.lower().strip()
    if key in cache:
        del cache[key]
        _save_cache(cache)


# ---------------------------------------------------------------------------
# ABI / encoding helpers
# ---------------------------------------------------------------------------

def _pad_address(address: str) -> str:
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    return ("0" * 24) + clean


def _pad_int_to_64(value: int) -> str:
    if value < 0:
        value = (1 << 256) + value
    return format(int(value), "064x")


def _decode_address(hex_str: str) -> str:
    return "0x" + hex_str[-40:].lower()


# ---------------------------------------------------------------------------
# RPC layer
# ---------------------------------------------------------------------------

def _single_url_rpc_call(url: str, method: str, params: list, timeout: float = 15.0) -> Any:
    """Make one JSON-RPC attempt against a specific URL."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ColdStack/5.3.24",
    }
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise _RpcRetryable(f"HTTP {exc.code}: {body[:200]}")
    except Exception as exc:
        raise _RpcRetryable(str(exc))

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise _RpcRetryable(f"RPC error: {msg}")

    if method in ("eth_getLogs", "eth_blockNumber"):
        return data
    return data.get("result")


class _RpcState:
    """Tracks the current RPC endpoint and its adaptive chunk size."""

    def __init__(
        self,
        rpc_call: Optional[Callable[[str, list], Optional[Any]]] = None,
        initial_chunk_size: int = DEFAULT_CHUNK_SIZE,
        min_chunk_size: int = MIN_CHUNK_SIZE,
    ):
        self.rpc_call = rpc_call
        self.urls: List[str] = []
        self.rpc_idx = 0
        self.min_chunk_size = min_chunk_size
        self.initial_chunk_size = initial_chunk_size
        self.chunk_sizes: Dict[str, int] = {}

        if rpc_call is None:
            self.urls = list(BASE_RPC_URLS)
            for url in self.urls:
                self.chunk_sizes[url] = initial_chunk_size
        else:
            self.chunk_sizes["test"] = initial_chunk_size

    def _key(self) -> str:
        if self.rpc_call:
            return "test"
        return self.urls[self.rpc_idx]

    def current_chunk_size(self) -> int:
        return self.chunk_sizes[self._key()]

    def halve_chunk(self) -> int:
        key = self._key()
        new_size = max(self.min_chunk_size, self.chunk_sizes[key] // 2)
        self.chunk_sizes[key] = new_size
        return new_size

    def rotate(self) -> bool:
        """Move to the next RPC endpoint. Returns False if no more endpoints."""
        if self.rpc_call:
            return False
        self.rpc_idx += 1
        if self.rpc_idx >= len(self.urls):
            return False
        # Ensure the new URL has an entry (inherits initial size if unseen).
        key = self._key()
        if key not in self.chunk_sizes:
            self.chunk_sizes[key] = self.initial_chunk_size
        return True

    def _try_current(self, method: str, params: list, timeout: float = 15.0) -> Any:
        if self.rpc_call:
            result = self.rpc_call(method, params)
            if result is None:
                raise _RpcRetryable("test rpc returned None")
            if isinstance(result, dict) and result.get("error"):
                err = result["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                raise _RpcRetryable(f"RPC error: {msg}")
            return result
        url = self.urls[self.rpc_idx]
        return _single_url_rpc_call(url, method, params, timeout)

    def call(self, method: str, params: list, timeout: float = 15.0) -> Any:
        """Make a non-chunked RPC call, rotating endpoints on retryable failure."""
        if self.urls:
            self.rpc_idx = 0
        last_err: Optional[_RpcRetryable] = None
        attempts = len(self.urls) if self.urls else 1
        for _ in range(attempts):
            try:
                return self._try_current(method, params, timeout)
            except _RpcRetryable as exc:
                last_err = exc
                if not self.rotate():
                    break
        raise StakedScanError(
            "Could not scan transfer history — RPC limits hit. "
            "Try entering the deposit ID directly."
            + (f" (last error: {last_err})" if last_err else "")
        )


# ---------------------------------------------------------------------------
# Block / activity helpers
# ---------------------------------------------------------------------------

def _hex_to_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return int(value, 16)
        except (ValueError, TypeError):
            return None
    return None


def _latest_block(state: _RpcState) -> int:
    result = state.call("eth_blockNumber", [])
    block = _hex_to_int(result.get("result") if isinstance(result, dict) else result)
    if block is None:
        raise StakedScanError("Could not read latest block from any RPC.")
    return block


def _transaction_count(state: _RpcState, wallet: str, block_tag: Any) -> int:
    result = state.call("eth_getTransactionCount", [wallet, block_tag])
    value = _hex_to_int(result)
    return value if value is not None else 0


def _find_first_activity_block(wallet: str, latest: int, state: _RpcState) -> Optional[int]:
    """Binary-search the first block where wallet has a non-zero nonce.

    Falls back to None if the RPCs do not support block-numbered tx-count queries.
    """
    try:
        latest_count = _transaction_count(state, wallet, "latest")
    except StakedScanError:
        return None
    if latest_count == 0:
        return None

    lo, hi = 0, latest
    while lo < hi:
        mid = (lo + hi) // 2
        try:
            count = _transaction_count(state, wallet, hex(mid))
        except StakedScanError:
            return None
        if count == 0:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _compute_scan_window(
    wallet_lower: str,
    latest: int,
    state: _RpcState,
    max_blocks: int,
) -> Tuple[int, Dict[str, Any]]:
    """Return (start_block, cache_entry) for the scan."""
    cache = _load_cache()
    entry = cache.get(wallet_lower, {})

    first_activity = entry.get("first_activity_block")
    if first_activity is None:
        first_activity = _find_first_activity_block(wallet_lower, latest, state)
        if first_activity is not None:
            entry["first_activity_block"] = first_activity

    if first_activity is not None:
        start = max(0, first_activity - FIRST_ACTIVITY_MARGIN)
    else:
        start = max(0, latest - max_blocks)

    last_scanned = entry.get("last_scanned_block")
    if last_scanned is not None:
        start = max(start, last_scanned - CACHE_OVERLAP)

    return start, entry


def wallet_address_active(
    wallet_address: str,
    rpc_call: Optional[Callable[[str, list], Optional[Any]]] = None,
) -> bool:
    """Return True if the wallet has ever sent a transaction on Base."""
    wallet_lower = wallet_address.lower().strip()
    if not wallet_lower.startswith("0x") or len(wallet_lower) != 42:
        return False
    state = _RpcState(rpc_call)
    try:
        return _transaction_count(state, wallet_lower, "latest") > 0
    except StakedScanError:
        return False


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def _parse_log_limit_from_error(exc: _RpcRetryable) -> Optional[int]:
    """Extract an explicit block-range limit from RPC error messages.

    Examples that parse successfully:
      - "eth_getLogs is limited to a 2,000 range" -> 2000
      - "eth_getLogs is limited to 0 - 50 blocks range" -> 50
    """
    msg = str(exc)
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(?:blocks?|range)", msg, re.IGNORECASE)
    if m:
        return int(m.group(1).replace(",", ""))
    return None


def _is_rate_limit(exc: _RpcRetryable) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("rate limit", "429", "too many requests"))


def _fetch_logs_chunk(
    state: _RpcState,
    position_manager: str,
    wallet_padded: str,
    topic_idx: int,
    from_block: int,
    to_block: int,
) -> List[Dict[str, Any]]:
    """Fetch Transfer logs for one range, adapting chunk size and rotating RPCs."""
    params = {
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": position_manager,
        "topics": [TRANSFER_TOPIC, None, None],
    }
    params["topics"][topic_idx] = wallet_padded

    rate_limit_attempts = 0
    while True:
        size = state.current_chunk_size()
        actual_to = min(to_block, from_block + size - 1)
        params["toBlock"] = hex(actual_to)

        try:
            data = state._try_current("eth_getLogs", [params])
            rate_limit_attempts = 0
        except _RpcRetryable as exc:
            if _is_rate_limit(exc):
                # Exponential backoff, capped at ~32 s, then keep retrying the
                # same chunk until the caller's deadline kills us.
                time.sleep(min(32.0, 0.5 * (2 ** rate_limit_attempts)))
                rate_limit_attempts += 1
                continue
            rate_limit_attempts = 0
            limit = _parse_log_limit_from_error(exc)
            if limit and limit < state.current_chunk_size():
                state.chunk_sizes[state._key()] = max(MIN_CHUNK_SIZE, limit)
                continue
            old_size = state.current_chunk_size()
            new_size = state.halve_chunk()
            if new_size == old_size:
                # Already at the floor for this RPC — rotate.
                if state.rotate():
                    continue
                raise StakedScanError(
                    "Could not scan transfer history — RPC limits hit. "
                    "Try entering the deposit ID directly."
                    + f" (last error: {exc})"
                )
            continue

        if not isinstance(data, dict):
            return []
        logs = data.get("result")
        if not isinstance(logs, list):
            return []
        return logs


def _fetch_logs_fixed(
    state: _RpcState,
    position_manager: str,
    wallet_padded: str,
    topic_idx: int,
    from_block: int,
    to_block: int,
) -> List[Dict[str, Any]]:
    """Fetch one chunk without mutating scan state; split+retry on range error."""
    params = {
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": position_manager,
        "topics": [TRANSFER_TOPIC, None, None],
    }
    params["topics"][topic_idx] = wallet_padded

    for attempt in range(7):
        try:
            data = state._try_current("eth_getLogs", [params])
            break
        except _RpcRetryable as exc:
            if _is_rate_limit(exc):
                # Exponential backoff on the same chunk instead of splitting;
                # splitting during a rate-limit storm just multiplies requests.
                time.sleep(0.5 * (2 ** attempt))
                continue
            # Range/execution error: split the chunk and try smaller pieces.
            if to_block - from_block < 2:
                return []
            mid = (from_block + to_block) // 2
            left = _fetch_logs_fixed(state, position_manager, wallet_padded, topic_idx, from_block, mid)
            right = _fetch_logs_fixed(state, position_manager, wallet_padded, topic_idx, mid + 1, to_block)
            return left + right
        except Exception as exc:
            return []
    else:
        return []

    if not isinstance(data, dict):
        return []
    logs = data.get("result")
    if not isinstance(logs, list):
        return []
    return logs


def _probe_chunk_size(
    state: _RpcState,
    position_manager: str,
    wallet_padded: str,
    latest: int,
) -> int:
    """Find the largest getLogs range the current RPC will serve for this manager."""
    for _ in range(20):
        size = state.current_chunk_size()
        from_block = max(0, latest - size)
        try:
            _fetch_logs_chunk(state, position_manager, wallet_padded, 1, from_block, latest)
            return state.current_chunk_size()
        except StakedScanError:
            if not state.rotate():
                raise
    raise StakedScanError(
        "Could not scan transfer history — RPC limits hit. "
        "Try entering the deposit ID directly."
    )


def _fetch_all_logs_parallel(
    state: _RpcState,
    wallet_padded: str,
    start_block: int,
    latest_block: int,
    max_seconds: float,
) -> List[Dict[str, Any]]:
    """Scan only outgoing transfers (from=wallet) across both NFPMs in parallel."""
    scan_start = time.monotonic()
    all_logs: List[Dict[str, Any]] = []

    # Determine a workable chunk size for each Position Manager on the current RPC.
    probed_sizes: Dict[str, int] = {}
    for pm in V3_POSITION_MANAGERS:
        probed_sizes[pm] = _probe_chunk_size(state, pm, wallet_padded, latest_block)

    # Build the work list: outgoing direction only (topic_idx=1).
    tasks: List[Tuple[str, int, int]] = []
    for pm in V3_POSITION_MANAGERS:
        size = probed_sizes[pm]
        for from_block in range(start_block, latest_block + 1, size):
            to_block = min(from_block + size - 1, latest_block)
            tasks.append((pm, from_block, to_block))

    if not tasks:
        return []

    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    future_to_task: Dict[Any, Tuple[str, int, int]] = {}
    futures = set()
    for pm, fb, tb in tasks:
        fut = executor.submit(_fetch_logs_fixed, state, pm, wallet_padded, 1, fb, tb)
        future_to_task[fut] = (pm, fb, tb)
        futures.add(fut)

    found_chunk_counts: Dict[str, int] = {}
    try:
        deadline = scan_start + max_seconds
        while futures and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            done, futures = wait(futures, timeout=max(0.1, remaining), return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    logs = future.result(timeout=5)
                except Exception:
                    logs = []
                if logs:
                    pm, fb, tb = future_to_task.get(future, ("?", 0, 0))
                    found_chunk_counts[pm] = found_chunk_counts.get(pm, 0) + 1
                    all_logs.extend(logs)
    finally:
        executor.shutdown(wait=False)

    elapsed = time.monotonic() - scan_start
    coverage_note = ""
    if tasks and start_block is not None and latest_block is not None:
        scanned_range = latest_block - start_block + 1
        if scanned_range > 0:
            coverage_note = f" (covers last {scanned_range:,} blocks only)"
    print(f"[aerodrome-staked] scanned {len(tasks)} chunks in {elapsed:.1f}s, found {len(all_logs)} logs, per-PM={found_chunk_counts}{coverage_note}")
    return all_logs


def _parse_transfer_log(log: Dict[str, Any], wallet_lower: str) -> Optional[Dict[str, Any]]:
    topics = log.get("topics", [])
    if len(topics) < 4:
        return None
    try:
        block_number = int(log.get("blockNumber", "0x0"), 16)
        log_index = int(log.get("logIndex", "0x0"), 16)
    except (ValueError, TypeError):
        return None
    token_id = int(topics[3], 16)
    from_addr = _decode_address(topics[1][2:] if topics[1].startswith("0x") else topics[1])
    to_addr = _decode_address(topics[2][2:] if topics[2].startswith("0x") else topics[2])
    return {
        "token_id": token_id,
        "from": from_addr,
        "to": to_addr,
        "block_number": block_number,
        "log_index": log_index,
        "position_manager": log.get("address", "").lower(),
    }


def _reconstruct_token_ownership(
    transfers: List[Dict[str, Any]],
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    """Return (currently_held, sent_out) token-id maps based on last transfer."""
    history: Dict[int, List[Dict[str, Any]]] = {}
    for tx in transfers:
        tid = tx["token_id"]
        history.setdefault(tid, []).append(tx)

    held: Dict[int, Dict[str, Any]] = {}
    sent: Dict[int, Dict[str, Any]] = {}
    for tid, events in history.items():
        events.sort(key=lambda e: (e["block_number"], e["log_index"]))
        last = events[-1]
        if last["to"] == last["_wallet"]:
            held[tid] = last
        elif last["from"] == last["_wallet"]:
            sent[tid] = last
    return held, sent


# ---------------------------------------------------------------------------
# Gauge verification
# ---------------------------------------------------------------------------

def _call_address(
    target: str,
    data: str,
    state: _RpcState,
) -> Optional[str]:
    try:
        result = state.call("eth_call", [{"to": target, "data": data}, "latest"])
    except StakedScanError:
        # Call reverted or all RPCs failed; treat as "no usable address".
        return None
    if result and isinstance(result, str) and len(result) >= 66:
        return _decode_address(result[2:66])
    return None


def _is_gauge(
    gauge_candidate: str,
    state: _RpcState,
) -> Optional[str]:
    """Return the pool address if gauge_candidate is a CL gauge, else None."""
    pool = _call_address(gauge_candidate, SELECTOR_POOL, state)
    if pool and int(pool, 16) != 0:
        return pool
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_staked_positions(
    wallet_address: str,
    rpc_call: Optional[Callable[[str, list], Optional[Any]]] = None,
    max_blocks: int = DEFAULT_MAX_BLOCKS,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_seconds: float = 120.0,
    use_cache: bool = True,
    return_info: bool = False,
) -> Any:
    """Discover staked Aerodrome SlipStream NFTs via transfer-history scan.

    Returns a list of (token_id, position_manager, gauge_address) tuples for
    positions currently held by a CL gauge (staked).

    The scan:
      - discovers the wallet's first activity block (binary search on nonce) or
        falls back to max_blocks;
      - resumes from the last cached scan block when use_cache=True;
      - rotates across BASE_RPC_URLS and shrinks eth_getLogs ranges when an RPC
        rejects/limit/rate-limits the request;
      - hard-stops after max_seconds and reports coverage via the info dict when
        return_info=True;
      - raises StakedScanError if every RPC fails at the minimum chunk size.

    When return_info=True, returns (discoveries, info) where info contains:
      - complete (bool)
      - scanned_from (int)
      - scanned_to (int)
    """
    wallet_lower = wallet_address.lower().strip()
    if not wallet_lower.startswith("0x") or len(wallet_lower) != 42:
        if return_info:
            return [], {"complete": True, "scanned_from": 0, "scanned_to": 0}
        return []

    state = _RpcState(rpc_call, initial_chunk_size=max(chunk_size, MIN_CHUNK_SIZE))
    latest_block = _latest_block(state)
    start_block, cache_entry = _compute_scan_window(wallet_lower, latest_block, state, max_blocks)

    wallet_padded = "0x" + _pad_address(wallet_lower)
    all_transfers: List[Dict[str, Any]] = []

    scan_start = time.monotonic()
    try:
        logs = _fetch_all_logs_parallel(
            state, wallet_padded, start_block, latest_block, max_seconds
        )
        for log in logs:
            parsed = _parse_transfer_log(log, wallet_lower)
            if parsed:
                parsed["_wallet"] = wallet_lower
                parsed["_direction"] = "from"
                all_transfers.append(parsed)
    finally:
        if use_cache:
            cache_entry["last_scanned_block"] = latest_block
            cache_entry["last_scan_time"] = time.time()
            cache = _load_cache()
            cache[wallet_lower] = cache_entry
            _save_cache(cache)

    elapsed = time.monotonic() - scan_start
    info = {
        "complete": elapsed < max_seconds,
        "scanned_from": start_block,
        "scanned_to": latest_block,
    }

    if not all_transfers:
        if return_info:
            return [], info
        return []

    # We only scanned outgoing transfers.  A token that left the wallet and is
    # now held by a gauge is staked; if it was later sold/transferred back the
    # ownerOf call will tell us.
    staked: List[Tuple[int, str, str]] = []
    verified_gauges: Dict[str, str] = {}  # holder -> pool
    seen_token_pm: set = set()
    for tx in all_transfers:
        tid = tx["token_id"]
        pm = tx["position_manager"]
        if not pm:
            continue
        if (tid, pm) in seen_token_pm:
            continue
        seen_token_pm.add((tid, pm))
        current_owner = _call_address(pm, SELECTOR_OWNER_OF + _pad_int_to_64(tid), state)
        if not current_owner or current_owner == wallet_lower:
            continue
        pool = verified_gauges.get(current_owner)
        if pool is None:
            pool = _is_gauge(current_owner, state)
            if pool:
                verified_gauges[current_owner] = pool
            else:
                verified_gauges[current_owner] = ""  # negative cache
        if not pool:
            continue
        staked.append((tid, pm, current_owner))

    unique: Dict[Tuple[int, str], str] = {}
    for tid, pm, gauge in staked:
        unique[(tid, pm)] = gauge
    discoveries = sorted(
        ((tid, pm, gauge) for (tid, pm), gauge in unique.items()),
        key=lambda t: (t[1], t[0]),
    )
    if return_info:
        return discoveries, info
    return discoveries


def annotate_staked_position(position: LPPosition, gauge_address: str, wallet_address: str = "") -> None:
    """Attach staked metadata to an LPPosition and update its display fields."""
    if not position.raw_data:
        position.raw_data = {}
    position.raw_data["is_staked"] = True
    position.raw_data["gauge_address"] = gauge_address
    wallet = wallet_address or (position.raw_data.get("wallet_address") or "")
    if not wallet:
        wallet = gauge_address
    position.raw_data["owner_display"] = f"{wallet} (staked via gauge)"


# Backwards-compatible alias for callers that previously passed the old callable.
_default_rpc_call = None  # type: ignore
