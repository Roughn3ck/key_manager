import { ArtifactStore } from "@railgun-community/wallet";
import fs from "fs";
import path from "path";

/**
 * Creates an ArtifactStore for the Railgun engine.
 * The engine downloads large proof artifact files (50MB+) and stores them
 * on disk for subsequent use. This module provides the file I/O interface.
 *
 * @param {string} artifactsDir - Directory for artifact storage. Defaults to ./artifacts-directory
 * @returns {ArtifactStore} Configured ArtifactStore instance
 */
export function createArtifactStore(artifactsDir) {
    const baseDir = path.resolve(artifactsDir || "./artifacts-directory");

    const resolvePath = (subPath) => path.join(baseDir, subPath);

    const getFile = async (filePath) => {
        return fs.promises.readFile(resolvePath(filePath));
    };

    const storeFile = async (dir, filePath, item) => {
        const fullDir = resolvePath(dir);
        await fs.promises.mkdir(fullDir, { recursive: true });
        await fs.promises.writeFile(resolvePath(filePath), item);
    };

    const fileExists = (filePath) => {
        return new Promise((resolve) => {
            fs.promises
                .access(resolvePath(filePath))
                .then(() => resolve(true))
                .catch(() => resolve(false));
        });
    };

    console.log(`[artifacts] Artifact store directory: ${baseDir}`);
    return new ArtifactStore(getFile, storeFile, fileExists);
}
