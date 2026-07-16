"""Browser actions — like, retweet, post via XActions BrowserDriver (Puppeteer).

Replaces CLI commands that don't exist in XActions v3.0.0.
Uses headless browser with auth cookie from ~/.xactions/config.json.
"""

import subprocess
import json
import logging
from pathlib import Path

log = logging.getLogger("xbot.browser")

NPM_DIR = Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "xactions"
SCRIPT_FILE = NPM_DIR / "xbot_action.mjs"


def _ensure_script():
    """Write the Node script to the xactions dir if not present."""
    if SCRIPT_FILE.exists():
        return  # already written
    script = '''import { BrowserDriver } from './src/agents/browserDriver.js';
import { readFileSync } from 'fs';
import { homedir } from 'os';
import path from 'path';

const configPath = path.join(homedir(), '.xactions', 'config.json');
const config = JSON.parse(readFileSync(configPath, 'utf-8'));

const driver = new BrowserDriver({ headless: true });
await driver.launch();
await driver.page.setCookie({
  name: 'auth_token',
  value: config.authToken,
  domain: '.x.com',
  path: '/',
  httpOnly: true,
  secure: true,
  sameSite: 'None',
});

const action = process.argv[2];
const tweetUrl = process.argv[3];
const tweetText = process.argv[4] || '';

try {
  if (action === 'like') {
    await driver.navigate(tweetUrl);
    await new Promise(r => setTimeout(r, 4000));
    const btn = await driver.page.$('[data-testid="like"]');
    if (btn) { await btn.click(); await new Promise(r => setTimeout(r, 2000)); console.log('LIKED'); }
    else console.log('NO_LIKE_BTN');
  }
  else if (action === 'retweet') {
    await driver.navigate(tweetUrl);
    await new Promise(r => setTimeout(r, 4000));
    const btn = await driver.page.$('[data-testid="retweet"]');
    if (btn) {
      await btn.click();
      await new Promise(r => setTimeout(r, 2000));
      const confirm = await driver.page.waitForSelector('[data-testid="retweetConfirm"]', { timeout: 5000 }).catch(() => null);
      if (confirm) { await confirm.click(); await new Promise(r => setTimeout(r, 2000)); console.log('RETWEETED'); }
      else {
        const item = await driver.page.evaluateHandle(() => {
          const els = document.querySelectorAll('[role="menuitem"]');
          for (const e of els) { if (e.textContent.includes('Repost')) return e; }
          return null;
        });
        if (item) { await item.click().catch(() => {}); await new Promise(r => setTimeout(r, 2000)); console.log('RETWEETED'); }
        else console.log('NO_CONFIRM');
      }
    } else console.log('NO_RETWEET_BTN');
  }
  else if (action === 'post') {
    await driver.navigate('https://x.com/compose/post');
    await new Promise(r => setTimeout(r, 3000));
    await driver.page.waitForSelector('[data-testid="tweetTextarea_0"]', { timeout: 15000 });
    await driver.antiDetection.humanType(driver.page, '[data-testid="tweetTextarea_0"]', tweetText);
    await new Promise(r => setTimeout(r, 1000));
    await driver.page.waitForSelector('[data-testid="tweetButton"]', { timeout: 10000 });
    await driver.page.click('[data-testid="tweetButton"]');
    await new Promise(r => setTimeout(r, 3000));
    console.log('POSTED');
  }
} catch(e) {
  console.log('ERROR:' + e.message);
}
await driver.browser.close();
'''
    SCRIPT_FILE.write_text(script, encoding="utf-8")


def _run_browser_action(action: str, tweet_url: str = "", text: str = "") -> tuple[bool, str]:
    """Run a browser action via Node script."""
    _ensure_script()

    try:
        result = subprocess.run(
            ["node", str(SCRIPT_FILE), action, tweet_url, text],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(NPM_DIR),
        )
        output = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if output:
            log.debug(f"browser action {action}: {output}")
        if stderr:
            log.debug(f"browser action {action} stderr: {stderr[:200]}")
        if "LIKED" in output:
            return True, "liked"
        elif "RETWEETED" in output:
            return True, "retweeted"
        elif "POSTED" in output:
            return True, "posted"
        elif "NO_" in output:
            return False, output
        elif "ERROR:" in output:
            return False, output
        return False, f"unexpected: {output or stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "node not found"


def like_tweet(tweet_url: str) -> tuple[bool, str]:
    """Like a tweet by URL. Returns (success, message)."""
    return _run_browser_action("like", tweet_url)


def retweet_tweet(tweet_url: str) -> tuple[bool, str]:
    """Retweet a tweet by URL. Returns (success, message)."""
    return _run_browser_action("retweet", tweet_url)


def post_tweet(text: str) -> tuple[bool, str]:
    """Post a new tweet. Returns (success, message)."""
    return _run_browser_action("post", "", text)