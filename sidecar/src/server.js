import express from 'express';
import cors from 'cors';
import HealthRouter from './routes/health.js';
import EngineRouter from './routes/engine.js';
import WalletRouter from './routes/wallet.js';
import BalanceRouter from './routes/balances.js';
import TransferRouter from './routes/transfer.js';
import PoiRouter from './routes/poi.js';
import CacheRouter from './routes/cache.js';

const app = express();
const PORT = parseInt(process.env.SIDECAR_PORT || '8765', 10);

app.use(cors());
app.use(express.json({ limit: '10mb' }));

// Request logging middleware
app.use((req, res, next) => {
    console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
    next();
});

// Route registration
app.use('/health', HealthRouter);
app.use('/engine', EngineRouter);
app.use('/wallet', WalletRouter);
app.use('/balances', BalanceRouter);
app.use('/transfer', TransferRouter);
app.use('/poi', PoiRouter);
app.use('/cache', CacheRouter);

// Error handler
app.use((err, req, res, next) => {
    console.error(`[ERROR] ${err.message}`);
    console.error(err.stack);
    res.status(500).json({ error: err.message });
});

const server = app.listen(PORT, '127.0.0.1', () => {
    console.log(`ColdStack Railgun Sidecar listening on http://127.0.0.1:${PORT}`);
});

// Graceful shutdown
process.on('SIGTERM', () => {
    console.log('[SIDECAR] SIGTERM received, shutting down...');
    server.close(() => {
        console.log('[SIDECAR] Server closed.');
        process.exit(0);
    });
});

process.on('SIGINT', () => {
    console.log('[SIDECAR] SIGINT received, shutting down...');
    server.close(() => {
        console.log('[SIDECAR] Server closed.');
        process.exit(0);
    });
});
