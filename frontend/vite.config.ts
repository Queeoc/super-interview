import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

const DEFAULT_BACKEND_ORIGIN = 'http://127.0.0.1:9900';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const directApiBaseUrl = env.VITE_API_BASE_URL?.trim();
  const useProxy = !directApiBaseUrl;

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
