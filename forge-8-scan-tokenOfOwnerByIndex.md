# Forge Prompt 8 — Fix Scan Wallet: Use tokenOfOwnerByIndex Instead of Brute-Force Scan

**Project:** ColdStack
**File:** `/mnt/b/Blockchain/coldstack/src/venue_adapters/hyperliquid_adapter.py`
**Version:** v5.1 (bug fix — NO rebuild, NO README/STATUS update yet)

**⚠️ Do NOT rebuild the EXE or update README.md/STATUS.md. This is a bug fix still in progress.**

## Root Cause

Scan Wallet does not find LP positions because the scan logic is fundamentally broken:

1. **totalSupply is NOT the max token ID** — totalSupply (446,935) is the *count* of minted tokens, not the highest token ID. Token IDs go above totalSupply (wallet `0xbf0e...` owns token 453,338, which is above totalSupply 446,935).

2. **The scan window (totalSupply - 5000 to totalSupply) misses positions** — token 453,338 is not in the range 441,935–446,935. The scan will never find it.

3. **The adapter docstring says "does not expose tokenOfOwnerByIndex" — this is WRONG.** `tokenOfOwnerByIndex(address, uint256)` (selector `0x2f745c59`) works correctly on the Project X PositionManager. Testing confirmed: `tokenOfOwnerByIndex(0xbf0e..., 0)` returns token ID 453,338.

## Fix

Replace the brute-force `ownerOf()` scan in `fetch_evm_lp_positions()` with `tokenOfOwnerByIndex()` calls. This is instant (1 RPC call per owned token) vs the current 5000+ call scan that never finds the positions.

### In `hyperliquid_adapter.py`, replace the `fetch_evm_lp_positions` method:

```python
    def fetch_evm_lp_positions(
        self,
        wallet_address: str,
        online_mode: bool = False,
        price_engine: Optional[PriceEngine] = None,
    ) -> List[LPPosition]:
        """Fetch all LP positions owned by a wallet via tokenOfOwnerByIndex.

        The Project X PositionManager supports ERC-721 Enumerable, so we can
        call tokenOfOwnerByIndex(address, index) for each index from 0 to
        balanceOf(address)-1. This is instant (1 RPC per owned token) vs
        the old brute-force scan of thousands of ownerOf() calls that missed
        positions with token IDs above totalSupply.
        """
        if not online_mode:
            raise OfflineError("Hyperliquid adapter requires online mode.")

        wallet_address = wallet_address.lower()
        positions: List[LPPosition] = []

        # 1. Get the wallet's NFT balance
        balance_result = _evm_rpc_call(
            "eth_call",
            [
                {
                    "to": POSITION_MANAGER,
                    "data": SELECTOR_BALANCE_OF + _pad_address(wallet_address),
                },
                "latest",
            ],
        )
        balance = 0
        if balance_result and isinstance(balance_result, str):
            try:
                balance = int(balance_result[2:66], 16)
            except (ValueError, IndexError):
                balance = 0

        print(f"[scan] balanceOf={balance} for {wallet_address}")

        if balance == 0:
            return positions

        # 2. Get each token ID via tokenOfOwnerByIndex
        # ERC-721 Enumerable: tokenOfOwnerByIndex(address owner, uint256 index)
        # Selector: 0x2f745c59
        owned_ids: List[int] = []
        for idx in range(balance):
            data = SELECTOR_TOKEN_OF_OWNER_BY_INDEX + _pad_address(wallet_address) + _pad_int_to_64(idx)
            result = _evm_rpc_call(
                "eth_call",
                [{"to": POSITION_MANAGER, "data": data}, "latest"],
            )
            if result and isinstance(result, str) and len(result) >= 66:
                try:
                    token_id = int(result[2:66], 16)
                    owned_ids.append(token_id)
                    print(f"[scan] tokenOfOwnerByIndex({idx}) = {token_id}")
                except (ValueError, IndexError):
                    pass

        print(f"[scan] found {len(owned_ids)} token IDs: {owned_ids}")

        # 3. Fetch each position by token ID
        for token_id in owned_ids:
            pos = self.fetch_evm_position_by_token_id(token_id, price_engine, wallet_address)
            if pos and not pos.error:
                positions.append(pos)

        return positions
```

### Also update the docstring at the top of the method

The old comment said "does not expose tokenOfOwnerByIndex" — this is incorrect. The Project X PositionManager DOES support ERC-721 Enumerable. Update the method docstring (shown above).

### Remove the old brute-force scan code

Remove all the old scan code: the `totalSupply` call, the `scan_start` calculation, the `ownerOf` batch loop, the "freshly minted tokens" probe, the "defensive ownership re-verification" block, and the fallback extension. All of this is replaced by the `tokenOfOwnerByIndex` approach.

### Remove the duplicate `_lp_do_fetch` warning when Platform is selected

