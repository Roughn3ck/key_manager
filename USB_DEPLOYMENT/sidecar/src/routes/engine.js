import { Router } from "express";
import {
    startRailgunEngine,
    stopRailgunEngine,
    loadProvider,
    getProver,
} from "@railgun-community/wallet";
import { groth16 } from "snarkjs";
import { sidecarState, updateState } from "../state.js";
import { createDatabase } from "../db.js";
import { createArtifactStore } from "../artifacts.js";
import { buildProviderConfig, COLDSTACK_TO_RAILGUN, SUPPORTED_RAILGUN_NETWORKS } from "../networks.js";
import { setupEngineCallbacks } from "../callbacks.js";

const router = Router();

// POST /engine/init
// Body: { rpcConfig: { chainName: { url, auth?, fallback? } }, ppoiNodes?: string[] }
router.post('/init', async (req, res) => {
    try {
        const { rpcConfig, ppoiNodes } = req.body;

        if (sidecarState.engineInitialized) {
            return res.json({
                status: 'already_initialized',
                message: 'Railgun engine is already running. Call /engine/shutdown first to reinitialize.',
            });
        }

        if (sidecarState.engineStarting) {
            return res.status(409).json({
                status: 'error',
                error: 'Engine initialization is already in progress',
            });
        }

        sidecarState.engineStarting = true;
        sidecarState.engineError = null;

        const db = createDatabase("./engine.db");
        const artifactStore = createArtifactStore("./artifacts-directory");

        const poiNodeURLs = ppoiNodes && ppoiNodes.length > 0
            ? ppoiNodes
            : ["https://ppoi.fdi.network"];

        console.log("[engine] Starting Railgun engine...");
        const walletSource = "coldstack";
        const shouldDebug = true;
        const useNativeArtifacts = false;
        const skipMerkletreeScans = false;
        const customPOILists = [];
        const verboseScanLogging = true;

        await startRailgunEngine(
            walletSource,
            db,
            shouldDebug,
            artifactStore,
            useNativeArtifacts,
            skipMerkletreeScans,
            poiNodeURLs,
            customPOILists,
            verboseScanLogging
        );

        console.log("[engine] Setting up Groth16 prover (snarkjs)...");
        getProver().setSnarkJSGroth16(groth16);

        setupEngineCallbacks();

        const providerResults = {};
        if (rpcConfig && typeof rpcConfig === 'object') {
            for (const [chainName, chainConfig] of Object.entries(rpcConfig)) {
                const networkName = COLDSTACK_TO_RAILGUN[chainName];
                if (!networkName) {
                    continue;
                }

                const providerConfig = buildProviderConfig(chainName, chainConfig);
                if (!providerConfig) {
                    continue;
                }

                try {
                    console.log(`[engine] Loading provider for ${chainName} (${networkName})...`);
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

                    providerResults[chainName] = { status: 'ok', fees: fees || null };
                    console.log(`[engine] Provider loaded for ${chainName}: fees=${JSON.stringify(fees)}`);
                } catch (err) {
                    sidecarState.providerErrors.set(chainName, err.message);
                    providerResults[chainName] = { status: 'error', error: err.message };
                    console.error(`[engine] Failed to load provider for ${chainName}: ${err.message}`);
                }
            }
        }

        updateState('engineInitialized', true);
        updateState('engineStarting', false);
        updateState('rpcConfig', rpcConfig || null);
        updateState('ppoiNodes', poiNodeURLs);

        const gracefulShutdown = async (signal) => {
            console.log(`[engine] ${signal} received, stopping Railgun engine...`);
            try {
                await stopRailgunEngine();
                console.log("[engine] Railgun engine stopped.");
            } catch (err) {
                console.error(`[engine] Error during shutdown: ${err.message}`);
            }
            process.exit(0);
        };

        process.removeAllListeners('SIGTERM');
        process.removeAllListeners('SIGINT');
        process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
        process.on('SIGINT', () => gracefulShutdown('SIGINT'));

        console.log("[engine] Railgun engine initialized successfully.");

        res.json({
            status: 'ok',
            message: 'Railgun engine initialized',
            providers: providerResults,
            ppoiNodes: poiNodeURLs,
            supportedNetworks: SUPPORTED_RAILGUN_NETWORKS.map(n => n.toString()),
        });

    } catch (err) {
        sidecarState.engineStarting = false;
        sidecarState.engineError = err.message;
        console.error("[engine] Init failed:", err.message);
        console.error(err.stack);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// POST /engine/shutdown
router.post('/shutdown', async (req, res) => {
    try {
        if (!sidecarState.engineInitialized) {
            return res.json({
                status: 'not_initialized',
                message: 'Railgun engine is not running.',
            });
        }

        console.log("[engine] Stopping Railgun engine...");
        await stopRailgunEngine();

        updateState('engineInitialized', false);
        sidecarState.wallets.clear();
        sidecarState.balances.clear();
        sidecarState.scanStatus.clear();
        sidecarState.loadedProviders.clear();
        sidecarState.fees.clear();

        console.log("[engine] Railgun engine stopped.");

        res.json({
            status: 'ok',
            message: 'Railgun engine stopped.',
        });
    } catch (err) {
        console.error("[engine] Shutdown failed:", err.message);
        res.status(500).json({
            status: 'error',
            error: err.message,
        });
    }
});

// GET /engine/status
router.get('/status', async (req, res) => {
    const providers = {};
    for (const [chain, info] of sidecarState.loadedProviders.entries()) {
        providers[chain] = info;
        const fees = sidecarState.fees.get(chain);
        if (fees) {
            providers[chain].fees = fees;
        }
        const err = sidecarState.providerErrors.get(chain);
        if (err) {
            providers[chain].error = err;
        }
    }

    res.json({
        status: sidecarState.engineInitialized ? 'running' : (sidecarState.engineStarting ? 'starting' : 'stopped'),
        engineInitialized: sidecarState.engineInitialized,
        engineError: sidecarState.engineError,
        walletsLoaded: sidecarState.wallets.size,
        providers,
        ppoiNodes: sidecarState.ppoiNodes,
    });
});

export default router;
