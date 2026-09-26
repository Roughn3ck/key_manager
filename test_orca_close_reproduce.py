"""Round-2 diagnostic: reproduce closePosition 6005 with FULL anchor logs and a
complete field-by-field hexdump of the live position, parsed with the
authoritative 2.x layout (state/position.rs). Read-only, no broadcast, no keys.

Run:  python test_orca_close_reproduce.py
"""
import base64, json, os, struct, sys, time, urllib.error, urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent / "src"))

from venue_adapters import orca_writer as ow
from venue_adapters.orca_adapter import _b58decode, _b58encode, _derive_position_address

POSITION_MINT = "FbNHxe9VV797JWG7XH2msjwp5Rvb6dGzndwwkEXXXBKX"
WHIRLPOOL = "CeaZcxBNLpJWtxzt58qQmfMBtJY8pQLvursXTJYGQpbN"
TOKEN_2022 = ow.TOKEN_2022_PROGRAM_ID
CLASSIC = ow.SPL_TOKEN_PROGRAM_ID

RPCS = ["https://api.mainnet-beta.solana.com", "https://solana-rpc.publicnode.com"]
_UA = {"Content-Type": "application/json", "User-Agent": "ColdStack/5.3.14"}
_state = {"idx": 0, "last": 0.0}


def rpc(method, params, tries=16):
    for _ in range(tries):
        dt = time.time() - _state["last"]
        if dt < 4.0:
            time.sleep(4.0 - dt)
        url = RPCS[_state["idx"] % len(RPCS)]; _state["idx"] += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        try:
            req = urllib.request.Request(url, data=payload, headers=_UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                body = json.loads(r.read().decode())
            _state["last"] = time.time()
        except urllib.error.HTTPError as e:
            _state["last"] = time.time()
            if e.code in (403, 410, 429, 503):
                time.sleep(8 if e.code == 429 else 4); continue
            raise
        except (urllib.error.URLError, OSError, ValueError):
            _state["last"] = time.time(); time.sleep(3); continue
        if isinstance(body, dict) and body.get("error"):
            code = body["error"].get("code") if isinstance(body["error"], dict) else None
            if code in (429, -32429):
                time.sleep(8); continue
            raise RuntimeError(f"{method}: {body['error']}")
        return body
    raise RuntimeError(f"{method}: exhausted")


def get_account_bytes(addr):
    b = rpc("getAccountInfo", [addr, {"encoding": "base64"}])
    v = (b.get("result") or {}).get("value")
    if not v:
        return None
    return base64.b64decode(v["data"][0])


def hexdump_parse(data):
    print("position account bytes:", len(data))
    print("hex:", data.hex())
    off = 0
    def g(n, fmt=None):
        nonlocal off
        raw = data[off:off+n]
        v = struct.unpack_from(fmt, raw)[0] if fmt else None
        print(f"  @{off:3}..{off+n:3}  {raw.hex():<{n*2}}  {v if v is not None else ''}")
        off += n
        return raw
    g(8); wh = _b58encode(g(32)); pm = _b58encode(g(32))
    liq = struct.unpack_from("<QQ", g(16)); liq = liq[0] + (liq[1] << 64)
    tl = struct.unpack_from("<i", g(4))[0]; tu = struct.unpack_from("<i", g(4))[0]
    g(16); fa = struct.unpack_from("<Q", g(8))[0]
    g(16); fb = struct.unpack_from("<Q", g(8))[0]
    rewards = []
    for i in range(3):
        gg = int.from_bytes(g(16), "little"); owed = struct.unpack_from("<Q", g(8))[0]
        rewards.append((gg, owed))
    print("\nPARSED:")
    print("  whirlpool:", wh, "\n  position_mint:", pm)
    print("  liquidity:", liq, " ticks:", tl, tu)
    print("  fee_owed_a:", fa, " fee_owed_b:", fb)
    for i, (gg, owed) in enumerate(rewards):
        print(f"  reward[{i}] growth_inside={gg} amount_owed={owed}")
    empty = liq == 0 and fa == 0 and fb == 0 and all(o == 0 for _, o in rewards)
    print("  is_position_empty (2.x) =", empty)
    return dict(liquidity=liq, fee_a=fa, fee_b=fb, rewards=rewards, empty=empty)


def main():
    if not (os.environ.get("COLDSATCK_E2E_RPC") or os.environ.get("COLDSTACK_E2E_RPC")):
        print("⏭️  SKIP test_orca_close_reproduce — live Solana RPC e2e. "
              "Set COLDSATCK_E2E_RPC=1 to run.")
        return 0
    pos_addr = _derive_position_address(POSITION_MINT)
    print("resolved position PDA:", pos_addr)
    assert pos_addr == "F98SmNgmft21dRAwfXGPtWu95Kb1WcSm58WgaQzUZQQR", pos_addr

    data = get_account_bytes(pos_addr)
    parsed = hexdump_parse(data)

    # Owner discovery (Token-2022 GPA, mint filter offset 0)
    body = rpc("getProgramAccounts", [TOKEN_2022, {
        "encoding": "jsonParsed",
        "filters": [{"memcmp": {"offset": 0, "bytes": POSITION_MINT}}]}])
    vals = body.get("result") or []
    owner = owner_ata = None
    for e in vals:
        info = e.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        if info.get("mint") == POSITION_MINT and info.get("tokenAmount", {}).get("amount") == "1":
            owner, owner_ata = info.get("owner"), e.get("pubkey")
    print("\nNFT owner:", owner, "ATA:", owner_ata)

    # Build the close ix exactly as the writer does (Token-2022), full-log sim.
    w = ow.OrcaWriter.__new__(ow.OrcaWriter)
    w._token_program_cache = {POSITION_MINT: TOKEN_2022}
    w._get_position_token_account = lambda a, b: owner_ata
    w._get_token_program = lambda m: TOKEN_2022
    pos = {"position_address": pos_addr, "position_mint": POSITION_MINT, "whirlpool": WHIRLPOOL}
    prog, accts, disc = ow.OrcaWriter._build_close_position_ix(w, owner, pos)
    print("\nbuilder accounts (close TE):")
    for i, a in enumerate(accts):
        print(f"  [{i}] {_b58encode(a.pubkey if isinstance(a.pubkey,(bytes,bytearray)) else a.pubkey)}"
              f" sig={a.is_signer} wr={a.is_writable}")
    print("discriminator:", disc.hex(), "(expect 01b6873b9b1963df)")

    from test_orca_close_simulate import build_message  # reuse canonical assembler
    hb = _b58decode(rpc("getLatestBlockhash", [{"commitment": "finalized"}])["result"]["value"]["blockhash"])
    tx = build_message(prog, accts, disc, _b58decode(owner), hb)
    body = rpc("simulateTransaction", [tx, {"sigVerify": False, "encoding": "base64", "commitment": "confirmed"}])
    val = body.get("result", {}).get("value", {})
    print("\n=== FULL SIMULATION ===")
    print("err:", val.get("err"))
    print("unitsConsumed:", val.get("unitsConsumed"))
    for ln in val.get("logs") or []:
        print("  LOG:", ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
