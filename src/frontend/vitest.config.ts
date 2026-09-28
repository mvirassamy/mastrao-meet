import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  resolve: {
    alias: {
      '@': path.join(root, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: [
      'src/features/{rooms,home,meetingHistory,settings,subtitle}/**/*.test.{ts,tsx}',
      'src/primitives/**/*.test.{ts,tsx}',
      'src/i18n/**/*.test.{ts,tsx}',
    ],
  },
})
