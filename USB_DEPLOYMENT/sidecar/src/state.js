// Shared state across route handlers
// Holds Railgun engine state, wallet references, provider configs, and scan progress

export const sidecarState = {
    // Engine state
    engineInitialized: false,
    engineStarting: false,
    engineError: null,

    // Railgun SDK objects (not directly referenced — SDK manages internally)
    // We track state via SDK callbacks

    // Wallet tracking
    wallets: new Map(),      // walletId -> { id, railgunAddress, publicKey, mnemonicHash }

    // Balance tracking (populated by onBalanceUpdateCallback)
    balances: new Map(),     // `${walletId}:${chain}:${bucket}` -> { tokenAddress, amount, symbol }

    // Scan status (populated by onMerkletreeScanCallback)
    scanStatus: new Map(),   // `${chain}` -> { progress, status, utxoProgress, txidProgress }

    // POI status
    poiStatus: new Map(),    // `${walletId}:${chain}` -> { status, listsChecked }

    // Network provider state
    loadedProviders: new Map(),  // chain -> { chainId, fees, pollingInterval }
    providerErrors: new Map(),   // chain -> error message (if provider load failed)

    // Engine config (set during init)
    rpcConfig: null,         // Raw RPC config from ColdStack
    ppoiNodes: [],           // POI node URLs

    // Railgun fees per chain (from loadProvider)
    fees: new Map(),         // chain -> { deposit, withdraw, nft }
};

export function updateState(key, value) {
    if (key in sidecarState) {
        sidecarState[key] = value;
    }
}

// Helper to set nested balance entries
export function setBalance(walletId, chain, bucket, balances) {
    const key = `${walletId}:${chain}:${bucket}`;
    sidecarState.balances.set(key, balances);
}

// Helper to set scan status
export function setScanStatus(chain, field, value) {
    let status = sidecarState.scanStatus.get(chain);
    if (!status) {
        status = { progress: 0, status: 'idle', utxoProgress: 0, txidProgress: 0 };
    }
    status[field] = value;
    sidecarState.scanStatus.set(chain, status);
}

// Helper to register a loaded wallet
export function registerWallet(walletInfo) {
    sidecarState.wallets.set(walletInfo.id, {
        id: walletInfo.id,
        railgunAddress: walletInfo.railgunAddress,
        publicKey: walletInfo.publicKey,
    });
}
