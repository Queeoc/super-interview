import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
var DEFAULT_BACKEND_ORIGIN = 'http://127.0.0.1:9900';
export default defineConfig(function (_a) {
    var _b;
    var mode = _a.mode;
    var env = loadEnv(mode, process.cwd(), '');
    var directApiBaseUrl = (_b = env.VITE_API_BASE_URL) === null || _b === void 0 ? void 0 : _b.trim();
    var useProxy = !directApiBaseUrl;
    return {
        plugins: [react()],
        server: useProxy
            ? {
                proxy: {
                    '/api': {
                        target: DEFAULT_BACKEND_ORIGIN,
                        changeOrigin: true,
                        secure: false
                    }
                }
            }
            : undefined,
        test: {
            environment: 'jsdom',
            setupFiles: ['./src/test/setup.ts'],
            css: true,
            globals: true
        }
    };
});
