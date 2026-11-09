# OPENLAB desktop shell

The desktop shell is a Tauri 2 application. Building it requires:

- the Rust toolchain (1.77+),
- Node 18+,
- the OPENLAB backend installed (`pip install -r requirements-dev.txt`).

## Development

```bash
cd apps/desktop
npm install
npm run tauri dev
```

The dev command runs the web dev server, waits for it, starts the backend with
`python -m openlab --port 3210` and opens the window.

## Building installers

```bash
cd apps/desktop
npm install
npm run tauri build
```

Produces MSI/NSIS on Windows and DMG on macOS under
`apps/desktop/src-tauri/target/release/bundle/`.

## Backend sidecar

For a fully standalone installer, build the backend with PyInstaller first:

```bash
pyinstaller --name openlab-backend --onefile apps/backend/openlab/__main__.py
cp dist/openlab-backend apps/desktop/src-tauri/resources/backend/
npm run tauri build
```

The shell looks for `resources/backend/openlab-backend[.exe]` before falling back to the
system Python module. This keeps the development loop (no frozen binary) working on any machine.
