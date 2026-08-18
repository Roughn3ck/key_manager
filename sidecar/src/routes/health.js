import { Router } from "express";
import { sidecarState } from "../state.js";

const router = Router();

// GET /health
router.get('/', (req, res) => {
    res.json({
        status: 'ok',
        version: '5.2.3',
        uptime: process.uptime(),
        engineInitialized: sidecarState.engineInitialized,
        engineStarting: sidecarState.engineStarting,
        walletsLoaded: sidecarState.wallets.size,
        providersLoaded: sidecarState.loadedProviders.size,
        timestamp: new Date().toISOString()
    });
});

export default router;
