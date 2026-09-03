import LevelDown from "leveldown";
import path from "path";
import fs from "fs";

/**
 * Creates a LevelDOWN-compatible database for the Railgun engine.
 * The database stores encrypted wallet data.
 *
 * @param {string} dbPath - Path for the database directory. Defaults to ./engine.db
 * @returns {LevelDown} LevelDOWN database instance
 */
export function createDatabase(dbPath) {
    const resolvedPath = path.resolve(dbPath || "./engine.db");

    const parentDir = path.dirname(resolvedPath);
    if (!fs.existsSync(parentDir)) {
        fs.mkdirSync(parentDir, { recursive: true });
    }

    console.log(`[db] Creating engine database at: ${resolvedPath}`);
    return new LevelDown(resolvedPath);
}
