const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser', workers: 1, retries: 0,
  use: { baseURL: 'http://127.0.0.1:8878', browserName: 'chromium', screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  webServer: { command: '.venv/bin/python scripts/dev-test-server.py', url: 'http://127.0.0.1:8878/api/status', reuseExistingServer: false, timeout: 30000 },
  reporter: [['list']],
});
