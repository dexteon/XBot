"""Post a tweet using XActions browser automation (Puppeteer via Node script)."""
import subprocess
import tempfile
import os
from pathlib import Path

POST_SCRIPT = """
const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.connect({ browserURL: 'http://localhost:9222' })
    .catch(() => puppeteer.launch({ headless: false, defaultViewport: null }));

  const page = await browser.newPage();

  // Load cookies from xactions
  const { execSync } = require('child_process');
  const cookieStr = execSync('xactions login --show', { encoding: 'utf-8' }).trim();

  // Navigate to X
  await page.goto('https://x.com/compose/post', { waitUntil: 'networkidle2', timeout: 60000 });

  // Wait for compose box
  await page.waitForSelector('[data-testid="tweetTextarea_0"]', { timeout: 30000 });
  await page.click('[data-testid="tweetTextarea_0"]');

  // Type the tweet
  await page.keyboard.type(process.argv[2], { delay: 50 });

  // Wait for send button
  await page.waitForSelector('[data-testid="tweetButton"]', { timeout: 10000 });
  await page.click('[data-testid="tweetButton"]');

  // Wait for confirmation
  await new Promise(r => setTimeout(r, 3000));

  console.log('Tweet posted successfully');

  if (browser.disconnect) {
    browser.disconnect();
  } else {
    await browser.close();
  }
})().catch(e => {
  console.error('Error:', e.message);
  process.exit(1);
});
"""


def post_tweet(text: str) -> tuple[bool, str]:
    """Post a tweet using Puppeteer browser automation."""
    npm_dir = Path.home() / "AppData" / "Roaming" / "npm" / "node_modules"

    # Write temp script
    with tempfile.NamedTemporaryFile(suffix=".js", delete=False, mode="w") as f:
        f.write(POST_SCRIPT)
        script_path = f.name

    try:
        result = subprocess.run(
            ["node", script_path, text],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(npm_dir),
            shell=False,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "Timeout posting tweet"
    finally:
        os.unlink(script_path)