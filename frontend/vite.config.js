import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
export default defineConfig({
    plugins: [react(), tailwindcss()],
    resolve: {
        alias: {
            "@": fileURLToPath(new URL("./src", import.meta.url)),
        },
    },
    server: {
        port: 5173,
        // ngrok tunnels for remote access. Vite 6 blocks requests whose Host header is not listed
        // here (localhost and 127.0.0.1 are always allowed), so the tunnel hostnames must be added.
        allowedHosts: [
            "faceted-proofing-occultist.ngrok-free.dev",
            "wasp-drastic-nursery.ngrok-free.dev",
        ],
    },
});
