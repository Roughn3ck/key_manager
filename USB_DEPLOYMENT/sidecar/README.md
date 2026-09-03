# ColdStack Railgun Sidecar

Node.js sidecar process for ColdStack that runs the Railgun privacy SDK.

## Development

```bash
cd sidecar
npm install
npm start
```

## Architecture

The sidecar is an Express HTTP server on localhost:8765 that wraps the
@railgun-community/wallet SDK. ColdStack's Python GUI communicates with
it via HTTP JSON requests.

See `B:\Blockchain\coldstack\AGENTS.md` for project conventions.
