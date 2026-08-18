import { Router } from "express";
import fs from "fs";
import path from "path";
import {
    stopRailgunEngine,
    startRailgunEngine,
    loadProvider,
    getProver,
} from "@railgun-community/wallet";
import { groth16 } from "snarkjs";
import { sidecarState, updateState } from "../state.js";
import { createDatabase } from "../db.js";
import { createArtifactStore } from "../artifacts.js";
import { buildProviderConfig, COLDSTACK_TO_RAILGUN } from "../networks.js";
import { setupEngineCallbacks } from "../callbacks.js";

const router = Router();

/**
 * Recursively delete a directory and all its contents.
 * @param {string} dirPath
 */
function removeDirectory(dirPath) {
    if (!fs.existsSync(dirPath)) return;
    console.log(`[cache] Removing directory: ${dirPath}`);
    fs.rmSync(dirPath, { recursive: true, force: true });
}

/**
 * Recursively calculate the total size of a directory in bytes.
 * @param {string} dirPath
 * @returns {number}
 */
function getDirectorySize(dirPath) {
    let totalSize = 0;
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    for (const entry of entries) {
        const fullPath = path.join(dirPath, entry.name);
        if (entry.isDirectory()) {
            totalSize += getDirectorySize(fullPath);
        } else {
            totalSize += fs.statSync(fullPath).size;
        }
    }
    return totalSize;
}

/**
 * Restart the Railgun engine with the current configuration.
 * Reuses the rpcConfig and ppoiNodes stored in sidecarState.
 *
 * @returns {Promise<object>} Result with status and any errors
 */
async function restartEngine() {
    const errors = [];

    const db = createDatabase("./engine.db");
    const artifactStore = createArtifactStore("./artifacts-directory");

    const poiNodeURLs = sidecarState.ppoiNodes.length > 0
        ? sidecarState.ppoiNodes
        : ["https://ppoi.fdi.network"];

    console.log("[cache] Restarting Railgun engine...");
    await startRailgunEngine(
        "coldstack",
        db,
        true,
        artifactStore,
        false,
        false,
        poiNodeURLs,
        [],
        true
    );

    getProver().setSnarkJSGroth16(groth16);

    setupEngineCallbacks();

    if (sidecarState.rpcConfig) {
        for (const [chainName, chainConfig] of Object.entries(sidecarState.rpcConfig)) {
            const networkName = COLDSTACK_TO_RAILGUN[chainName];
            if (!networkName) continue;

            const providerConfig = buildProviderConfig(chainName, chainConfig);
            if (!providerConfig) continue;

            try {
                console.log(`[cache] Reloading provider for ${chainName}...`);
                const pollingInterval = 1000 * 60 * 5;
                const fees = await loadProvider(providerConfig, networkName, pollingInterval);

                sidecarState.loadedProviders.set(chainName, {
                    chainId: providerConfig.chainId,
                    pollingInterval,
                });

                if (fees) {
                    sidecarState.fees.set(chainName, {
                        deposit: fees.deposit,
                        withdraw: fees.withdraw,
                        nft: fees.nft,
                    });
                }
            } catch (err) {
                errors.push(`${chainName}: ${err.message}`);
                console.error(`[cache] Failed to reload provider for ${chainName}: ${err.message}`);
            }
        }
    }

    const walletIds = Array.from(sidecarState.wallets.keys());
    sidecarState.wallets.clear();
    sidecarState.balances.clear();
    sidecarState.scanStatus.clear();

    updateState('engineInitialized', true);

    return {
        engineRestarted: true,
        walletIdsCleared: walletIds,
        providerErrors: errors,
    };
}

// POST /cache/rebuild
// Body: { chain?: string }
router.post('/rebuild', async (req, res) => {
    try {
        if (!sidecarState.engineInitialized) {
            return res.status(400).json({
                status: 'error',
                error: 'Railgun engine not initialized.',
            });
        }

        const { chain } = req.body;
        console.log(`[cache] Full rebuild requested${chain ? ` for chain: ${chain}` : ' (all chains)'}`);

        console.log('[cache] Step 1: Stopping Railgun engine...');
        await stopRailgunEngine();
        updateState('engineInitialized', false);

        const engineDbPath = path.resolve("./engine.db");
        removeDirectory(engineDbPath);

        const artifactsPath = path.resolve("./artifacts-directory");
        removeDirectory(artifactsPath);

        console.log('[cache] Step 4: Restarting engine...');
        const result = await restartEngine();

        console.log('[cache] Full rebuild complete.');
        res.json({
            status: 'ok',
            message: 'Full cache rebuild complete. Engine restarted with fresh database.',
            chain: chain || 'all',
            ...result,
        });

    } catch (err) {
        sidecarState.engineError = err.message;
        console.error('[cache] Rebuild failed:', err.message);
        console.error(err.stack);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// POST /cache/clear
// Body: { chain?: string }
router.post('/clear', async (req, res) => {
    try {
        if (!sidecarState.engineInitialized) {
            return res.status(400).json({
                status: 'error',
                error: 'Railgun engine not initialized.',
            });
        }

        const { chain } = req.body;
        console.log(`[cache] Cache clear requested${chain ? ` for chain: ${chain}` : ' (all chains)'}`);

        console.log('[cache] Step 1: Stopping Railgun engine...');
        await stopRailgunEngine();
        updateState('engineInitialized', false);

        const engineDbPath = path.resolve("./engine.db");
        removeDirectory(engineDbPath);

        console.log('[cache] Step 3: Restarting engine (artifacts preserved)...');
        const result = await restartEngine();

        console.log('[cache] Cache clear complete.');
        res.json({
            status: 'ok',
            message: 'Cache cleared. Engine restarted with fresh database. Artifacts preserved.',
            chain: chain || 'all',
            ...result,
        });

    } catch (err) {
        sidecarState.engineError = err.message;
        console.error('[cache] Clear failed:', err.message);
        console.error(err.stack);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// GET /cache/status
router.get('/status', async (req, res) => {
    const engineDbPath = path.resolve("./engine.db");
    const artifactsPath = path.resolve("./artifacts-directory");

    const engineDbExists = fs.existsSync(engineDbPath);
    const artifactsExist = fs.existsSync(artifactsPath);

    let engineDbSize = 0;
    let artifactsSize = 0;

    try {
        if (engineDbExists) {
            engineDbSize = getDirectorySize(engineDbPath);
        }
    } catch (e) { /* ignore */ }

    try {
        if (artifactsExist) {
            artifactsSize = getDirectorySize(artifactsPath);
        }
    } catch (e) { /* ignore */ }

    res.json({
        status: 'ok',
        engineInitialized: sidecarState.engineInitialized,
        engineDb: {
            exists: engineDbExists,
            sizeBytes: engineDbSize,
            sizeMB: (engineDbSize / (1024 * 1024)).toFixed(2),
        },
        artifacts: {
            exists: artifactsExist,
            sizeBytes: artifactsSize,
            sizeMB: (artifactsSize / (1024 * 1024)).toFixed(2),
        },
        walletsLoaded: sidecarState.wallets.size,
    });
});

export default router;
