import {defineConfig} from 'vitest/config'; import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{proxy:{'/api':'http://backend:8000'}},test:{environment:'jsdom',setupFiles:'./src/test-setup.ts'}});