In `gui_main_v5.py`, the `_lp_do_fetch_single()` method falls through to `self._lp_do_fetch()` when Position ID is empty. This triggers the "Scan Wallet — 20 minutes" warning even when a Platform is selected. The warning should only appear when Platform is "Auto-detect".

In `_lp_do_fetch_single()`, find:
```python
        if not position_id:
            # No position ID entered — fall through to wallet scan
            self._lp_do_fetch()
            return
```

Replace with:
```python
        if not position_id:
            # No position ID entered — fall through to wallet scan
            # Check if a specific platform is selected
            platform_menu = self._lp_widgets.get("platform_menu")
            selected_venue = None
            if platform_menu:
                val = platform_menu.get()
                if val and val != "Auto-detect":
                    selected_venue = self.LP_PLATFORM_MAP.get(val, val)
            
            if selected_venue:
                # Specific platform selected — do a filtered scan without the warning
                self._lp_do_filtered_scan(wallet_address, selected_venue)
            else:
                # Auto-detect — full scan with warning
                self._lp_do_fetch()
            return
```

And add the new method `_lp_do_filtered_scan`:

```python
    def _lp_do_filtered_scan(self, address: str, venue_key: str):
        """Scan a wallet for positions on a specific venue only (no warning dialog).

        Used when the user selects a specific Platform and clicks Fetch Position
        with an empty Position ID. Skips the "20 minutes" warning since the
        scan is limited to one venue.
        """
        if not self.lp_engine or not self.online_mode:
            self.show_notification("Offline - enable Online Mode in Settings", error=True)
            return

        status = self._lp_widgets.get("status_label")
        refresh_btn = self._lp_widgets.get("refresh_btn")
        scroll = self._lp_widgets.get("scroll")
        if refresh_btn:
            refresh_btn.configure(state="disabled")
        if scroll:
            for widget in scroll.winfo_children():
                widget.destroy()

        # Check saved pools first for fast-path
        saved = load_saved_pools(self.key_manager.address_db, wallet_address=address)
        if saved:
            if status:
                status.configure(text=f"Fetching saved positions on {venue_key}... Full scan will follow.")
            self._lp_render_saved_placeholders(address)
            self._lp_fetch_saved_only(address)
            self.root.after(2000, lambda: self._lp_do_filtered_full_scan(address, venue_key))
        else:
            if status:
                status.configure(text=f"Scanning wallet on {venue_key}...")
            self._lp_do_filtered_full_scan(address, venue_key)

    def _lp_do_filtered_full_scan(self, address: str, venue_key: str):
        """Full scan filtered to a specific venue (threaded)."""
        status = self._lp_widgets.get("status_label")
        if status:
            status.configure(text=f"Fetching positions on {venue_key}...")

        def _fetch_thread():
            try:
                # Use fetch_all_positions with venue_key filter
                positions = self.lp_engine.fetch_all_positions(address, venue_key=venue_key)
                # Merge saved pools for this wallet
                saved = load_saved_pools(self.key_manager.address_db, wallet_address=address)
                if saved:
                    from venue_adapters.hyperliquid_adapter import HyperliquidAdapter
                    adapter = HyperliquidAdapter()
                    for entry in saved:
                        tid = entry.get("token_id")
                        venue = entry.get("venue", "HyperEVM")
                        if tid and venue == "HyperEVM":
                            pid = f"hyperevm:{tid}"
                            if any(p.position_id == pid for p in positions):
                                continue
                            try:
                                pos = adapter.fetch_evm_position_by_token_id(tid, self.price_engine)
                                if pos and not pos.error:
                                    positions.append(pos)
                            except Exception:
                                pass
                self.root.after(0, lambda: self._lp_on_loaded(positions, address))
            except OfflineError:
                self.root.after(0, lambda: self._lp_on_error("Offline mode enabled"))
            except Exception as e:
                self.root.after(0, lambda: self._lp_on_error(str(e)))

        threading.Thread(target=_fetch_thread, daemon=True).start()
```

## Verification

1. `python -m py_compile src/venue_adapters/hyperliquid_adapter.py` — no syntax errors
2. `python -m py_compile src/gui_main_v5.py` — no syntax errors
3. Run the GUI, select account with wallet `0xbf0e7d5868479b3b2602fa929dec6661408edc71`, Platform = "HyperEVM (Project X)", leave Position ID empty, click Fetch Position
   - Should NOT show the "20 minutes" warning dialog
   - Should find token 453,338 (and any other positions) within a few seconds
4. Run the same with wallet `0x8958Bd96896De55bFe31b1A6Eb2B280ebE098509` — should find its 1 position
5. Platform = "Auto-detect", empty Position ID, click Fetch Position — SHOULD show the "20 minutes" warning
6. Do NOT rebuild the EXE yet — more verification needed